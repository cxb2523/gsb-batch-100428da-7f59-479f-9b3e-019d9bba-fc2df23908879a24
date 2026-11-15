"""Offline tests for Pinterest.batch_download item-by-item progression.

These tests never start a real browser and never touch the network:
- Pinterest.__init__ (Chrome + Pinterest login) is bypassed with __new__.
- The Selenium driver is replaced by FakeDriver, which simulates tab
  creation, tab closing with window-handle reordering, and switch errors.
- Pinterest.crawl is replaced by an in-memory stub, so no page is read and
  no image is downloaded.
"""

import asyncio
import os
import sys
import tempfile
import unittest

# Allow running with plain `python -m unittest` / `python tests/...` and with
# pytest, including under isolated mode (-I) where PYTHONPATH is ignored.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from exceptions import EndPageException
from pinterest import Pinterest


class FakeSwitchTo:
    def __init__(self, driver):
        self.driver = driver
        self.fail_on = None
        self.switch_calls = 0

    def window(self, handle):
        self.switch_calls += 1
        if self.fail_on is not None and self.fail_on(handle):
            raise RuntimeError(f"simulated switch failure for {handle}")
        if handle not in self.driver.window_handles:
            raise RuntimeError(f"no such window: {handle}")
        self.driver.current_handle = handle


class FakeDriver:
    """Simulates just enough of the Selenium driver surface."""

    def __init__(self, initial_tabs=1):
        self.window_handles = [f"tab-{i}" for i in range(initial_tabs)]
        self.current_handle = self.window_handles[0]
        self.switch_to = FakeSwitchTo(self)
        self.closed_handles = []
        self.tab_url = {}
        self.get_calls = []

    def execute_script(self, script):
        if "window.open" in script:
            new_handle = f"tab-{len(self.window_handles) + len(self.closed_handles)}"
            self.window_handles.append(new_handle)
            return new_handle
        return None

    def get(self, url):
        self.tab_url[self.current_handle] = url
        self.get_calls.append((self.current_handle, url))

    def implicitly_wait(self, seconds):
        pass

    def close(self):
        handle = self.current_handle
        if handle not in self.window_handles:
            raise RuntimeError(f"cannot close missing window {handle}")
        self.closed_handles.append(handle)
        self.window_handles.remove(handle)
        # Simulate Chrome reordering the remaining window handles.
        self.window_handles.reverse()
        if self.window_handles:
            self.current_handle = self.window_handles[0]


def make_crawl(behavior, crawl_calls, expected_dirs):
    """Return a crawl() stub driven by a per-URL action queue.

    behavior: {url: ["ok", "ok", "end", ...]} or {"url": RuntimeError(...)}
    "ok"  -> pretend one new image was discovered on this item's page
    "end" -> raise EndPageException, i.e. the page reached its end
    Exception instance -> raised to simulate a page read failure
    """

    def fake_crawl(self, dir_arg):
        url = self.driver.tab_url[self.driver.current_handle]

        # The directory passed in must always be this link's own directory.
        assert dir_arg == expected_dirs[url], (
            f"directory binding broken: {url} was crawled into {dir_arg}, "
            f"expected {expected_dirs[url]}"
        )
        crawl_calls.append((url, dir_arg))

        action = behavior[url]
        if isinstance(action, Exception):
            raise action
        step = action.pop(0)
        if step == "end":
            raise EndPageException()
        # Simulate one newly discovered image, recorded on the shared piclist.
        self.piclist.append(f"{url}#img{len(self.piclist)}")

    return fake_crawl


class BatchDownloadTest(unittest.TestCase):
    def setUp(self):
        asyncio.set_event_loop(asyncio.new_event_loop())
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def make_pinterest(self, driver):
        p = Pinterest.__new__(Pinterest)
        p.driver = driver
        p.piclist = []
        p.currentdir = self.tmpdir
        p.user_agent = "fake-agent"
        return p

    def run_batch(self, urls, dirs, behavior, n=-1, driver=None):
        driver = driver or FakeDriver(initial_tabs=1)
        p = self.make_pinterest(driver)
        expected_dirs = {
            url: os.path.join(self.tmpdir, d) for url, d in zip(urls, dirs)
        }
        crawl_calls = []
        p.crawl = make_crawl(behavior, crawl_calls, expected_dirs).__get__(p, Pinterest)
        results = p.batch_download(n, list(urls), list(dirs))
        return p, driver, results, crawl_calls, expected_dirs

    def test_binding_survives_tab_close_and_handle_reorder(self):
        urls = ["http://u0", "http://u1", "http://u2"]
        dirs = ["download0", "download1", "download2"]
        behavior = {
            # u0 ends first round; u1 ends second round; u2 ends third round.
            "http://u0": ["end"],
            "http://u1": ["ok", "end"],
            "http://u2": ["ok", "ok", "end"],
        }

        p, driver, results, crawl_calls, expected_dirs = self.run_batch(
            urls, dirs, behavior
        )

        # Every crawl call used the directory bound to its own link.
        for url, dir_arg in crawl_calls:
            self.assertEqual(dir_arg, expected_dirs[url])

        # Every link is reported exactly once, still paired with its directory.
        self.assertEqual([r["url"] for r in results], urls)
        self.assertEqual(
            [r["dir"] for r in results],
            [expected_dirs[u] for u in urls],
        )

        by_url = {r["url"]: r for r in results}
        self.assertEqual(by_url["http://u0"]["status"], "ended")
        self.assertEqual(by_url["http://u0"]["downloaded"], 0)
        self.assertEqual(by_url["http://u1"]["status"], "ended")
        self.assertEqual(by_url["http://u1"]["downloaded"], 1)
        self.assertEqual(by_url["http://u2"]["status"], "ended")
        self.assertEqual(by_url["http://u2"]["downloaded"], 2)

        # Each ended item closed exactly its own tab; handles were reordered
        # by FakeDriver.close() along the way.
        self.assertEqual(set(driver.closed_handles), {"tab-0", "tab-1", "tab-2"})
        self.assertEqual(driver.window_handles, [])

        # The input lists must not be mutated by the batch run.
        self.assertEqual(urls, ["http://u0", "http://u1", "http://u2"])

    def test_switch_failure_is_recorded_and_batch_continues(self):
        urls = ["http://u0", "http://u1", "http://u2"]
        dirs = ["download0", "download1", "download2"]
        expected_dirs = {
            url: os.path.join(self.tmpdir, d) for url, d in zip(urls, dirs)
        }

        driver = FakeDriver(initial_tabs=1)
        p = self.make_pinterest(driver)

        # Navigation happens before tab_url is populated, so this fault only
        # triggers during the crawl rounds, exactly for u1's own tab.
        driver.switch_to.fail_on = lambda handle: (
            driver.tab_url.get(handle) == "http://u1"
        )

        crawl_calls = []
        p.crawl = make_crawl(
            {u: ["ok", "ok"] for u in urls}, crawl_calls, expected_dirs
        ).__get__(p, Pinterest)

        results = p.batch_download(2, list(urls), list(dirs))
        by_url = {r["url"]: r for r in results}

        # Failed item is retained with a readable reason and zero downloads.
        self.assertEqual(by_url["http://u1"]["status"], "failed")
        self.assertEqual(by_url["http://u1"]["downloaded"], 0)
        self.assertIn("switch to tab failed", by_url["http://u1"]["reason"])
        self.assertIn("simulated switch failure", by_url["http://u1"]["reason"])

        # The other links keep running for all rounds and finish normally.
        self.assertEqual(by_url["http://u0"]["status"], "completed")
        self.assertEqual(by_url["http://u0"]["downloaded"], 2)
        self.assertEqual(by_url["http://u2"]["status"], "completed")
        self.assertEqual(by_url["http://u2"]["downloaded"], 2)

        # A failed item never closes its tab; nothing ended in this run.
        self.assertEqual(driver.closed_handles, [])
        self.assertEqual(len(results), 3)

        # The failed link's directory was never used for another link's images.
        crawled_urls = [u for u, _ in crawl_calls]
        self.assertNotIn("http://u1", crawled_urls)
        for url, dir_arg in crawl_calls:
            self.assertEqual(dir_arg, expected_dirs[url])

    def test_crawl_exception_marks_failed_and_continues(self):
        urls = ["http://u0", "http://u1", "http://u2"]
        dirs = ["download0", "download1", "download2"]
        behavior = {
            "http://u0": ["end"],
            "http://u1": RuntimeError("boom: page read failed"),
            "http://u2": ["end"],
        }

        p, driver, results, crawl_calls, expected_dirs = self.run_batch(
            urls, dirs, behavior
        )

        by_url = {r["url"]: r for r in results}
        self.assertEqual(by_url["http://u0"]["status"], "ended")
        self.assertEqual(by_url["http://u2"]["status"], "ended")

        self.assertEqual(by_url["http://u1"]["status"], "failed")
        self.assertIn("crawl failed", by_url["http://u1"]["reason"])
        self.assertIn("boom: page read failed", by_url["http://u1"]["reason"])

        # Tabs of the normally ended links are closed; the failed one stays.
        self.assertEqual(set(driver.closed_handles), {"tab-0", "tab-2"})
        self.assertEqual(len(results), 3)

    def test_page_limit_marks_remaining_items_completed(self):
        urls = ["http://u0", "http://u1"]
        dirs = ["download0", "download1"]
        behavior = {u: ["ok", "ok", "ok"] for u in urls}

        p, driver, results, crawl_calls, expected_dirs = self.run_batch(
            urls, dirs, behavior, n=2
        )

        self.assertTrue(all(r["status"] == "completed" for r in results))
        self.assertEqual([r["downloaded"] for r in results], [2, 2])
        self.assertEqual(driver.closed_handles, [])

    def test_mismatched_lengths_exit(self):
        driver = FakeDriver(initial_tabs=1)
        p = self.make_pinterest(driver)
        with self.assertRaises(SystemExit):
            p.batch_download(1, ["http://u0", "http://u1"], ["download0"])

    def test_empty_url_list_returns_empty_result(self):
        driver = FakeDriver(initial_tabs=1)
        p = self.make_pinterest(driver)
        self.assertEqual(p.batch_download(1, [], []), [])
        # No extra tabs opened.
        self.assertEqual(driver.window_handles, ["tab-0"])

    def test_result_contains_link_dir_status_count_and_reason(self):
        urls = ["http://u0", "http://u1"]
        dirs = ["download0", "download1"]
        behavior = {"http://u0": ["end"], "http://u1": RuntimeError("read error")}

        p, driver, results, crawl_calls, expected_dirs = self.run_batch(
            urls, dirs, behavior
        )

        for r in results:
            self.assertIn("url", r)
            self.assertIn("dir", r)
            self.assertIn("status", r)
            self.assertIn("downloaded", r)
            self.assertIn("reason", r)
        self.assertEqual(results[0]["url"], "http://u0")
        self.assertEqual(results[0]["dir"], expected_dirs["http://u0"])
        self.assertEqual(results[0]["status"], "ended")
        self.assertEqual(results[1]["status"], "failed")
        self.assertIn("read error", results[1]["reason"])

    def test_single_download_behavior_unchanged_at_end_of_page(self):
        # Single-link mode must still stop cleanly on EndPageException and
        # return its fail-image count (0).
        driver = FakeDriver(initial_tabs=1)
        p = self.make_pinterest(driver)

        def crawl_ends(dir_arg):
            raise EndPageException()

        p.crawl = crawl_ends
        fail_image = p.single_download(3, "http://single", "single_dir")

        self.assertEqual(fail_image, 0)
        self.assertEqual(driver.get_calls, [("tab-0", "http://single")])
        self.assertTrue(os.path.isdir(os.path.join(self.tmpdir, "single_dir")))

    def test_end_page_exception_can_be_raised_without_message(self):
        exc = EndPageException()
        self.assertEqual(str(exc), "End of page")
        with self.assertRaises(EndPageException):
            raise EndPageException()


if __name__ == "__main__":
    unittest.main()
