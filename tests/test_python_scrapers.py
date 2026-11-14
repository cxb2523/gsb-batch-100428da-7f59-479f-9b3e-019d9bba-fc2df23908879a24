"""Acceptance tests for the Python Google Maps scrapers.

Two layers:

1. Pure unit tests for the shared logic (scroll stall detection, rating /
   review extraction, fixed-header CSV, zero-result reporting) - these need
   no browser and always run.
2. Browser integration tests against the local fixture page
   (``tests/fixtures/fixture.html``) covering the four required scenarios:
   normal multi-card, partial missing fields, zero results and an
   early-bottom feed.  Both the Selenium and the Playwright drivers run the
   same scenarios.

Run with:  python -I -m unittest tests.test_python_scrapers -v
"""

from __future__ import annotations

import csv
import io
import os
import sys
import unittest
from contextlib import redirect_stdout

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON_DIR = os.path.join(REPO_ROOT, "Python")
FIXTURE_URL = (
    "file:///"
    + os.path.join(REPO_ROOT, "tests", "fixtures", "fixture.html")
    .replace("\\", "/")
)

sys.path.insert(0, PYTHON_DIR)

from maps_common import (  # noqa: E402
    CSV_HEADER,
    extract_rating,
    extract_reviews,
    report,
    scroll_feed,
    write_csv,
)


class ScrollStallTests(unittest.TestCase):
    def test_stops_after_two_stalled_rounds(self):
        # Heights grow for the first two rounds, then freeze.
        heights = [100, 200, 300, 300, 300, 300]
        index = {"i": 0}

        def get_height():
            value = heights[min(index["i"], len(heights) - 1)]
            return value

        def scroll_once():
            index["i"] += 1

        scrolls, reason = scroll_feed(scroll_once, get_height, max_scrolls=10, scroll_pause=0)
        self.assertEqual(reason, "reached_bottom")
        # round 1 -> 200 (growth), round 2 -> 300 (growth),
        # round 3 -> 300 (stall 1), round 4 -> 300 (stall 2 => stop)
        self.assertEqual(scrolls, 4)

    def test_reaches_max_scrolls_when_panel_keeps_growing(self):
        heights = iter(range(100, 1000, 100))

        scrolls, reason = scroll_feed(
            lambda: None, lambda: next(heights), max_scrolls=5, scroll_pause=0
        )
        self.assertEqual(scrolls, 5)
        self.assertEqual(reason, "max_scrolls_reached")

    def test_empty_feed_stops_early(self):
        # Height never changes at all: two rounds, then bottom.
        scrolls, reason = scroll_feed(lambda: None, lambda: 0, max_scrolls=10, scroll_pause=0)
        self.assertEqual(scrolls, 2)
        self.assertEqual(reason, "reached_bottom")

    def test_single_recovery_resets_stall_counter(self):
        heights = [100, 100, 200, 200, 200]
        index = {"i": 0}

        def get_height():
            return heights[min(index["i"], len(heights) - 1)]

        scrolls, reason = scroll_feed(
            lambda: index.__setitem__("i", index["i"] + 1),
            get_height,
            max_scrolls=10,
            scroll_pause=0,
        )
        # stall at round 1, growth at round 2 resets, stalls at 3 and 4 stop.
        self.assertEqual(scrolls, 4)
        self.assertEqual(reason, "reached_bottom")


class ExtractionTests(unittest.TestCase):
    def test_rating_from_aria_label(self):
        self.assertEqual(extract_rating("Rated 4.6 out of 5 stars"), "4.6")
        self.assertEqual(extract_rating("3.9 stars"), "3.9")
        self.assertEqual(extract_rating(""), "")
        self.assertEqual(extract_rating(None), "")
        self.assertEqual(extract_rating("no digits here"), "")

    def test_reviews_strip_thousands_separators(self):
        self.assertEqual(extract_reviews("(1,234)"), "1234")
        self.assertEqual(extract_reviews("12,000 reviews"), "12000")
        self.assertEqual(extract_reviews("56"), "56")
        self.assertEqual(extract_reviews(""), "")
        self.assertEqual(extract_reviews(None), "")


class CsvTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = os.path.join(REPO_ROOT, "tests", "tmp")
        os.makedirs(self.tmp_dir, exist_ok=True)
        self.path = os.path.join(self.tmp_dir, "csv_unittest.csv")

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_zero_records_writes_header_only_csv(self):
        write_csv(self.path, [])
        with io.open(self.path, encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], CSV_HEADER)

    def test_columns_are_kept_when_fields_missing(self):
        write_csv(self.path, [{"Name": "Bare", "Rating": ""}])
        with io.open(self.path, encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(reader.fieldnames, CSV_HEADER)
            row = next(reader)
            self.assertEqual(row["Name"], "Bare")
            for column in CSV_HEADER[1:]:
                self.assertEqual(row[column], "")

    def test_quotes_and_commas_are_escaped(self):
        write_csv(
            self.path,
            [{"Name": 'Joe "The Chef"', "Services": "Dine-in, Delivery"}],
        )
        with io.open(self.path, encoding="utf-8-sig", newline="") as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual(row["Name"], 'Joe "The Chef"')
        self.assertEqual(row["Services"], "Dine-in, Delivery")


class ReportTests(unittest.TestCase):
    def test_zero_result_report_does_not_claim_success(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            report([], "out.csv", 2, "reached_bottom")
        output = buffer.getvalue()
        self.assertIn("0 records", output)
        self.assertIn("reached bottom", output)
        self.assertNotIn("Saved 0", output)

    def test_nonzero_report_shows_count_and_reason(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            report([{"Name": "a"}], "out.csv", 10, "max_scrolls_reached")
        output = buffer.getvalue()
        self.assertIn("Saved 1 records", output)
        self.assertIn("max_scrolls", output)


# ---------------------------------------------------------------------------
# Browser integration tests (skipped automatically if a driver/browser is
# unavailable in the environment).
# ---------------------------------------------------------------------------

EXPECTED_NORMAL_RECORDS = 3


def _fixture(scenario):
    return f"{FIXTURE_URL}#{scenario}"


class BrowserScenarioMixin:
    """Shared scenario expectations; subclasses supply run()."""

    def run_scraper(self, scenario, output_path, max_scrolls=6, scroll_pause=0.2):
        raise NotImplementedError

    def _output_rows(self, path):
        with io.open(path, encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def scenario_normal(self, output_path):
        records, scrolls, reason = self.run_scraper("normal", output_path)
        self.assertEqual(len(records), EXPECTED_NORMAL_RECORDS)
        rows = self._output_rows(output_path)
        self.assertEqual(len(rows), EXPECTED_NORMAL_RECORDS)
        self.assertEqual(rows[0]["Name"], "Alpha Bistro")
        self.assertEqual(rows[0]["Rating"], "4.6")
        self.assertEqual(rows[0]["Reviews"], "1234")
        self.assertEqual(rows[0]["Category"], "Restaurant")
        self.assertEqual(rows[0]["Services"], "Dine-in, Delivery")
        self.assertIn("googleusercontent", rows[0]["Image"])
        self.assertTrue(rows[0]["Detail URL"].startswith("https://"))
        # A static feed cannot grow: two stalled rounds end the loop early.
        self.assertEqual(reason, "reached_bottom")
        self.assertLess(scrolls, 6)

    def scenario_missing_fields(self, output_path):
        records, _, _ = self.run_scraper("missing", output_path)
        self.assertEqual(len(records), 3)
        rows = self._output_rows(output_path)
        with io.open(output_path, encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(reader.fieldnames, CSV_HEADER)
        # First card: no rating and no image -> empty strings, run still succeeds.
        self.assertEqual(rows[0]["Name"], "No Rating Diner")
        self.assertEqual(rows[0]["Rating"], "")
        self.assertEqual(rows[0]["Image"], "")
        self.assertEqual(rows[0]["Reviews"], "89")
        # Second card has no name/category/reviews/detail URL.
        self.assertEqual(rows[1]["Name"], "")
        self.assertEqual(rows[1]["Detail URL"], "")
        self.assertEqual(rows[1]["Rating"], "4.0")
        # Third card is almost entirely empty but all seven columns remain.
        self.assertEqual(rows[2]["Name"], "Bare Bones")
        for column in ("Rating", "Reviews", "Category", "Services", "Image", "Detail URL"):
            self.assertEqual(rows[2][column], "")

    def scenario_zero_results(self, output_path):
        records, scrolls, reason = self.run_scraper("empty", output_path)
        self.assertEqual(records, [])
        self.assertEqual(reason, "reached_bottom")
        self.assertEqual(scrolls, 2)
        with io.open(output_path, encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], CSV_HEADER)

    def scenario_early_bottom(self, output_path):
        records, scrolls, reason = self.run_scraper("early-bottom", output_path, max_scrolls=8)
        self.assertEqual(len(records), EXPECTED_NORMAL_RECORDS)
        self.assertEqual(reason, "reached_bottom")
        # 6 configured max -> only 2 stalled rounds needed after the initial
        # height; assert it stopped well before the cap.
        self.assertLessEqual(scrolls, 3)

    def scenario_growing_then_bottom(self, output_path):
        # Lazy-loaded batches append on the first scrolls, then growth stops.
        # 4 batches x 2 cards = 8 records; the loop must keep scrolling while
        # the panel grows and stop on the two trailing stalled rounds.
        records, scrolls, reason = self.run_scraper("growing", output_path, max_scrolls=10)
        self.assertEqual(len(records), 8)
        self.assertEqual(reason, "reached_bottom")
        self.assertLess(scrolls, 10)
        rows = self._output_rows(output_path)
        self.assertEqual(rows[0]["Name"], "Growing Place 1")
        self.assertEqual(rows[-1]["Name"], "Growing Place 8")
        # Review counts are plain numbers without separators.
        self.assertEqual(rows[0]["Reviews"], "100")


@unittest.skipUnless(
    os.environ.get("RUN_BROWSER_TESTS") == "1",
    "set RUN_BROWSER_TESTS=1 to run Selenium/Playwright browser tests",
)
class SeleniumScenarioTests(BrowserScenarioMixin, unittest.TestCase):
    def setUp(self):
        from selenium_scraper import run_scraper

        self._run = run_scraper
        self.tmp_dir = os.path.join(REPO_ROOT, "tests", "tmp")
        os.makedirs(self.tmp_dir, exist_ok=True)

    def run_scraper(self, scenario, output_path, max_scrolls=6, scroll_pause=0.2):
        return self._run(
            url=_fixture(scenario),
            output_file=output_path,
            max_scrolls=max_scrolls,
            scroll_pause=scroll_pause,
            headless=True,
        )

    def test_normal(self):
        self.scenario_normal(os.path.join(self.tmp_dir, "py_selenium_normal.csv"))

    def test_missing_fields(self):
        self.scenario_missing_fields(os.path.join(self.tmp_dir, "py_selenium_missing.csv"))

    def test_zero_results(self):
        self.scenario_zero_results(os.path.join(self.tmp_dir, "py_selenium_empty.csv"))

    def test_early_bottom(self):
        self.scenario_early_bottom(os.path.join(self.tmp_dir, "py_selenium_bottom.csv"))

    def test_growing_then_bottom(self):
        self.scenario_growing_then_bottom(os.path.join(self.tmp_dir, "py_selenium_growing.csv"))


@unittest.skipUnless(
    os.environ.get("RUN_BROWSER_TESTS") == "1",
    "set RUN_BROWSER_TESTS=1 to run Selenium/Playwright browser tests",
)
class PlaywrightScenarioTests(BrowserScenarioMixin, unittest.TestCase):
    def setUp(self):
        from playwright_scraper import run_scraper

        self._run = run_scraper
        self.tmp_dir = os.path.join(REPO_ROOT, "tests", "tmp")
        os.makedirs(self.tmp_dir, exist_ok=True)

    def run_scraper(self, scenario, output_path, max_scrolls=6, scroll_pause=0.2):
        return self._run(
            url=_fixture(scenario),
            output_file=output_path,
            max_scrolls=max_scrolls,
            scroll_pause=scroll_pause,
            headless=True,
            channel=os.environ.get("MAPS_BROWSER_CHANNEL") or None,
        )

    def test_normal(self):
        self.scenario_normal(os.path.join(self.tmp_dir, "py_playwright_normal.csv"))

    def test_missing_fields(self):
        self.scenario_missing_fields(os.path.join(self.tmp_dir, "py_playwright_missing.csv"))

    def test_zero_results(self):
        self.scenario_zero_results(os.path.join(self.tmp_dir, "py_playwright_empty.csv"))

    def test_early_bottom(self):
        self.scenario_early_bottom(os.path.join(self.tmp_dir, "py_playwright_bottom.csv"))

    def test_growing_then_bottom(self):
        self.scenario_growing_then_bottom(os.path.join(self.tmp_dir, "py_playwright_growing.csv"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
