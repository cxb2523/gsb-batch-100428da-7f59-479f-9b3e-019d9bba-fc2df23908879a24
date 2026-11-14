"""
Tests for the failure-aware pagination logic in consistentScraper.py.

Network access is fully mocked: these tests exercise the retry loop, the
tri-state page classification (ok / empty / failed), the consecutive-empty
stop condition, and the multi-run termination reasons without sending any
real HTTP requests.

Run (from this directory):
    python -I -m unittest test_consistent_scraper -v
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests

import consistentScraper as cs


PRODUCT_HTML = """
<div class="MjjYud">
  <div class="Ez5pwe">
    <div data-ved="ved-1"></div>
    <div class="gkQHve SsM98d RmEs5b">Test Headset</div>
    <div class="lmQWe">$9.99</div>
  </div>
</div>
"""


def make_response(text="", content=None):
    resp = mock.Mock()
    resp.text = text
    resp.content = content if content is not None else text.encode("utf-8")
    return resp


def http_error(status=500):
    return requests.HTTPError(f"{status} Server Error")


class RetryWrapperTests(unittest.TestCase):
    def setUp(self):
        self.sleep_patcher = mock.patch.object(cs.time, "sleep")
        self.sleep_patcher.start()
        self.addCleanup(self.sleep_patcher.stop)

    def test_success_first_attempt_no_retry(self):
        fetch = mock.Mock(return_value="ok")
        with mock.patch.object(cs, "MAX_RETRIES", 2):
            result, err = cs.request_with_retries("label", "u", mock.Mock(), fetch)
        self.assertEqual(result, "ok")
        self.assertIsNone(err)
        self.assertEqual(fetch.call_count, 1)

    def test_retries_then_succeeds(self):
        fetch = mock.Mock(side_effect=[http_error(502), http_error(502), "ok"])
        with mock.patch.object(cs, "MAX_RETRIES", 2):
            result, err = cs.request_with_retries("label", "u", mock.Mock(), fetch)
        self.assertEqual(result, "ok")
        self.assertIsNone(err)
        self.assertEqual(fetch.call_count, 3)  # 1 initial + 2 retries
        # PAUSE_SECONDS wait happens between attempts only
        self.assertEqual(cs.time.sleep.call_count, 2)

    def test_exhausted_retries_returns_error(self):
        fetch = mock.Mock(side_effect=http_error(503))
        with mock.patch.object(cs, "MAX_RETRIES", 2):
            result, err = cs.request_with_retries("label", "u", mock.Mock(), fetch)
        self.assertIsNone(result)
        self.assertIsInstance(err, requests.HTTPError)
        self.assertEqual(fetch.call_count, 3)

    def test_non_request_exception_is_not_retried(self):
        # Configuration/parsing bugs must not be hidden by the retry loop.
        fetch = mock.Mock(side_effect=ValueError("boom"))
        with mock.patch.object(cs, "MAX_RETRIES", 2):
            with self.assertRaises(ValueError):
                cs.request_with_retries("label", "u", mock.Mock(), fetch)
        self.assertEqual(fetch.call_count, 1)


class FetchResultPageClassificationTests(unittest.TestCase):
    """Tri-state classification with the two retry wrappers stubbed."""

    def _patch_paths(self, async_outcome, plain_outcome):
        """Each outcome is a (value, error) tuple returned by the retry wrapper."""
        async_mock = mock.Mock(return_value=async_outcome)
        plain_mock = mock.Mock(return_value=plain_outcome)
        return (
            mock.patch.object(cs, "_fetch_async_with_retry", async_mock),
            mock.patch.object(cs, "_fetch_plain_with_retry", plain_mock),
            plain_mock,
        )

    def test_async_products_short_circuits_to_ok(self):
        products = [{"title": "A", "price": "$1"}]
        p1, p2, plain_mock = self._patch_paths((["<html>"], None), (None, None))
        with p1, p2, mock.patch.object(cs, "parse_async_batch", return_value=(products, 1)):
            page = cs.fetch_result_page(mock.Mock(), None, None, None, None, 10)
        self.assertEqual(page["status"], "ok")
        self.assertEqual(page["products"], products)
        self.assertEqual(page["start"], 10)
        plain_mock.assert_not_called()  # no fallback needed

    def test_both_paths_succeed_empty_is_empty(self):
        p1, p2, _ = self._patch_paths(([""], None), (([], 0), None))
        with p1, p2, mock.patch.object(cs, "parse_async_batch", return_value=([], 0)):
            page = cs.fetch_result_page(mock.Mock(), None, None, None, None, 0)
        self.assertEqual(page["status"], "empty")
        self.assertEqual(page["page_total"], 0)
        self.assertIsNone(page["async_error"])
        self.assertIsNone(page["fallback_error"])

    def test_both_paths_fail_is_failed_with_both_errors(self):
        err = http_error(500)
        p1, p2, _ = self._patch_paths((None, err), (None, err))
        with p1, p2:
            page = cs.fetch_result_page(mock.Mock(), None, None, None, None, 30)
        self.assertEqual(page["status"], "failed")
        self.assertEqual(page["start"], 30)
        self.assertIn("HTTPError", page["async_error"])
        self.assertIn("HTTPError", page["fallback_error"])

    def test_async_fails_fallback_recovers_products_is_ok(self):
        err = http_error(500)
        products = [{"title": "B", "price": "$2"}]
        p1, p2, _ = self._patch_paths((None, err), ((products, 1), None))
        with p1, p2:
            page = cs.fetch_result_page(mock.Mock(), None, None, None, None, 0)
        self.assertEqual(page["status"], "ok")
        self.assertEqual(page["products"], products)
        self.assertIn("plain fallback", page["note"])
        self.assertIn("HTTPError", page["async_error"])

    def test_async_fails_fallback_succeeds_empty_is_conservatively_failed(self):
        # We never got a successful full-slice response for this offset, so an
        # empty fallback must not be treated as a confirmed-empty page.
        err = http_error(500)
        p1, p2, _ = self._patch_paths((None, err), (([], 0), None))
        with p1, p2:
            page = cs.fetch_result_page(mock.Mock(), None, None, None, None, 20)
        self.assertEqual(page["status"], "failed")
        self.assertEqual(page["start"], 20)
        self.assertIn("HTTPError", page["async_error"])
        self.assertIsNone(page["fallback_error"])

    def test_async_empty_fallback_fails_is_failed(self):
        err = http_error(502)
        p1, p2, _ = self._patch_paths(([""], None), (None, err))
        with p1, p2, mock.patch.object(cs, "parse_async_batch", return_value=([], 0)):
            page = cs.fetch_result_page(mock.Mock(), None, None, None, None, 0)
        self.assertEqual(page["status"], "failed")
        self.assertIsNone(page["async_error"])
        self.assertIn("HTTPError", page["fallback_error"])


class FetchResultPageEndToEndTests(unittest.TestCase):
    """Drive the real fetch/parse functions with scrape_do mocked."""

    def setUp(self):
        self.sleep_patcher = mock.patch.object(cs.time, "sleep")
        self.sleep_patcher.start()
        self.addCleanup(self.sleep_patcher.stop)

    def test_real_async_html_returns_products(self):
        def fake_scrape_do(url, session):
            return make_response(PRODUCT_HTML)

        with mock.patch.object(cs, "MAX_RETRIES", 2), \
             mock.patch.object(cs, "scrape_do", side_effect=fake_scrape_do):
            page = cs.fetch_result_page(mock.Mock(), None, None, None, None, 0)

        self.assertEqual(page["status"], "ok")
        self.assertEqual(page["page_total"], 1)
        self.assertEqual(page["products"][0]["title"], "Test Headset")

    def test_empty_responses_classified_as_empty(self):
        with mock.patch.object(cs, "MAX_RETRIES", 2), \
             mock.patch.object(cs, "scrape_do", return_value=make_response("")):
            page = cs.fetch_result_page(mock.Mock(), None, None, None, None, 0)
        self.assertEqual(page["status"], "empty")

    def test_http_errors_retried_then_classified_failed(self):
        calls = []

        def fake_scrape_do(url, session):
            calls.append(url)
            raise http_error(429)

        with mock.patch.object(cs, "MAX_RETRIES", 2), \
             mock.patch.object(cs, "scrape_do", side_effect=fake_scrape_do):
            page = cs.fetch_result_page(mock.Mock(), None, None, None, None, 40)

        self.assertEqual(page["status"], "failed")
        self.assertEqual(page["start"], 40)
        # async path (3 attempts) + plain fallback path (3 attempts)
        self.assertEqual(len(calls), 6)
        self.assertIn("HTTPError", page["async_error"])
        self.assertIn("HTTPError", page["fallback_error"])


class RunExtractionTests(unittest.TestCase):
    def setUp(self):
        self.sleep_patcher = mock.patch.object(cs.time, "sleep")
        self.sleep_patcher.start()
        self.addCleanup(self.sleep_patcher.stop)
        # Initial token page: empty HTML is enough for token extraction.
        self.scrape_patcher = mock.patch.object(
            cs, "scrape_do", return_value=make_response("", b"")
        )
        self.scrape_patcher.start()
        self.addCleanup(self.scrape_patcher.stop)

    @staticmethod
    def _page(status, start, products=None):
        return {
            "status": status,
            "products": products or [],
            "page_total": len(products) if products else 0,
            "start": start,
            "async_error": "HTTPError()" if status == "failed" else None,
            "fallback_error": None,
            "note": "test",
        }

    def test_failed_page_does_not_count_as_empty_and_pagination_continues(self):
        product = {"title": "P", "price": "$1"}
        scripted = [
            self._page("ok", 0, [product]),
            self._page("failed", 10),
            self._page("empty", 20),
            self._page("empty", 30),
            self._page("empty", 40),
        ]
        fetch_mock = mock.Mock(side_effect=scripted)
        with mock.patch.object(cs, "MAX_PAGES", 99), \
             mock.patch.object(cs, "PAGE_SIZE", 10), \
             mock.patch.object(cs, "EMPTY_PAGE_LIMIT", 3), \
             mock.patch.object(cs, "fetch_result_page", fetch_mock):
            pass_products, new_count, failed_pages = cs.run_extraction(mock.Mock())

        # Stopped exactly after the third confirmed-empty page (the failed one
        # neither incremented the counter nor aborted the run).
        self.assertEqual(fetch_mock.call_count, 5)
        self.assertEqual(new_count, 1)
        self.assertEqual(len(failed_pages), 1)
        self.assertEqual(failed_pages[0]["start"], 10)
        self.assertEqual(failed_pages[0]["page"], 2)
        self.assertIn("HTTPError", failed_pages[0]["error"])

    def test_only_failed_pages_never_triggers_empty_stop(self):
        scripted = [self._page("failed", 0), self._page("failed", 10), self._page("failed", 20)]
        fetch_mock = mock.Mock(side_effect=scripted)
        with mock.patch.object(cs, "MAX_PAGES", 2), \
             mock.patch.object(cs, "PAGE_SIZE", 10), \
             mock.patch.object(cs, "fetch_result_page", fetch_mock):
            pass_products, new_count, failed_pages = cs.run_extraction(mock.Mock())

        self.assertEqual(new_count, 0)
        self.assertEqual(len(failed_pages), 3)
        self.assertEqual([fp["start"] for fp in failed_pages], [0, 10, 20])
        self.assertEqual(fetch_mock.call_count, 3)  # exhausted page limit, not empty stop

    def test_failed_page_between_empty_pages_breaks_consecutive_run(self):
        scripted = [
            self._page("empty", 0),
            self._page("failed", 10),
            self._page("empty", 20),
        ]
        fetch_mock = mock.Mock(side_effect=scripted)
        with mock.patch.object(cs, "MAX_PAGES", 2), \
             mock.patch.object(cs, "EMPTY_PAGE_LIMIT", 3), \
             mock.patch.object(cs, "fetch_result_page", fetch_mock):
            _, _, failed_pages = cs.run_extraction(mock.Mock())
        # Only two confirmed-empty pages were seen, so the empty-stop never fired;
        # the run ended solely because MAX_PAGES was reached.
        self.assertEqual(fetch_mock.call_count, 3)
        self.assertEqual(len(failed_pages), 1)


class MainTerminationTests(unittest.TestCase):
    def setUp(self):
        self.sleep_patcher = mock.patch.object(cs.time, "sleep")
        self.sleep_patcher.start()
        self.addCleanup(self.sleep_patcher.stop)
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        self.tmp.close()
        self.addCleanup(lambda: os.path.exists(self.tmp.name) and os.unlink(self.tmp.name))

    def _product(self, title, price):
        # No product_params -> the OAPV detail-fetch stage is skipped entirely.
        return {
            "title": title,
            "price": price,
            "link": "N/A",
            "image_url": "N/A",
            "seller_name": "N/A",
            "rating": None,
            "review_count": None,
        }

    def test_clean_run_reports_no_new_products(self):
        product = self._product("A", "$1")
        runs = [
            ({("A", "$1"): product}, 1, []),
            ({}, 0, []),
        ]
        with mock.patch.object(cs, "OUT_JSON", self.tmp.name), \
             mock.patch.object(cs, "run_extraction", side_effect=runs):
            summary = cs.main()

        self.assertEqual(summary["stop_reason"], cs.STOP_REASON_NO_NEW_PRODUCTS)
        self.assertEqual(summary["total_runs"], 2)
        self.assertEqual(summary["failed_pages"], [])
        with open(self.tmp.name, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(len(data), 1)
        # Product JSON keeps the original schema; no control metadata leaked in.
        self.assertNotIn("stop_reason", data[0])
        self.assertNotIn("failed_pages", data[0])
        self.assertNotIn("product_params", data[0])
        for key in ("title", "price", "link", "image_url", "seller_name",
                    "rating", "review_count", "brand", "description",
                    "detail_images", "reviews", "forums", "offers"):
            self.assertIn(key, data[0])

    def test_failed_final_run_reports_interrupted(self):
        product = self._product("A", "$1")
        failed = [{
            "page": 2,
            "start": 10,
            "async_error": "HTTPError('500')",
            "fallback_error": "HTTPError('502')",
            "error": "HTTPError('500')",
            "note": "both failed",
        }]
        runs = [
            ({("A", "$1"): product}, 1, []),
            ({}, 0, failed),
        ]
        with mock.patch.object(cs, "OUT_JSON", self.tmp.name), \
             mock.patch.object(cs, "run_extraction", side_effect=runs):
            summary = cs.main()

        self.assertEqual(summary["stop_reason"], cs.STOP_REASON_INTERRUPTED)
        self.assertEqual(len(summary["failed_pages"]), 1)
        self.assertEqual(summary["failed_pages"][0]["start"], 10)
        self.assertEqual(summary["failed_pages"][0]["run"], 2)
        self.assertIn("失败页", summary["stop_reason_text"])
        with open(self.tmp.name, encoding="utf-8") as f:
            data = json.load(f)
        # Output remains a bare product list with the uniform schema.
        self.assertEqual(len(data), 1)
        self.assertNotIn("stop_reason", data[0])


class ScrapeDoContractTests(unittest.TestCase):
    def test_scrape_do_url_shape_unchanged(self):
        # The Scrape.do invocation itself must stay identical: same gateway,
        # token query param, URL-encoded target. No new request types added.
        session = mock.Mock()
        session.get.return_value = make_response("ok")
        with mock.patch.object(cs, "SCRAPE_DO_TOKEN", "tok123"):
            resp = cs.scrape_do("https://www.google.com/search?q=a+b&udm=28", session)
        called_url = session.get.call_args[0][0]
        self.assertTrue(called_url.startswith("http://api.scrape.do/?token=tok123&url="))
        # urllib.parse.quote leaves "/" safe by default, so the scheme colon is
        # percent-encoded but the path slashes are not (original behaviour).
        self.assertIn("https%3A//www.google.com/search", called_url)
        self.assertEqual(resp.text, "ok")

    def test_default_config_values(self):
        self.assertEqual(cs.MAX_RETRIES, 2)
        self.assertEqual(cs.EMPTY_PAGE_LIMIT, 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
