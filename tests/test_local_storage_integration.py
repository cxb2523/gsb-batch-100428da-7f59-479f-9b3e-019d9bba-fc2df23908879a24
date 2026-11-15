"""Integration tests for ScrapePagesTask against the real bose LocalStorage.

Unlike ``test_scrape_pages.py`` (which uses an in-memory storage stub), these
tests write an actual ``local_storage.json`` on disk, in a temporary working
directory. They verify that repeated runs keep that file consistent.

Run with:  python -m unittest discover -s tests -v
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from bose import LocalStorage  # noqa: E402
from src.scrape_pages import (  # noqa: E402
    FAILURE_KEY,
    STAGE_EXTRACT_DATA,
    STAGE_OPEN_PAGE,
    SUCCESS_KEY,
    ScrapePagesTask,
)


class Driver:
    def __init__(self, open_fail=(), extract_fail=(), session_fail=False):
        self.open_fail = set(open_fail)
        self.extract_fail = set(extract_fail)
        self.session_fail = session_fail
        self.opened = []

    def organic_get(self, url):
        if self.session_fail:
            raise RuntimeError("session endpoint unreachable")

    def get_by_current_page_referrer(self, link):
        self.opened.append(link)
        if link in self.open_fail:
            raise TimeoutError(f"page never loaded: {link}")


class Task(ScrapePagesTask):
    def __init__(self, driver):
        super().__init__()
        self.driver = driver

    def extract_page_data(self, driver, link):
        if link in driver.extract_fail:
            raise KeyError(f"missing price on {link}")
        return {"link": link, "title": link.rsplit("/", 1)[-1].upper()}


class RealLocalStorageTests(unittest.TestCase):
    def setUp(self):
        self.original_cwd = os.getcwd()
        self.workdir = tempfile.mkdtemp(prefix="scrape_pages_storage_")
        os.chdir(self.workdir)
        # Pick up local_storage.json in the temporary working directory.
        LocalStorage.refresh()

        self.links = [
            "https://example.test/a",
            "https://example.test/b-open-fails",
            "https://example.test/c-extract-fails",
            "https://example.test/d",
        ]

    def tearDown(self):
        os.chdir(self.original_cwd)
        shutil.rmtree(self.workdir, ignore_errors=True)
        # Point the shared LocalStorage singleton back at the repository.
        LocalStorage.refresh()

    def read_storage_file(self):
        with open("local_storage.json", encoding="utf-8") as file:
            return json.load(file)

    def run_task(self, driver):
        task = Task(driver)
        return task.run(driver, self.links)

    def test_full_lifecycle_keeps_storage_file_consistent(self):
        self.assertEqual(self.read_storage_file(), {})

        # Run 1: two links fail, everything else succeeds.
        LocalStorage.set_item("links", self.links)
        driver = Driver(
            open_fail={self.links[1]},
            extract_fail={self.links[2]},
            session_fail=True,
        )
        result = self.run_task(driver)

        self.assertEqual([item["link"] for item in result], [self.links[0], self.links[3]])
        self.assertEqual(driver.opened, self.links)

        data = self.read_storage_file()
        self.assertEqual(
            [entry["link"] for entry in data[SUCCESS_KEY]],
            [self.links[0], self.links[3]],
        )
        self.assertEqual(
            [(failure["link"], failure["stage"], failure["index"])
             for failure in data[FAILURE_KEY]],
            [
                (self.links[1], STAGE_OPEN_PAGE, 1),
                (self.links[2], STAGE_EXTRACT_DATA, 2),
            ],
        )
        self.assertEqual(data[FAILURE_KEY][0]["error_type"], "TimeoutError")
        self.assertEqual(data[FAILURE_KEY][1]["error_type"], "KeyError")
        self.assertIn("Traceback", data[FAILURE_KEY][0]["traceback"])

        # Run 2: still broken. Only failed links are retried, attempts grow.
        driver_2 = Driver(open_fail={self.links[1]}, extract_fail={self.links[2]})
        self.run_task(driver_2)

        self.assertEqual(driver_2.opened, [self.links[1], self.links[2]])
        data = self.read_storage_file()
        self.assertEqual(
            [failure["attempts"] for failure in data[FAILURE_KEY]], [2, 2]
        )
        self.assertEqual(len(data[SUCCESS_KEY]), 2)

        # Run 3: the site is fixed. Retried links now succeed and leave the
        # failure records; nothing is scraped twice.
        driver_3 = Driver()
        result_3 = self.run_task(driver_3)

        self.assertEqual(driver_3.opened, [self.links[1], self.links[2]])
        data = self.read_storage_file()
        self.assertEqual(data[FAILURE_KEY], [])
        self.assertEqual(
            [entry["link"] for entry in data[SUCCESS_KEY]], self.links
        )
        self.assertEqual(
            [entry["index"] for entry in data[SUCCESS_KEY]], [0, 1, 2, 3]
        )
        self.assertEqual(
            [entry["attempts"] for entry in data[SUCCESS_KEY]], [1, 3, 3, 1]
        )
        self.assertEqual(
            result_3, [entry["data"] for entry in data[SUCCESS_KEY]]
        )

        # Run 4: nothing left to do; the file stays exactly as it was.
        driver_4 = Driver()
        result_4 = self.run_task(driver_4)

        self.assertEqual(driver_4.opened, [])
        self.assertEqual(len(self.read_storage_file()[SUCCESS_KEY]), 4)
        self.assertEqual(result_4, [entry["data"] for entry in data[SUCCESS_KEY]])

    def test_empty_queue_does_not_touch_existing_history(self):
        LocalStorage.set_item("links", [self.links[0]])
        self.run_task(Driver())
        before = self.read_storage_file()

        self.assertEqual(Task(Driver()).run(Driver(), []), [])
        self.assertEqual(self.read_storage_file(), before)


if __name__ == "__main__":
    unittest.main()
