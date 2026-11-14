"""Concurrent image downloading with resumable files and live progress."""

from __future__ import annotations

import concurrent.futures
import os
import random
import re
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests

from .config import BASE
from .http import _pick_proxies, browser_headers
from .ui import err_console, make_progress

# Token identifying temp files created by this process. Fragments left by
# earlier (killed) runs carry a different token and are treated as stale;
# temp files of live workers in this run are never touched by peers.
_RUN_TOKEN = uuid.uuid4().hex


def _tmp_name(pin_id: str) -> str:
    """Unique temp-file name for one in-progress download.

    The leading dot keeps it out of the ``{pin_id}.*`` resume glob, and the
    run token lets us tell our own live temp files from stale fragments.
    """
    return f".{pin_id}.{_RUN_TOKEN}.{uuid.uuid4().hex}.part"


def _stale_fragments(out_dir: Path, pin_id: str) -> list[Path]:
    """Temp fragments of this pin left behind by previous, interrupted runs."""
    frags = []
    for p in out_dir.glob(f".{pin_id}.*.part"):
        if not p.is_file():
            continue
        parts = p.name.split(".")
        # ['', pin_id, run_token, uuid, 'part'] — foreign/unknown token = stale
        if len(parts) < 5 or parts[2] != _RUN_TOKEN:
            frags.append(p)
    return frags


# Serialize atomic replaces per destination: two workers finishing the same
# pin at the same moment would otherwise collide inside MoveFileEx (WinError 5).
_DEST_LOCKS: dict[str, threading.Lock] = {}
_DEST_LOCKS_GUARD = threading.Lock()


def _atomic_replace(tmp: Path, dest: Path, tries: int = 5) -> bool:
    """Move tmp onto dest atomically; retry transient sharing violations."""
    with _DEST_LOCKS_GUARD:
        lock = _DEST_LOCKS.setdefault(str(dest), threading.Lock())
    for _ in range(tries):
        try:
            with lock:
                os.replace(tmp, dest)
            return True
        except OSError:
            time.sleep(0.05)
    return False


def download_image(session: requests.Session, pin: dict, out_dir: Path,
                   max_retries: int = 3) -> str:
    """Download one pin's image.

    Returns the filename, 'EXISTS', 'REPAIRED' (a stale temp fragment was
    replaced by a complete file), or '' on failure. Downloads go to a temp
    file first and are atomically moved into place only after validation,
    so a killed process never leaves a half-written final file behind.
    """
    time.sleep(random.uniform(0, 0.4))  # small random stagger between workers
    url = pin["image_url"]
    # Upgrade sized URLs (736x etc.) to /originals/ for max quality.
    upgraded = re.sub(r"/(\d+x\d*|\d+x)/", "/originals/", url)
    candidates = [upgraded, url] if upgraded != url else [url]

    for existing in out_dir.glob(f"{pin['pin_id']}.*"):
        if existing.is_file() and existing.stat().st_size > 0:
            pin["local_file"] = existing.name
            return "EXISTS"

    # Interrupted earlier runs may have left temp fragments behind. A fresh
    # download that replaces them is a repair, not a brand-new download.
    had_fragment = False
    for frag in _stale_fragments(out_dir, pin["pin_id"]):
        had_fragment = True
        try:
            frag.unlink()
        except OSError:
            pass  # locked or already gone — leave it alone

    for url in candidates:
        ext = Path(urlparse(url).path).suffix.lower() or ".jpg"
        filename = f"{pin['pin_id']}{ext}"
        dest = out_dir / filename
        if dest.exists() and dest.stat().st_size > 0:
            pin["local_file"] = filename
            return "EXISTS"
        for attempt in range(1, max_retries + 1):
            tmp = out_dir / _tmp_name(pin["pin_id"])
            try:
                r = session.get(url, timeout=30, stream=True,
                                headers={**browser_headers(referer=f"{BASE}/"),
                                         "Accept": "image/*,*/*;q=0.8"},
                                proxies=_pick_proxies(session))
                if r.status_code == 200 and r.headers.get(
                        "Content-Type", "image").startswith("image"):
                    try:
                        expected = int(r.headers["Content-Length"])
                    except (KeyError, ValueError):
                        expected = None
                    written = 0
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_content(65536):
                            f.write(chunk)
                            written += len(chunk)
                    if written > 0 and (expected is None
                                        or written == expected):
                        # Atomic replace: concurrent workers on the same pin
                        # can never leave a half-written final file.
                        if _atomic_replace(tmp, dest):
                            pin["local_file"] = filename
                            return "REPAIRED" if had_fragment else filename
                        # replace kept failing — clean up and retry the URL
            except requests.RequestException:
                pass
            finally:
                try:
                    tmp.unlink()  # no-op once the temp file was replaced
                except OSError:
                    pass
            time.sleep(1.5 * attempt)
    return ""


def download_all(session: requests.Session, pins: list[dict], out_dir: Path,
                 workers: int, min_width: int, min_height: int,
                 progress_cb=None) -> dict:
    """Download images concurrently with a live progress bar. Returns stats."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stats = {"downloaded": 0, "skipped_existing": 0, "skipped_small": 0,
             "skipped_video": 0, "repaired": 0, "failed": 0}

    def apply(pin: dict, result: str) -> None:
        if result == "EXISTS":
            stats["skipped_existing"] += 1
        elif result == "SMALL":
            stats["skipped_small"] += 1
        elif result == "VIDEO":
            stats["skipped_video"] += 1
        elif result == "REPAIRED":
            stats["repaired"] += 1  # download_image already set local_file
        elif result:
            stats["downloaded"] += 1
            pin["local_file"] = result
        else:
            stats["failed"] += 1

    prog, task = make_progress("[cyan]Downloading images", len(pins))
    with prog:
        futures = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for i, pin in enumerate(pins, 1):
                if pin["is_video"] and not pin["image_url"]:
                    apply(pin, "VIDEO")
                    prog.update(task, advance=1)
                    if progress_cb:
                        progress_cb(sum(stats.values()))
                    continue
                if pin["width"] and (pin["width"] < min_width
                                     or pin["height"] < min_height):
                    apply(pin, "SMALL")
                    prog.update(task, advance=1)
                    if progress_cb:
                        progress_cb(sum(stats.values()))
                    continue
                futures[pool.submit(download_image, session, pin, out_dir)] = pin

            for fut in concurrent.futures.as_completed(futures):
                pin = futures[fut]
                try:
                    apply(pin, fut.result())
                except Exception as e:  # noqa: BLE001
                    err_console.print(f"  pin {pin['pin_id']}: {e}")
                    apply(pin, "")
                prog.update(task, advance=1)
                if progress_cb:
                    progress_cb(sum(stats.values()))
        prog.update(task, description="[green]Downloads done")
    return stats
