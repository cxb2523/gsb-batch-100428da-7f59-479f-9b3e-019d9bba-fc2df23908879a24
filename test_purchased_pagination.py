# Teste local da paginação resiliente da origem "purchased" (sem login real).
# Usa uma API paginada falsa que falha em offsets configuráveis.
import os
import sys
import types
import shutil
import tempfile
import threading

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# --- stubs das dependências pesadas antes de importar privacy_scraper ---
camoufox_mod = types.ModuleType("camoufox")
camoufox_sync = types.ModuleType("camoufox.sync_api")
camoufox_sync.Camoufox = object
camoufox_mod.sync_api = camoufox_sync
sys.modules["camoufox"] = camoufox_mod
sys.modules["camoufox.sync_api"] = camoufox_sync

dotenv_mod = types.ModuleType("dotenv")
dotenv_mod.load_dotenv = lambda *a, **k: None
sys.modules["dotenv"] = dotenv_mod


class _FakeTqdm:
    def __init__(self, *a, **k):
        self.total = k.get("total", 0)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def update(self, n=1):
        pass

    def refresh(self):
        pass

    @staticmethod
    def write(*a, **k):
        pass


tqdm_mod = types.ModuleType("tqdm")
tqdm_mod.tqdm = _FakeTqdm
sys.modules["tqdm"] = tqdm_mod

curl_cffi_mod = types.ModuleType("curl_cffi")
curl_cffi_mod.requests = types.SimpleNamespace(Session=object)
sys.modules["curl_cffi"] = curl_cffi_mod

_orig_which = shutil.which
shutil.which = lambda c: "ffmpeg" if c == "ffmpeg" else _orig_which(c)

# .env falso em diretório temporário (o módulo exige .env no cwd ao importar)
_tmpdir = tempfile.mkdtemp(prefix="ps_test_")
os.chdir(_tmpdir)
with open(".env", "w") as f:
    f.write("EMAIL=a@b.c\nPASSWORD=x\n")

sys.path.insert(0, REPO_DIR)
import privacy_scraper as ps  # noqa: E402

PAGE_SIZE = 20
TARGET = "alvo"


def make_post(i, profile=TARGET, unlocked=True):
    return {
        "creator": {"profileName": profile},
        "createdAt": f"2026-01-{(i % 28) + 1:02d}T{(i % 24):02d}:00:00Z",
        "text": f"post {i}",
        "price": 10,
        "medias": [
            {"type": "image", "isLocked": not unlocked, "url": f"https://cdn/img{i}.jpg", "mediaId": f"img{i}"},
            {"type": "video", "isLocked": not unlocked, "url": f"https://cdn/vid{i}.mp4", "mediaId": f"vid{i}"},
        ],
    }


class FakeScraper:
    """API paginada falsa: fail_map[offset] = nº de falhas antes de suceder, ou 'always'."""

    def __init__(self, pages, fail_map=None):
        self.pages = pages
        self.fail_map = fail_map or {}
        self.calls = {}
        self._lock = threading.Lock()
        self.token_v2 = "fake"

    def refresh_token_if_needed(self):
        pass

    def get_purchased_media(self, offset=0, limit=PAGE_SIZE):
        with self._lock:
            self.calls[offset] = self.calls.get(offset, 0) + 1
            n_calls = self.calls[offset]
        fail = self.fail_map.get(offset, 0)
        if fail == "always" or n_calls <= fail:
            return None  # simula falha de requisição (HTTP não-200)
        return {"items": self.pages.get(offset, [])}


def build_pages():
    # offsets 0..80 cheios (20 itens), offset 100 curto (5 itens) => fim real
    pages = {}
    idx = 0
    for off in range(0, 100, PAGE_SIZE):
        pages[off] = [make_post(idx + j) for j in range(PAGE_SIZE)]
        idx += PAGE_SIZE
    pages[100] = [make_post(idx + j) for j in range(5)]
    return pages


def run_case(name, fail_map, expect_failed_offsets, expect_items, expect_calls_min=None):
    scraper = FakeScraper(build_pages(), fail_map)
    dl = ps.MediaDownloader(None, scraper)
    items = dl.list_items(TARGET, "purchased", "3")
    report = dl.last_list_report

    assert report is not None, "relatório de paginação ausente"
    got_failed = sorted(fp["offset"] for fp in report["failed_pages"])
    assert got_failed == sorted(expect_failed_offsets), \
        f"[{name}] offsets falhos esperados {expect_failed_offsets}, obtidos {got_failed}"
    assert len(items) == expect_items, \
        f"[{name}] esperados {expect_items} itens, obtidos {len(items)}"
    # ordenação por data decrescente mantida
    dates = [it["date_raw"] for it in items]
    assert dates == sorted(dates, reverse=True), f"[{name}] lista não está em ordem decrescente de data"
    # só itens do profile alvo e com mídia desbloqueada
    for it in items:
        assert it["files"], f"[{name}] entrada vazia indevida na lista"
    if expect_calls_min:
        for off, n in expect_calls_min.items():
            assert scraper.calls.get(off, 0) >= n, \
                f"[{name}] offset {off}: esperadas >= {n} chamadas, obtidas {scraper.calls.get(off, 0)}"
    print(f"OK - {name}: {len(items)} itens, páginas ok={report['ok_pages']}, "
          f"falhas={got_failed}")


def main():
    total_full = 5 * PAGE_SIZE + 5  # 105 posts do profile alvo

    # 1) página 40 falha 2x e depois funciona: retry recupera, lista completa
    run_case("retry recupera página", fail_map={40: 2},
             expect_failed_offsets=[], expect_items=total_full,
             expect_calls_min={40: 3})

    # 2) página 60 sempre falha: registrada, não vira "fim de lista",
    #    paginação continua até a página curta real (offset 100)
    run_case("página perdida não encerra lista", fail_map={60: "always"},
             expect_failed_offsets=[60], expect_items=total_full - PAGE_SIZE,
             expect_calls_min={60: 1 + ps.PURCHASED_PAGE_RETRIES, 100: 1})

    # 3) falha com exceção (não só retorno None)
    class BoomScraper(FakeScraper):
        def get_purchased_media(self, offset=0, limit=PAGE_SIZE):
            if offset == 20:
                raise ConnectionError("boom")
            return super().get_purchased_media(offset, limit)

    scraper = BoomScraper(build_pages())
    dl = ps.MediaDownloader(None, scraper)
    items = dl.list_items(TARGET, "purchased", "3")
    report = dl.last_list_report
    assert [fp["offset"] for fp in report["failed_pages"]] == [20]
    assert "boom" in report["failed_pages"][0]["reason"]
    assert len(items) == total_full - PAGE_SIZE
    print(f"OK - exceção registrada com motivo: {report['failed_pages'][0]['reason']}")

    # 4) filtro de profile: posts de outro profile não entram
    pages = build_pages()
    pages[0] = pages[0] + [make_post(9999, profile="outro")]
    scraper = FakeScraper(pages)
    dl = ps.MediaDownloader(None, scraper)
    items = dl.list_items(TARGET, "purchased", "3")
    assert all("post 9999" not in (it["caption"] or "") for it in items)
    print("OK - filtro por profile mantido")

    print("\nTodos os testes passaram.")


if __name__ == "__main__":
    main()
