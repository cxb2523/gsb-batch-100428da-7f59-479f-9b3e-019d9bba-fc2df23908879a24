import os
import posixpath
import hashlib
import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse, unquote
from typing import Dict, Iterable, List, Optional, Tuple

import aiohttp
import aiofiles
import aiofiles.os  # Klasör kontrolleri için asenkron I/O

from .config import CONTENT_TYPE_MAP

logger = logging.getLogger(__name__)


class DownloadStatus(str, Enum):
    """Bir kaynağın indirme sonucunun olası durumları."""

    SUCCESS = "success"        # İndirildi ve diske yazıldı
    SKIPPED = "skipped"        # Dosya zaten vardı, yeniden indirilmedi
    HTTP_ERROR = "http_error"  # Sunucu 200 dışında bir durum kodu döndürdü
    TIMEOUT = "timeout"        # İndirme zaman aşımına uğradı
    ERROR = "error"            # Diğer beklenmeyen hatalar


@dataclass
class DownloadResult:
    """Tek bir URL'nin indirme sonucu.

    ``local_path`` sadece SUCCESS ve SKIPPED durumlarında doludur ve her zaman
    gerçekten diske yazılmış (veya zaten var olan) dosyanın, ``output_dir``
    içindeki göreli yolunu (``/`` ayraçlı) gösterir. Diğer durumlarda ``None``
    olur; böylece çağıran taraf olmayan bir dosyaya yönlendirme yapmaz.
    """

    url: str
    status: DownloadStatus
    local_path: Optional[str] = None
    detail: Optional[str] = None

    @property
    def ok(self) -> bool:
        """Kaynak çevrimdışı sayfada kullanılabilir mi (indirildi veya zaten vardı)?"""
        return self.status in (DownloadStatus.SUCCESS, DownloadStatus.SKIPPED)

    @property
    def failed(self) -> bool:
        return not self.ok

    def __bool__(self) -> bool:
        return self.ok


def format_summary(results: Iterable[DownloadResult]) -> str:
    """İndirme sonuçlarını insan tarafından okunabilir bir özete çevirir."""
    results = list(results)
    success = sum(1 for r in results if r.status is DownloadStatus.SUCCESS)
    skipped = sum(1 for r in results if r.status is DownloadStatus.SKIPPED)
    failures = [r for r in results if not r.ok]

    lines = [
        "📊 Download Summary",
        f"  Success: {success}",
        f"  Skipped: {skipped}",
        f"  Failed:  {len(failures)}",
        f"  Total:   {len(results)}",
    ]

    if failures:
        lines.append("  Failed resources:")
        for result in failures:
            reason = result.detail or result.status.value
            lines.append(f"   - {result.url} - {reason}")

    return "\n".join(lines)


class AsyncDownloader:
    def __init__(self, output_dir: str, timeout: int = 60, max_concurrent: int = 20):
        self.output_dir: str = output_dir
        self.timeout: aiohttp.ClientTimeout = aiohttp.ClientTimeout(total=timeout)
        self.semaphore: asyncio.Semaphore = asyncio.Semaphore(max_concurrent)
        self.session: Optional[aiohttp.ClientSession] = None

        # Aynı çalıştırmada aynı yerel yolu hedefleyen farklı URL'leri izler.
        # Anahtar: normalize edilmiş mutlak yol, değer: o yolu talep eden URL.
        self._claimed_paths: Dict[str, str] = {}
        self._claim_lock: asyncio.Lock = asyncio.Lock()

    async def __aenter__(self):
        connector = aiohttp.TCPConnector(limit=0)
        self.session = aiohttp.ClientSession(connector=connector, timeout=self.timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    # Orijinal kodundaki bu metodu KESİNLİKLE tutmalısın
    def _guess_extension(self, content_type: Optional[str], fallback: str = "bin") -> str:
        if not content_type:
            return fallback
        for ctype, ext in CONTENT_TYPE_MAP.items():
            if content_type.startswith(ctype):
                return ext
        return fallback

    # ------------------------------------------------------------------
    # Yerel yol üretimi ve çakışma çözümü
    # ------------------------------------------------------------------
    @staticmethod
    def _derive_relative_path(url: str, folder: str) -> str:
        """URL yolundan, kategori klasörü altındaki göreli yolu üretir.

        Yol her zaman ``/`` ayraçlı döner; disk üzerinde ``os.path.join`` ile
        birleştirilir. Böylece HTML'e yazılan yol ile diske yazılan yol aynı
        dosyayı gösterir.
        """
        parsed = urlparse(url)
        raw_path = unquote(parsed.path.lstrip("/")) or "index.html"
        if raw_path.endswith("/"):
            raw_path += "index.html"
        # `..`, ters bölü ve sürücü ayracı gibi segmentleri temizleyerek URL
        # yolunun output_dir dışına taşmasını engelle.
        segments = [
            s for s in raw_path.split("/")
            if s not in ("", ".", "..")
        ]
        segments = [s.replace("\\", "_").replace(":", "_") for s in segments]
        if not segments:
            segments = ["index.html"]
        return "/".join([folder, *segments])

    def _absolute_path(self, rel_path: str) -> str:
        return os.path.join(self.output_dir, *rel_path.split("/"))

    def _path_key(self, rel_path: str) -> str:
        return os.path.normcase(os.path.abspath(self._absolute_path(rel_path)))

    @staticmethod
    def _alternative_name(rel_path: str, url: str, attempt: int) -> str:
        """Çakışan yol için URL'ye özgü, kısa hash'li bir ad üretir."""
        dirname, filename = posixpath.split(rel_path)
        stem, ext = posixpath.splitext(filename)
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
        if attempt == 0:
            name = f"{stem}-{digest}{ext}"
        else:
            name = f"{stem}-{digest}-{attempt}{ext}"
        return posixpath.join(dirname, name)

    async def _reserve_path(self, rel_path: str, url: str) -> str:
        """Bir yerel göreli yolu bu URL için ayırır.

        Yol başka bir URL tarafından kapılmışsa, bu (sonra gelen) URL için
        çakışmayan hash'li bir ad üretilir. Aynı URL'nin tekrar talebi kendi
        yolunu geri alır.
        """
        async with self._claim_lock:
            key = self._path_key(rel_path)
            owner = self._claimed_paths.get(key)
            if owner is None or owner == url:
                self._claimed_paths[key] = url
                return rel_path

            attempt = 0
            while True:
                candidate = self._alternative_name(rel_path, url, attempt)
                candidate_key = self._path_key(candidate)
                candidate_owner = self._claimed_paths.get(candidate_key)
                if candidate_owner is None or candidate_owner == url:
                    self._claimed_paths[candidate_key] = url
                    return candidate
                attempt += 1

    async def _release_path(self, rel_path: str, url: str) -> None:
        """Başarısız indirmelerde, dosya yazılmadığı için rezervasyonu bırakır."""
        async with self._claim_lock:
            key = self._path_key(rel_path)
            if self._claimed_paths.get(key) == url:
                del self._claimed_paths[key]

    async def _switch_reservation(self, old_rel: str, new_rel: str, url: str) -> str:
        """Uzantı düzeltmesi sonrası rezervasyonu yeni yola taşır."""
        if old_rel == new_rel:
            return old_rel
        final_rel = await self._reserve_path(new_rel, url)
        async with self._claim_lock:
            old_key = self._path_key(old_rel)
            if self._claimed_paths.get(old_key) == url:
                del self._claimed_paths[old_key]
        return final_rel

    # ------------------------------------------------------------------
    # İndirme
    # ------------------------------------------------------------------
    async def download_file(self, url: str, folder: str) -> DownloadResult:
        if not self.session:
            raise RuntimeError("Downloader session is not initialized")

        try:
            return await self._download_file(url, folder)
        except (asyncio.TimeoutError, aiohttp.ServerTimeoutError):
            logger.error(f"[TIMEOUT] İndirme zaman aşımı: {url}")
            return DownloadResult(
                url=url, status=DownloadStatus.TIMEOUT,
                detail=f"timeout after {self.timeout.total}s",
            )
        except Exception as e:
            logger.error(f"[ERR] İndirme hatası {url}: {e}")
            return DownloadResult(
                url=url, status=DownloadStatus.ERROR,
                detail=f"{type(e).__name__}: {e}",
            )

    async def _download_file(self, url: str, folder: str) -> DownloadResult:
        rel_path = self._derive_relative_path(url, folder)
        rel_path = await self._reserve_path(rel_path, url)

        abs_path = self._absolute_path(rel_path)
        if await aiofiles.os.path.exists(abs_path):
            logger.info(f"[SKIP] Zaten var: {abs_path}")
            return DownloadResult(
                url=url, status=DownloadStatus.SKIPPED, local_path=rel_path,
                detail="already exists",
            )

        try:
            async with self.semaphore:
                async with self.session.get(url) as response:
                    if response.status != 200:
                        logger.error(f"[ERR] Başarısız {url}: HTTP {response.status}")
                        await self._release_path(rel_path, url)
                        return DownloadResult(
                            url=url, status=DownloadStatus.HTTP_ERROR,
                            detail=f"HTTP {response.status}",
                        )

                    # Eksik uzantıları Content-Type'dan tahmin et ve düzelt
                    filename = posixpath.basename(rel_path)
                    if "." not in filename or filename.endswith("file"):
                        content_type = response.headers.get("Content-Type", "")
                        ext = self._guess_extension(content_type)
                        if ext:
                            new_rel = posixpath.join(
                                posixpath.dirname(rel_path), f"{filename}.{ext}"
                            )
                            rel_path = await self._switch_reservation(
                                rel_path, new_rel, url
                            )
                            abs_path = self._absolute_path(rel_path)

                            # Uzantı eklendikten sonra ortaya çıkan yol da
                            # önceki bir çalıştırmadan kalmış olabilir.
                            if await aiofiles.os.path.exists(abs_path):
                                logger.info(f"[SKIP] Zaten var: {abs_path}")
                                return DownloadResult(
                                    url=url, status=DownloadStatus.SKIPPED,
                                    local_path=rel_path, detail="already exists",
                                )

                    local_dir = os.path.dirname(abs_path)
                    if not await aiofiles.os.path.exists(local_dir):
                        await asyncio.to_thread(os.makedirs, local_dir, exist_ok=True)

                    # Önce geçici dosyaya yaz; yarıda kalan indirme, çağıran
                    # tarafından "başarılı" sayılan bir dosya bırakmasın.
                    tmp_path = abs_path + ".part"
                    try:
                        async with aiofiles.open(tmp_path, "wb") as f:
                            async for chunk in response.content.iter_chunked(64 * 1024):
                                await f.write(chunk)
                        await asyncio.to_thread(os.replace, tmp_path, abs_path)
                    except BaseException:
                        try:
                            await aiofiles.os.remove(tmp_path)
                        except OSError:
                            pass
                        raise

                    logger.info(f"[OK] İndirildi: {abs_path}")
                    return DownloadResult(
                        url=url, status=DownloadStatus.SUCCESS, local_path=rel_path,
                        detail=None,
                    )
        except BaseException:
            # HTTP hatası/zaman aşımı/IO hatası: yolu serbest bırak ki aynı
            # yolu isteyen başka bir URL gereksiz yere hash'li ada düşmesin.
            await self._release_path(rel_path, url)
            raise

    async def download_all(
        self, assets: Iterable[Tuple[str, str]]
    ) -> List[DownloadResult]:
        """Tüm kaynakları eşzamanlı indirir ve URL başına sonuç listesi döner.

        Girdi sıralanır: aynı göreli yolu isteyen URL'ler arasında hangisinin
        temel adı, hangisinin hash'li adı alacağı böylece çalıştırmalar arasında
        kararlı olur.
        """
        asset_list = sorted(set(assets))
        tasks = [self.download_file(url, folder) for url, folder in asset_list]
        return await asyncio.gather(*tasks)
