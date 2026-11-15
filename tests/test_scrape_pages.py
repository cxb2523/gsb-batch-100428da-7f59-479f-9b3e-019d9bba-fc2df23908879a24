"""Tests for the failure-isolating / resumable ScrapePagesTask.

Run with:  python -m unittest discover -s tests -v
"""

import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.scrape_pages import (  # noqa: E402
    FAILURE_KEY,
    REASON_EXTRACT_DATA,
    REASON_OPEN_PAGE,
    STAGE_EXTRACT_DATA,
    STAGE_OPEN_PAGE,
    SUCCESS_KEY,
    ScrapePagesTask,
)


class MemoryStorage:
    """Minimal stand-in for bose's LocalStorage backed by a dict."""

    def __init__(self, initial=None):
        self.data = dict(initial or {})

    def get_item(self, key, default=None):
        return self.data.get(key, default)

    def set_item(self, key, value):
        self.data[key] = value


class FakeDriver:
    def __init__(self, open_errors=None, extract_errors=None, session_error=None):
        self.open_errors = open_errors or {}
        self.extract_errors = extract_errors or {}
        self.session_error = session_error
        self.opened = []
        self.session_prepared = 0

    def organic_get(self, url):
        self.session_prepared += 1
        if self.session_error is not None:
            raise self.session_error

    def get_by_current_page_referrer(self, link):
        self.opened.append(link)
        if link in self.open_errors:
            raise self.open_errors[link]

    def extract(self, link):
        if link in self.extract_errors:
            raise self.extract_errors[link]
        return {"link": link, "title": "Title of " + link}


class Task(ScrapePagesTask):
    def __init__(self, storage, driver):
        super().__init__()
        self._storage = storage
        self._driver = driver

    def get_storage(self):
        return self._storage

    def extract_page_data(self, driver, link):
        return driver.extract(link)


class EmptyLinksTests(unittest.TestCase):
    def test_get_data_skips_task_when_links_missing(self):
        task = Task(MemoryStorage(), FakeDriver())
        self.assertEqual(task.get_data(), [])

    def test_get_data_skips_task_when_links_empty(self):
        task = Task(MemoryStorage({"links": []}), FakeDriver())
        self.assertEqual(task.get_data(), [])

    def test_get_data_skips_task_for_unusable_links_value(self):
        task = Task(MemoryStorage({"links": None}), FakeDriver())
        self.assertEqual(task.get_data(), [])

    def test_run_returns_empty_result_without_opening_pages(self):
        driver = FakeDriver()
        storage = MemoryStorage({"links": []})
        result = Task(storage, driver).run(driver, [])
        self.assertEqual(result, [])
        self.assertEqual(driver.opened, [])
        self.assertEqual(storage.get_item(SUCCESS_KEY, []), [])
        self.assertEqual(storage.get_item(FAILURE_KEY, []), [])

    def test_run_with_missing_links_key_returns_empty_result(self):
        driver = FakeDriver()
        storage = MemoryStorage()
        result = Task(storage, driver).run(
            driver, storage.get_item("links", [])
        )
        self.assertEqual(result, [])
        self.assertEqual(driver.opened, [])


class FailureIsolationTests(unittest.TestCase):
    def setUp(self):
        self.storage = MemoryStorage()
        self.driver = FakeDriver(
            open_errors={"https://example.test/broken-open": RuntimeError("boom-open")},
            extract_errors={
                "https://example.test/broken-extract": ValueError("boom-extract")
            },
        )
        self.links = [
            "https://example.test/good-1",
            "https://example.test/broken-open",
            "https://example.test/good-2",
            "https://example.test/broken-extract",
            "https://example.test/good-3",
        ]
        self.storage.set_item("links", self.links)

    def test_single_failures_do_not_abort_the_round(self):
        result = Task(self.storage, self.driver).run(self.driver, self.links)

        # Every link was attempted, including the ones after the failures.
        self.assertEqual(self.driver.opened, self.links)

        # Successful entries are returned.
        self.assertEqual(
            result,
            [
                {"link": "https://example.test/good-1", "title": "Title of https://example.test/good-1"},
                {"link": "https://example.test/good-2", "title": "Title of https://example.test/good-2"},
                {"link": "https://example.test/good-3", "title": "Title of https://example.test/good-3"},
            ],
        )

    def test_failures_record_link_reason_position_and_original_error(self):
        Task(self.storage, self.driver).run(self.driver, self.links)
        failures = self.storage.get_item(FAILURE_KEY)

        self.assertEqual(len(failures), 2)
        by_link = {failure["link"]: failure for failure in failures}

        open_failure = by_link["https://example.test/broken-open"]
        self.assertEqual(open_failure["stage"], STAGE_OPEN_PAGE)
        self.assertEqual(open_failure["reason"], REASON_OPEN_PAGE)
        self.assertEqual(open_failure["index"], 1)
        self.assertEqual(open_failure["run_index"], 1)
        self.assertEqual(open_failure["error_type"], "RuntimeError")
        self.assertEqual(open_failure["error_message"], "boom-open")
        self.assertIn("RuntimeError: boom-open", open_failure["traceback"])

        extract_failure = by_link["https://example.test/broken-extract"]
        self.assertEqual(extract_failure["stage"], STAGE_EXTRACT_DATA)
        self.assertEqual(extract_failure["reason"], REASON_EXTRACT_DATA)
        self.assertEqual(extract_failure["index"], 3)
        self.assertEqual(extract_failure["run_index"], 3)
        self.assertEqual(extract_failure["error_type"], "ValueError")
        self.assertEqual(extract_failure["error_message"], "boom-extract")
        self.assertIn("ValueError: boom-extract", extract_failure["traceback"])

    def test_successes_are_persisted_with_link_and_position(self):
        Task(self.storage, self.driver).run(self.driver, self.links)
        successes = self.storage.get_item(SUCCESS_KEY)

        self.assertEqual(
            [entry["link"] for entry in successes],
            [
                "https://example.test/good-1",
                "https://example.test/good-2",
                "https://example.test/good-3",
            ],
        )
        self.assertEqual([entry["index"] for entry in successes], [0, 2, 4])
        self.assertEqual(successes[0]["data"]["title"], "Title of https://example.test/good-1")


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.storage = MemoryStorage()
        self.links = [
            "https://example.test/good-1",
            "https://example.test/flaky",
            "https://example.test/good-2",
        ]
        self.storage.set_item("links", self.links)

    def test_only_failed_links_are_retried(self):
        first_driver = FakeDriver(
            extract_errors={"https://example.test/flaky": RuntimeError("first failure")}
        )
        Task(self.storage, first_driver).run(first_driver, self.links)
        self.assertEqual(
            self.storage.get_item(FAILURE_KEY)[0]["link"],
            "https://example.test/flaky",
        )

        second_driver = FakeDriver()
        Task(self.storage, second_driver).run(second_driver, self.links)

        # Only the previously failed link is opened on resume.
        self.assertEqual(second_driver.opened, ["https://example.test/flaky"])
        # And its success is now part of the returned result.
        self.assertEqual(
            [entry["link"] for entry in self.storage.get_item(SUCCESS_KEY)],
            self.links,
        )
        self.assertEqual(self.storage.get_item(FAILURE_KEY), [])

    def test_successful_retry_is_removed_from_failure_records(self):
        first_driver = FakeDriver(
            open_errors={
                "https://example.test/flaky": RuntimeError("first failure"),
            }
        )
        Task(self.storage, first_driver).run(first_driver, self.links)

        second_driver = FakeDriver()
        Task(self.storage, second_driver).run(second_driver, self.links)

        self.assertEqual(self.storage.get_item(FAILURE_KEY), [])
        successes = self.storage.get_item(SUCCESS_KEY)
        flaky = [s for s in successes if s["link"] == "https://example.test/flaky"]
        self.assertEqual(len(flaky), 1)
        self.assertEqual(flaky[0]["attempts"], 2)

    def test_failure_records_accumulate_attempts_across_runs(self):
        for expected_attempts in (1, 2, 3):
            driver = FakeDriver(
                extract_errors={
                    "https://example.test/flaky": RuntimeError("still failing"),
                }
            )
            Task(self.storage, driver).run(driver, self.links)

            failures = self.storage.get_item(FAILURE_KEY)
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0]["attempts"], expected_attempts)
            self.assertEqual(failures[0]["error_message"], "still failing")
            self.assertIn("still failing", failures[0]["traceback"])

    def test_additional_failing_link_is_retried_after_older_failures(self):
        first_driver = FakeDriver(
            extract_errors={
                "https://example.test/flaky": RuntimeError("first"),
            }
        )
        Task(self.storage, first_driver).run(first_driver, self.links)

        # A new broken link is appended to the queue.
        links = self.links + ["https://example.test/new-broken"]
        self.storage.set_item("links", links)

        second_driver = FakeDriver(
            open_errors={
                "https://example.test/new-broken": RuntimeError("no page"),
            }
        )
        Task(self.storage, second_driver).run(second_driver, links)

        self.assertEqual(
            second_driver.opened,
            ["https://example.test/flaky", "https://example.test/new-broken"],
        )
        failures = self.storage.get_item(FAILURE_KEY)
        self.assertEqual(
            [failure["link"] for failure in failures],
            ["https://example.test/new-broken"],
        )
        self.assertEqual(failures[0]["index"], 3)


class SessionFailureTests(unittest.TestCase):
    def test_session_setup_failure_does_not_abort_the_round(self):
        storage = MemoryStorage()
        links = ["https://example.test/good-1", "https://example.test/good-2"]
        driver = FakeDriver(session_error=RuntimeError("auth endpoint down"))

        result = Task(storage, driver).run(driver, links)

        self.assertEqual(driver.session_prepared, 1)
        self.assertEqual(driver.opened, links)
        self.assertEqual(len(result), 2)


class DuplicateLinkTests(unittest.TestCase):
    def test_repeated_link_in_queue_is_scraped_once_and_not_duplicated(self):
        storage = MemoryStorage()
        links = [
            "https://example.test/good-1",
            "https://example.test/good-2",
            "https://example.test/good-1",
        ]
        driver = FakeDriver()

        result = Task(storage, driver).run(driver, links)

        self.assertEqual(
            driver.opened,
            ["https://example.test/good-1", "https://example.test/good-2"],
        )
        self.assertEqual(
            [entry["link"] for entry in storage.get_item(SUCCESS_KEY)],
            ["https://example.test/good-1", "https://example.test/good-2"],
        )
        self.assertEqual(len(result), 2)


class DirtyDataTests(unittest.TestCase):
    def setUp(self):
        self.storage = MemoryStorage()
        self.links = [
            "https://example.test/good-1",
            "https://example.test/good-2",
        ]
        self.storage.set_item("links", self.links)

    def test_repeated_clean_runs_do_not_duplicate_records(self):
        for run_number in range(3):
            driver = FakeDriver()
            Task(self.storage, driver).run(driver, self.links)
            if run_number > 0:
                # Nothing is re-scraped after the first successful run.
                self.assertEqual(driver.opened, [])

        successes = self.storage.get_item(SUCCESS_KEY)
        self.assertEqual([entry["link"] for entry in successes], self.links)
        self.assertEqual(self.storage.get_item(FAILURE_KEY), [])

    def test_previous_records_are_not_overwritten_by_empty_runs(self):
        driver = FakeDriver()
        Task(self.storage, driver).run(driver, self.links)

        empty_storage = MemoryStorage({"links": []})
        empty_driver = FakeDriver()
        self.assertEqual(Task(empty_storage, empty_driver).run(empty_driver, []), [])
        # With no links there is nothing to report, and nothing is written:
        # an existing history must not be wiped by an empty queue.
        self.assertEqual(empty_storage.get_item(SUCCESS_KEY, []), [])
        self.assertEqual(empty_storage.get_item(FAILURE_KEY, []), [])

        # Existing success records survive a later run untouched.
        self.assertEqual(len(self.storage.get_item(SUCCESS_KEY)), 2)

    def test_stale_success_links_are_not_retried_even_if_listed_as_failed(self):
        self.storage.set_item(
            SUCCESS_KEY,
            [{"link": self.links[0], "index": 0, "data": {"title": "cached"}}],
        )
        self.storage.set_item(
            FAILURE_KEY,
            [{"link": self.links[0], "index": 0, "stage": STAGE_OPEN_PAGE, "attempts": 1}],
        )

        driver = FakeDriver()
        result = Task(self.storage, driver).run(driver, self.links)

        self.assertEqual(driver.opened, [self.links[1]])
        self.assertEqual(self.storage.get_item(FAILURE_KEY), [])
        self.assertEqual(
            [entry["link"] for entry in self.storage.get_item(SUCCESS_KEY)],
            self.links,
        )
        self.assertEqual(len(result), 2)

    def test_corrupt_storage_values_are_ignored(self):
        self.storage.set_item(SUCCESS_KEY, "not-a-list")
        self.storage.set_item(
            FAILURE_KEY,
            [
                "garbage",
                {"no_link": True},
                {"link": "", "stage": STAGE_OPEN_PAGE},
                None,
            ],
        )

        driver = FakeDriver()
        result = Task(self.storage, driver).run(driver, self.links)

        self.assertEqual(driver.opened, self.links)
        self.assertEqual(len(result), 2)
        self.assertEqual(self.storage.get_item(FAILURE_KEY), [])
        self.assertEqual(
            [entry["link"] for entry in self.storage.get_item(SUCCESS_KEY)],
            self.links,
        )


# --------------------------------------------------------------------------
# End-to-end check against local sample pages served from a local HTTP server.
# No external network access is required.
# -------------------------------------------------------------------------------

GOOD_PAGE = "<html><body><h1>Sample Article</h1><p>Body text</p></body></html>"
BROKEN_PAGE = "<html><body><p>Nothing to extract here</p></body></html>"


class LocalSiteHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/good-1":
            body, status = GOOD_PAGE, 200
        elif self.path == "/good-2":
            body, status = GOOD_PAGE, 200
        elif self.path == "/broken-extract":
            body, status = BROKEN_PAGE, 200
        elif self.path == "/broken-open":
            body, status = "server error", 500
        else:
            body, status = "not found", 404

        status_text = "OK" if status == 200 else "ERROR"
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(f"{status_text}\n{body}".encode("utf-8"))

    def log_message(self, format, *args):
        pass


class FixedHandler(LocalSiteHandler):
    """Serves every path successfully, simulating a site that was fixed."""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(f"OK\n{GOOD_PAGE}".encode("utf-8"))


class HTTPDriver:
    """Tiny local-only driver: fetches the page and mimics an extraction step."""

    def __init__(self, base_url):
        self.base_url = base_url
        self.opened = []
        self.prepared = 0
        self.page = ""

    def organic_get(self, url):
        self.prepared += 1

    def get_by_current_page_referrer(self, link):
        import requests

        self.opened.append(link)
        response = requests.get(link, timeout=5)
        if response.status_code != 200 or response.text.startswith("ERROR"):
            raise RuntimeError(f"HTTP {response.status_code} while opening {link}")
        self.page = response.text

    def extract(self, link):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(self.page, "html.parser")
        heading = soup.find("h1")
        if heading is None:
            raise ValueError(f"Missing <h1> on {link}")
        return {"link": link, "title": heading.get_text(strip=True)}


class LocalSiteEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), LocalSiteHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def test_local_pages_partial_success(self):
        links = [
            f"{self.base_url}/good-1",
            f"{self.base_url}/broken-open",
            f"{self.base_url}/broken-extract",
            f"{self.base_url}/good-2",
        ]
        storage = MemoryStorage({"links": links})
        task = Task(storage, HTTPDriver(self.base_url))

        result = task.run(task._driver, links)

        self.assertEqual(
            result,
            [
                {"link": links[0], "title": "Sample Article"},
                {"link": links[3], "title": "Sample Article"},
            ],
        )
        failures = storage.get_item(FAILURE_KEY)
        self.assertEqual(
            [(failure["link"], failure["stage"]) for failure in failures],
            [
                (links[1], STAGE_OPEN_PAGE),
                (links[2], STAGE_EXTRACT_DATA),
            ],
        )
        self.assertEqual(
            [failure["index"] for failure in failures], [1, 2]
        )

    def test_resume_retries_only_failed_links(self):
        links = [
            f"{self.base_url}/good-1",
            f"{self.base_url}/broken-open",
            f"{self.base_url}/good-2",
        ]
        storage = MemoryStorage({"links": links})
        first = Task(storage, HTTPDriver(self.base_url))
        first.run(first._driver, links)
        self.assertEqual(len(storage.get_item(FAILURE_KEY)), 1)

        # The local endpoint now serves that URL successfully.
        fixed_server = HTTPServer(("127.0.0.1", 0), FixedHandler)
        fixed_thread = threading.Thread(
            target=fixed_server.serve_forever, daemon=True
        )
        fixed_thread.start()
        fixed_base = f"http://127.0.0.1:{fixed_server.server_port}"

        try:
            class RedirectingDriver(HTTPDriver):
                def get_by_current_page_referrer(self, link):
                    # Only the origin changes; the path (and therefore the
                    # queue entry) stays exactly as recorded.
                    self.opened.append(link)
                    import requests

                    response = requests.get(
                        fixed_base + "/" + link.rsplit("/", 1)[-1], timeout=5
                    )
                    if response.status_code != 200:
                        raise RuntimeError(
                            f"HTTP {response.status_code} while opening {link}"
                        )
                    self.page = response.text

            retry_task = Task(storage, RedirectingDriver(fixed_base))
            retry_driver = retry_task._driver
            result = retry_task.run(retry_driver, links)
        finally:
            fixed_server.shutdown()
            fixed_server.server_close()
            fixed_thread.join(timeout=5)

        # Only the previously failed link was opened again.
        self.assertEqual(retry_driver.opened, [links[1]])
        self.assertEqual(storage.get_item(FAILURE_KEY), [])
        self.assertEqual(
            [entry["link"] for entry in storage.get_item(SUCCESS_KEY)], links
        )
        self.assertEqual(len(result), 3)


if __name__ == "__main__":
    unittest.main()
