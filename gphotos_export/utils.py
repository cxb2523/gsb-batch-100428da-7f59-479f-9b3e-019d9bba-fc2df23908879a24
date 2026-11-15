"""Shared utilities for Google Photos scraper."""

import json
import logging
import os
import random
import re
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

try:
    import msvcrt  # Windows
except ImportError:  # pragma: no cover - platform branch
    msvcrt = None


SESSION_DIR = Path("./session")
SESSION_DIR_BACKWARD = Path("./session-backward")
DOWNLOADS_DIR = Path("./downloads-2")
STAGING_DIR = Path("./downloads-2/.staging")
STAGING_DIR_BACKWARD = Path("./downloads-2/.staging-backward")
LASTDONE_FILE = Path(".lastdone")
LASTDONE_FILE_BACKWARD = Path("backward.lastdone")
# One append-only JSON-lines completion record per direction. Every
# successfully downloaded media gets one line: {"url", "path", "ts", ...}.
COMPLETED_FILE = Path(".completed.jsonl")
COMPLETED_FILE_BACKWARD = Path("backward.completed.jsonl")
SKIPLIST_FILE = Path("skiplist.txt")
GOOGLE_PHOTOS_URL = "https://photos.google.com"
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2
DOWNLOAD_TIMEOUT = 60
HUMAN_DELAY_RANGE = (1.0, 3.0)
# How long to wait on a contended cross-process record lock before giving up.
LOCK_TIMEOUT_S = 30.0


def setup_logging():
    log_dir = Path("./logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"download-{time.strftime('%Y%m%d-%H%M%S')}.log"

    fmt = logging.Formatter(
        "[%(asctime)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    stdout_handler = logging.StreamHandler()
    stdout_handler.setFormatter(fmt)
    root.addHandler(stdout_handler)

    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    for name in ("seleniumbase", "selenium", "urllib3", "uc", "nodriver"):
        logging.getLogger(name).setLevel(logging.WARNING)

    logging.getLogger("setup").info("Logging to %s", log_file)


def create_driver(headed, user_data_dir, download_dir):
    from seleniumbase import Driver

    driver = Driver(
        uc=True,
        headed=headed,
        user_data_dir=str(user_data_dir),
        chromium_arg="--window-size=1280,720",
    )
    driver.execute_cdp_cmd("Page.setDownloadBehavior", {
        "behavior": "allow",
        "downloadPath": str(download_dir.resolve()),
    })
    return driver


def read_progress(lastdone_path):
    if not lastdone_path.exists():
        raise FileNotFoundError(
            f"No {lastdone_path} file found. Create it with the URL of your oldest photo."
        )
    url = lastdone_path.read_text().strip()
    if not url:
        raise ValueError(f"{lastdone_path} is empty. Add the URL of your oldest photo.")
    return url


def save_progress(lastdone_path, url):
    if not url.startswith("https://photos.google.com"):
        return
    tmp = lastdone_path.with_suffix(".tmp")
    tmp.write_text(url)
    os.replace(tmp, lastdone_path)


# Per-process locks so threads inside one worker also serialize on a record
# file. Cross-process safety is provided by the OS file lock in
# _file_lock(); this dict is keyed by resolved path so two Path objects
# pointing at the same file share one lock.
_record_locks = {}
_record_locks_guard = threading.Lock()


def _get_record_lock(lock_path):
    key = str(lock_path.resolve())
    with _record_locks_guard:
        lock = _record_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _record_locks[key] = lock
        return lock


class _file_lock:
    """Exclusive cross-process advisory lock on a sidecar lock file.

    Uses ``msvcrt.locking`` on Windows and ``fcntl.flock`` on POSIX. The
    lock file is created next to the record file (``<record>.lock``) so the
    record itself stays a pure JSONL stream. Acquires with a bounded retry
    instead of blocking forever on a wedged lock holder.
    """

    def __init__(self, lock_path, timeout=LOCK_TIMEOUT_S):
        self.lock_path = lock_path
        self.timeout = timeout
        self.fh = None

    def __enter__(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        # "a+b" creates the file if missing and never truncates it.
        self.fh = open(self.lock_path, "a+b")
        deadline = time.time() + self.timeout
        while True:
            try:
                if msvcrt is not None:
                    # Lock one byte at the current position; LK_NBLCK raises
                    # OSError when another process holds the region.
                    self.fh.seek(0)
                    msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.time() >= deadline:
                    self.fh.close()
                    self.fh = None
                    raise TimeoutError(
                        f"Timed out waiting for record lock: {self.lock_path}"
                    )
                time.sleep(0.05)

    def __exit__(self, exc_type, exc, tb):
        try:
            if msvcrt is not None:
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        finally:
            self.fh.close()
            self.fh = None


def append_completed(completed_path, url, final_path, extra=None):
    """Atomically append one completion record for a downloaded media item.

    The record is a single JSON object on one line (JSONL) containing at
    least:

      * ``url``  — the cleaned media URL (identity key)
      * ``path`` — the final on-disk path where the file landed

    A timestamp and any ``extra`` fields are included when available. Writes
    are serialized across processes (OS file lock on ``<record>.lock``) and
    across threads (an in-process lock), flushed and fsynced before the lock
    is released, so two workers can never overwrite each other or leave a
    half-written line.
    """
    record = {
        "url": clean_url(url),
        "path": str(final_path),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if extra:
        record.update(extra)
    line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")

    lock_path = completed_path.with_name(completed_path.name + ".lock")
    thread_lock = _get_record_lock(lock_path)
    with thread_lock, _file_lock(lock_path):
        completed_path.parent.mkdir(parents=True, exist_ok=True)
        with open(completed_path, "ab") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
    return record


def read_completed(completed_path):
    """Read a completion record file into a ``{cleaned_url: record}`` dict.

    Missing files yield an empty dict. Corrupt or otherwise unparseable
    lines — truncated JSON, non-object lines, records without a usable
    ``url``/``path`` — are skipped (one warning each) instead of aborting
    the resume, so a single bad line never blocks a whole rerun.
    """
    log = logging.getLogger("completed")
    records = {}
    if not completed_path.exists():
        return records
    with open(completed_path, "rb") as fh:
        for line_no, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                record = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                log.warning("Ignoring unparseable line %d in %s", line_no, completed_path)
                continue
            if not isinstance(record, dict):
                log.warning("Ignoring non-object line %d in %s", line_no, completed_path)
                continue
            url = record.get("url")
            path = record.get("path")
            if not isinstance(url, str) or not url or not isinstance(path, str) or not path:
                log.warning("Ignoring incomplete record on line %d in %s", line_no, completed_path)
                continue
            # Re-clean on read: older records may predate query stripping.
            records[clean_url(url)] = record
    return records


def completed_urls(completed_path):
    """Return the set of cleaned media URLs present in a completion record."""
    return set(read_completed(completed_path).keys())


class OtherCompletedIndex:
    """Lazily refreshed view of the *other* direction's completion record.

    Each worker rendezvous-checks against the record file owned by the
    opposite worker. Re-reading that whole file on every photo would be
    O(n²), so the file is only re-parsed when its mtime/size changes —
    which happens exactly when the other worker appends.
    """

    def __init__(self, completed_path):
        self.completed_path = completed_path
        self._urls = set()
        self._mtime_ns = None
        self._size = None
        self.refresh()

    @property
    def urls(self):
        """Current snapshot of the other worker's cleaned URL set."""
        return self.refresh()

    def refresh(self):
        try:
            stat = self.completed_path.stat()
        except OSError:
            # File does not exist (other worker hasn't written yet) or is
            # momentarily inaccessible: treat as empty and retry later.
            self._mtime_ns = None
            self._size = None
            self._urls = set()
            return self._urls
        if stat.st_mtime_ns != self._mtime_ns or stat.st_size != self._size:
            self._mtime_ns = stat.st_mtime_ns
            self._size = stat.st_size
            self._urls = completed_urls(self.completed_path)
        return self._urls

    def contains(self, url):
        """True if a cleaned ``url`` appears in the other worker's record."""
        self.refresh()
        return clean_url(url) in self._urls

    def __contains__(self, url):
        return self.contains(url)


def clean_url(url):
    """Normalize a Google Photos URL for identity comparison.

    Strips the ``/u/<n>/`` user segment (Google redirects through it at
    random) plus any query string / fragment, so the same media reached via
    different redirects compares equal:

        https://photos.google.com/u/0/photo/abc?pli=1 -> https://photos.google.com/photo/abc
    """
    url = re.sub(r"/u/\d+/", "/", url)
    # Drop query string and fragment — they carry no media identity.
    url = re.split(r"[?#]", url, maxsplit=1)[0]
    return url.rstrip("/")


def get_media_date(file_path):
    try:
        result = subprocess.run(
            ["exiftool", "-json", "-DateTimeOriginal", "-CreateDate", str(file_path)],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)[0]
            for field in ("DateTimeOriginal", "CreateDate"):
                val = data.get(field)
                if val and "0000" not in val:
                    parts = val.split(":")
                    return int(parts[0]), int(parts[1])
    except Exception:
        pass
    return 1970, 1


def get_date_from_html(driver):
    try:
        html = driver.page_source
        pattern = r'aria-label="(?:Photo|Video) - (?:Landscape|Portrait|Square) - ([A-Za-z]{3} \d{1,2}, \d{4}, \d{1,2}:\d{2}:\d{2}\s*[APM]{2})"'
        match = re.search(pattern, html)
        if match:
            date_str = match.group(1).replace("\u202f", " ")
            dt = datetime.strptime(date_str, "%b %d, %Y, %I:%M:%S %p")
            return dt.year, dt.month
    except Exception:
        pass
    return 1970, 1


def load_skiplist(skiplist_path):
    """Load URLs to skip from a text file (one per line)."""
    if not skiplist_path.exists():
        return set()
    urls = set()
    for line in skiplist_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.add(clean_url(line))
    return urls


def file_already_downloaded(filename, downloads_dir):
    """Check if a file with this name already exists anywhere in downloads."""
    for dirpath, _, filenames in os.walk(downloads_dir):
        if os.path.basename(dirpath).startswith(".staging"):
            continue
        if filename in filenames:
            return True
    return False


def _place_file(src_path, dest_dir, overwrite=False):
    """Move a single file into dest_dir, handling name truncation and duplicates."""
    filename = src_path.name
    if len(filename) > 200:
        ext = src_path.suffix
        filename = filename[:200 - len(ext)] + ext

    dest = dest_dir / filename

    if not overwrite:
        counter = 2
        while dest.exists():
            stem = src_path.stem[:200]
            dest = dest_dir / f"{stem} {counter}{src_path.suffix}"
            counter += 1

    # os.replace (unlike Path.rename) atomically overwrites an existing
    # destination on both POSIX and Windows.
    os.replace(src_path, dest)
    return dest


def organize_file(src_path, downloads_dir, driver, overwrite=False):
    log = logging.getLogger("organize")

    # If it's a zip, extract files into the same destination folder.
    if src_path.suffix.lower() == ".zip":
        import zipfile
        if zipfile.is_zipfile(src_path):
            log.info("Got zip file: %s — extracting", src_path.name)
            year, month = get_media_date(src_path)
            if year == 1970:
                year, month = get_date_from_html(driver)
            dest_dir = downloads_dir / str(year) / str(month)
            dest_dir.mkdir(parents=True, exist_ok=True)
            extracted = []
            with zipfile.ZipFile(src_path, "r") as zf:
                for member in zf.infolist():
                    if member.is_dir():
                        continue
                    extracted_path = Path(zf.extract(member, src_path.parent))
                    final = _place_file(extracted_path, dest_dir, overwrite=overwrite)
                    log.info("  Extracted: %s -> %s", member.filename, final.name)
                    extracted.append(final)
            src_path.unlink()
            return extracted[0] if extracted else src_path

    year, month = get_media_date(src_path)
    if year == 1970:
        year, month = get_date_from_html(driver)
        if year != 1970:
            log.info("Date from HTML: %d/%d", year, month)

    dest_dir = downloads_dir / str(year) / str(month)
    dest_dir.mkdir(parents=True, exist_ok=True)
    return _place_file(src_path, dest_dir, overwrite=overwrite)


def wait_for_download(staging_dir, timeout, known_files):
    log = logging.getLogger("download")
    deadline = time.time() + timeout

    while time.time() < deadline:
        current = set(staging_dir.iterdir())
        new_files = current - known_files

        # Filter out in-progress downloads
        completed = [f for f in new_files if not f.name.endswith(".crdownload")]
        in_progress = [f for f in new_files if f.name.endswith(".crdownload")]

        if completed:
            return completed[0]

        if in_progress:
            # Reset deadline while download is actively happening
            deadline = time.time() + timeout

        time.sleep(0.5)

    raise TimeoutError("Download timed out")


def human_delay(min_s=None, max_s=None):
    if min_s is None:
        min_s = HUMAN_DELAY_RANGE[0]
    if max_s is None:
        max_s = HUMAN_DELAY_RANGE[1]
    time.sleep(random.uniform(min_s, max_s))
