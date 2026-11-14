"""Unit tests for pinterest_scraper."""

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import requests

from pinterest_scraper.config import __version__
from pinterest_scraper.dedupe import DedupeStore
from pinterest_scraper.downloader import download_all, download_image
from pinterest_scraper.scraper import _best_image, extract_pin
from pinterest_scraper.storage import save_outputs
from pinterest_scraper.web.server import Job, ScrapeRequest


class TestScraperCore(unittest.TestCase):
    def test_version(self):
        self.assertEqual(__version__, "1.3.0")

    def test_extract_pin_valid(self):
        raw = {
            "id": 123456789,
            "type": "pin",
            "title": "Test Pin",
            "description": "A test description",
            "images": {
                "236x": {"url": "https://i.pinimg.com/236x/abc.jpg", "width": 236, "height": 300},
                "736x": {"url": "https://i.pinimg.com/736x/abc.jpg", "width": 736, "height": 935},
            },
            "creator": {"username": "photographer", "full_name": "Photo Pro"},
            "board": {"name": "Inspiration", "url": "/photographer/inspiration/"},
            "repin_count": 42,
        }
        pin = extract_pin(raw)
        self.assertIsNotNone(pin)
        self.assertEqual(pin["pin_id"], "123456789")
        self.assertEqual(pin["title"], "Test Pin")
        self.assertEqual(pin["width"], 736)
        self.assertEqual(pin["height"], 935)
        self.assertEqual(pin["creator_username"], "photographer")
        self.assertEqual(pin["board_name"], "Inspiration")
        self.assertEqual(pin["saves"], 42)
        self.assertAlmostEqual(pin["aspect_ratio"], 0.787, places=2)

    def test_extract_pin_skips_non_pins(self):
        self.assertIsNone(extract_pin({"id": 1, "type": "board"}))
        self.assertIsNone(extract_pin({"id": 2, "type": "user"}))
        self.assertIsNone(extract_pin({}))

    def test_best_image_empty(self):
        largest, variants = _best_image({})
        self.assertIsNone(largest)
        self.assertEqual(variants, {})


class TestStorageAndDedupe(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.out_dir = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_dedupe_store(self):
        store_path = self.out_dir / ".seen_pins.json"
        store = DedupeStore(store_path, enabled=True)
        pins = [
            {"pin_id": "1", "image_url": "https://example.com/1.jpg"},
            {"pin_id": "2", "image_url": "https://example.com/2.jpg"},
            {"pin_id": "1", "image_url": "https://example.com/1.jpg"},
        ]
        filtered = store.filter(pins)
        self.assertEqual(len(filtered), 2)
        store.add(filtered)
        store.save()

        # Reload store and verify persistence
        new_store = DedupeStore(store_path, enabled=True)
        self.assertIn("1", new_store.pin_ids)
        self.assertIn("2", new_store.pin_ids)
        second_filter = new_store.filter(pins)
        self.assertEqual(len(second_filter), 0)

    def test_save_outputs(self):
        pins = [
            {"pin_id": "100", "title": "Pin 100", "saves": 10},
            {"pin_id": "200", "title": "Pin 200", "saves": 20},
        ]
        res = save_outputs(pins, self.out_dir, "test_job")
        self.assertTrue(res["json"].exists())
        self.assertTrue(res["csv"].exists())
        self.assertEqual(res["total"], 2)

        # Merge with updated data
        updated_pins = [
            {"pin_id": "100", "title": "Pin 100 Updated", "saves": 15},
            {"pin_id": "300", "title": "Pin 300", "saves": 30},
        ]
        res2 = save_outputs(updated_pins, self.out_dir, "test_job")
        self.assertEqual(res2["total"], 3)
        data = json.loads(res2["json"].read_text(encoding="utf-8"))
        p100 = next(p for p in data if p["pin_id"] == "100")
        self.assertEqual(p100["title"], "Pin 100 Updated")


class _FakeResponse:
    """Minimal stand-in for a streamed requests.Response."""

    def __init__(self, body=b"", status_code=200, fail_after=None):
        self.status_code = status_code
        self.headers = {"Content-Type": "image/jpeg",
                        "Content-Length": str(len(body))}
        self._body = body
        self._fail_after = fail_after

    def iter_content(self, chunk_size):
        sent = 0
        for i in range(0, len(self._body), 8):  # small chunks to allow mid-stream failure
            chunk = self._body[i:i + 8]
            if self._fail_after is not None and sent + len(chunk) > self._fail_after:
                raise requests.ConnectionError("connection dropped mid-stream")
            sent += len(chunk)
            yield chunk


class _FakeSession:
    """Fake requests.Session serving a fixed body; thread-safe."""

    def __init__(self, body=b"", status_code=200, fail_after=None):
        self._body = body
        self._status = status_code
        self._fail_after = fail_after
        self.requests = 0
        self._lock = threading.Lock()

    def get(self, url, **kwargs):
        with self._lock:
            self.requests += 1
        return _FakeResponse(self._body, self._status, self._fail_after)


def _pin(pin_id, url=None):
    return {"pin_id": pin_id,
            "image_url": url or f"https://example.com/{pin_id}.jpg",
            "is_video": False, "width": 0, "height": 0, "local_file": ""}


class TestDownloader(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.out_dir = Path(self.tmp_dir.name)
        # Skip the politeness/backoff sleeps so tests stay fast.
        sleeper = mock.patch("pinterest_scraper.downloader.time.sleep",
                             lambda *a, **k: None)
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _leftover_temps(self):
        return [p for p in self.out_dir.iterdir() if p.name.endswith(".part")]

    def test_existing_image_sets_local_file(self):
        # Pre-create image file
        existing_file = self.out_dir / "999.jpg"
        existing_file.write_bytes(b"fake image data")

        pin = {"pin_id": "999", "image_url": "https://example.com/999.jpg", "local_file": ""}
        session = requests.Session()
        res = download_image(session, pin, self.out_dir)
        self.assertEqual(res, "EXISTS")
        self.assertEqual(pin["local_file"], "999.jpg")

    def test_download_success_leaves_only_final_file(self):
        body = b"\xff\xd8image-bytes" * 500
        session = _FakeSession(body)
        pin = _pin("777")
        res = download_image(session, pin, self.out_dir)
        self.assertEqual(res, "777.jpg")
        self.assertEqual((self.out_dir / "777.jpg").read_bytes(), body)
        self.assertEqual(pin["local_file"], "777.jpg")
        self.assertEqual(self._leftover_temps(), [])
        # Only the final file remains in the directory
        self.assertEqual([p.name for p in self.out_dir.iterdir()], ["777.jpg"])

    def test_interrupted_write_cleans_temp_and_leaves_no_dest(self):
        body = b"x" * 4096
        session = _FakeSession(body, fail_after=64)  # dies mid-stream
        pin = _pin("888")
        res = download_image(session, pin, self.out_dir, max_retries=2)
        self.assertEqual(res, "")
        self.assertFalse((self.out_dir / "888.jpg").exists())
        self.assertEqual(self._leftover_temps(), [])

        # Same via download_all: counted as failed, not downloaded
        stats = download_all(session, [_pin("888")], self.out_dir,
                             workers=1, min_width=0, min_height=0)
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(stats["downloaded"], 0)
        self.assertFalse((self.out_dir / "888.jpg").exists())
        self.assertEqual(self._leftover_temps(), [])

    def test_stale_fragment_is_repaired_and_counted(self):
        body = b"complete-image" * 300
        # Leftover temp fragment from a killed previous run (foreign run token)
        frag = self.out_dir / ".555.deadbeefcafe.00112233.part"
        frag.write_bytes(b"partial-")

        pin = _pin("555")
        res = download_image(_FakeSession(body), pin, self.out_dir)
        self.assertEqual(res, "REPAIRED")
        self.assertEqual((self.out_dir / "555.jpg").read_bytes(), body)
        self.assertEqual(pin["local_file"], "555.jpg")
        self.assertEqual(self._leftover_temps(), [])

        # Again through download_all: goes to the repaired counter and the
        # progress callback, not to downloaded or failed
        frag.write_bytes(b"partial-")
        (self.out_dir / "555.jpg").unlink()
        progress = []
        stats = download_all(_FakeSession(body), [_pin("555")], self.out_dir,
                             workers=1, min_width=0, min_height=0,
                             progress_cb=progress.append)
        self.assertEqual(stats["repaired"], 1)
        self.assertEqual(stats["downloaded"], 0)
        self.assertEqual(stats["failed"], 0)
        self.assertEqual(stats["skipped_existing"], 0)
        self.assertEqual(progress[-1], 1)
        self.assertEqual((self.out_dir / "555.jpg").read_bytes(), body)
        self.assertEqual(self._leftover_temps(), [])

        # After the repair the file is final: a rerun is a plain EXISTS
        stats = download_all(_FakeSession(body), [_pin("555")], self.out_dir,
                             workers=1, min_width=0, min_height=0)
        self.assertEqual(stats["skipped_existing"], 1)
        self.assertEqual(stats["repaired"], 0)

    def test_concurrent_workers_same_pin_keep_file_intact(self):
        body = bytes(range(256)) * 1024  # 256 KiB, forces overlapping streams
        session = _FakeSession(body)
        pins = [_pin("666"), _pin("666")]
        stats = download_all(session, pins, self.out_dir,
                             workers=2, min_width=0, min_height=0)
        self.assertEqual((self.out_dir / "666.jpg").read_bytes(), body)
        self.assertEqual(self._leftover_temps(), [])
        self.assertEqual(stats["failed"], 0)
        # Each worker either downloaded fresh or repaired — never a bad file
        self.assertEqual(stats["downloaded"] + stats["repaired"], 2)


class TestWebJob(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.out_dir = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_scrape_request_ignores_extra(self):
        req = ScrapeRequest(mode="search", query="cat", show_insights=True, lang="en")
        self.assertEqual(req.query, "cat")
        self.assertEqual(req.mode, "search")

    def test_job_does_not_crash_on_store(self):
        req = ScrapeRequest(mode="search", query="test", limit=2, download=False, details=False, dedup=False)
        job = Job(req, self.out_dir)
        # Mock search_pins to return test data without network
        import pinterest_scraper.web.server as srv
        original_search = srv.search_pins
        try:
            srv.search_pins = lambda session, query, limit, **kw: [
                {"pin_id": "p1", "image_url": "https://example.com/p1.jpg", "local_file": ""},
                {"pin_id": "p2", "image_url": "https://example.com/p2.jpg", "local_file": ""},
            ]
            job._run()
            self.assertEqual(len(job.pins), 2)
            self.assertEqual(job.error, "")
        finally:
            srv.search_pins = original_search

    def test_board_pins_invalid_url_raises_value_error(self):
        from pinterest_scraper.scraper import board_pins
        import requests
        session = requests.Session()
        with self.assertRaises(ValueError):
            board_pins(session, "invalid_url_without_user_and_slug", 10)

    def test_xlsx_response(self):
        from pinterest_scraper.web.server import _xlsx_response
        pins = [{"pin_id": "1", "title": "Pin One", "saves": 100}]
        resp = _xlsx_response(pins)
        self.assertEqual(resp.media_type, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    def test_zip_response(self):
        from pinterest_scraper.web.server import _zip_response
        pins = [{"pin_id": "1", "local_file": "1.jpg"}]
        resp = _zip_response(pins)
        self.assertEqual(resp.media_type, "application/zip")


if __name__ == "__main__":
    unittest.main()
