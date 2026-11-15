"""Integration tests for the download main loop using a fake browser driver.

These exercise the real main() flow (checkpoint resume, completion-record
skips, rendezvous stopping) with Selenium replaced by an in-memory fake, so
no browser or seleniumbase install is required.
"""

import json
from pathlib import Path

import gphotos_export.download as dl
import gphotos_export.utils as utils


URLS = [
    "https://photos.google.com/photo/p0",
    "https://photos.google.com/u/0/photo/p1?pli=1",   # redirect form
    "https://photos.google.com/photo/p2",
    "https://photos.google.com/photo/p3",
    "https://photos.google.com/photo/p4",
]


class FakeDriver:
    """Minimal driver whose current_url follows a scripted photo sequence."""

    def __init__(self, urls, start_index=0):
        self._urls = urls
        self._i = start_index

    @property
    def current_url(self):
        return self._urls[self._i]

    def get(self, url):
        # Starting from a checkpoint URL: jump to its index when known.
        cleaned = [utils.clean_url(u) for u in self._urls]
        target = utils.clean_url(url)
        if target in cleaned:
            self._i = cleaned.index(target)

    def quit(self):
        pass


def _patch_runtime(monkeypatch, tmp_path):
    """Redirect every runtime path/constant into an isolated temp dir."""
    session = tmp_path / "session"
    session_bw = tmp_path / "session-backward"
    session.mkdir()
    session_bw.mkdir()
    staging = tmp_path / "staging"
    staging_bw = tmp_path / "staging-backward"
    downloads = tmp_path / "downloads-2"

    monkeypatch.setattr(dl, "SESSION_DIR", session)
    monkeypatch.setattr(dl, "SESSION_DIR_BACKWARD", session_bw)
    monkeypatch.setattr(dl, "STAGING_DIR", staging)
    monkeypatch.setattr(dl, "STAGING_DIR_BACKWARD", staging_bw)
    monkeypatch.setattr(dl, "DOWNLOADS_DIR", downloads)
    monkeypatch.setattr(dl, "LASTDONE_FILE", tmp_path / ".lastdone")
    monkeypatch.setattr(dl, "LASTDONE_FILE_BACKWARD", tmp_path / "backward.lastdone")
    monkeypatch.setattr(dl, "COMPLETED_FILE", tmp_path / ".completed.jsonl")
    monkeypatch.setattr(dl, "COMPLETED_FILE_BACKWARD", tmp_path / "backward.completed.jsonl")
    monkeypatch.setattr(dl, "SKIPLIST_FILE", tmp_path / "skiplist.txt")
    # No real browser, no real waiting, no log files in the repo.
    monkeypatch.setattr(dl, "create_driver", lambda **kw: FakeDriver(URLS))
    monkeypatch.setattr(dl, "get_latest_photo", lambda driver: URLS[-1])
    monkeypatch.setattr(dl, "setup_logging", lambda: None)
    monkeypatch.setattr(dl, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(dl.time, "sleep", lambda *a, **k: None)


def _make_download_stub(monkeypatch, tmp_path):
    """Replace download_single: create a real file and return its path."""
    calls = []

    def fake_download_single(driver, staging_dir, downloads_dir, overwrite=False):
        url = utils.clean_url(driver.current_url)
        name = url.rsplit("/", 1)[-1] + ".jpg"
        final = downloads_dir / "2024" / "1" / name
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_bytes(b"\xff\xd8\xff")
        calls.append(url)
        return "ok", final

    monkeypatch.setattr(dl, "download_single", fake_download_single)
    return calls


def _navigate_forward_once(driver, current_url):
    driver._i = min(driver._i + 1, len(driver._urls) - 1)
    return True


def _navigate_backward_once(driver, current_url):
    driver._i = max(driver._i - 1, 0)
    return True


class TestRendezvousAtStart:
    def test_backward_worker_stops_immediately_when_forward_record_covers_start(
        self, monkeypatch, tmp_path
    ):
        _patch_runtime(monkeypatch, tmp_path)
        calls = _make_download_stub(monkeypatch, tmp_path)
        monkeypatch.setattr(dl, "navigate_backward", _navigate_backward_once)

        # Forward worker has already recorded the newest photo (p4), which is
        # where the backward worker would start. Note the stored URL is the
        # clean form while the driver reports the /u/0/ redirect form.
        utils.append_completed(
            dl.COMPLETED_FILE, URLS[-1], tmp_path / "downloads-2" / "p4.jpg",
        )

        monkeypatch.setattr("sys.argv", ["download", "--backward"])
        dl.main()

        # Nothing downloaded: rendezvous detected before the first download.
        assert calls == []
        # Backward record must not have been touched.
        assert not dl.COMPLETED_FILE_BACKWARD.exists()


class TestResumeSkipsCompletedAndStopsAtRendezvous:
    def test_backward_uses_own_records_to_skip_and_forward_records_to_stop(
        self, monkeypatch, tmp_path
    ):
        _patch_runtime(monkeypatch, tmp_path)
        calls = _make_download_stub(monkeypatch, tmp_path)
        monkeypatch.setattr(dl, "navigate_backward", _navigate_backward_once)

        # Checkpoint says: resume at p3.
        dl.LASTDONE_FILE_BACKWARD.write_text(URLS[3])
        # Backward's own completion record already covers p3 (resume point).
        utils.append_completed(
            dl.COMPLETED_FILE_BACKWARD, URLS[3],
            tmp_path / "downloads-2" / "2024" / "1" / "p3.jpg",
        )
        # Forward worker has already reached p1 — rendezvous point.
        utils.append_completed(
            dl.COMPLETED_FILE, URLS[1],
            tmp_path / "downloads-2" / "2024" / "1" / "p1.jpg",
        )

        monkeypatch.setattr("sys.argv", ["download", "--backward"])
        dl.main()

        # p3 skipped (own record); p2 downloaded; p1 triggers rendezvous and
        # is NOT downloaded; p0 never reached.
        assert calls == [utils.clean_url(URLS[2])]

        # The one new download was durably recorded with url + path.
        backward_records = utils.read_completed(dl.COMPLETED_FILE_BACKWARD)
        p2 = "https://photos.google.com/photo/p2"
        assert p2 in backward_records
        assert backward_records[p2]["path"].endswith("p2.jpg")

        # Checkpoint URL semantics preserved: still written every cycle.
        assert dl.LASTDONE_FILE_BACKWARD.exists()

    def test_forward_worker_writes_records_for_each_download(
        self, monkeypatch, tmp_path
    ):
        _patch_runtime(monkeypatch, tmp_path)
        calls = _make_download_stub(monkeypatch, tmp_path)
        monkeypatch.setattr(dl, "navigate_next", _navigate_forward_once)

        # Start at the oldest; forward stop point is the newest (p4).
        dl.LASTDONE_FILE.write_text(URLS[0])
        monkeypatch.setattr("sys.argv", ["download"])
        dl.main()

        # p0 (start, overwrite) then p1..p4 = 5 downloads.
        assert len(calls) == 5
        records = utils.read_completed(dl.COMPLETED_FILE)
        assert set(records.keys()) == {utils.clean_url(u) for u in URLS}
        # Every record carries a verifiable final path that exists.
        for record in records.values():
            assert Path(record["path"]).exists()
        # The redirect-form URL p1 was normalized to its clean identity.
        assert "https://photos.google.com/photo/p1" in records
        # Checkpoint still advanced.
        assert utils.clean_url(dl.LASTDONE_FILE.read_text().strip()) == \
            "https://photos.google.com/photo/p4"
        # Each record is one physical JSON line.
        lines = dl.COMPLETED_FILE.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 5
        for line in lines:
            json.loads(line)
