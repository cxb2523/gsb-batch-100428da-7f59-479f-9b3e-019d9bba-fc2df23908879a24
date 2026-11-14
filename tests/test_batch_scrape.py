"""
Offline tests for the batch scrape entry point.

A fake HTTP layer replaces `requests.request` so no real API key or network
access is needed. It simulates products that succeed, that keep returning
429 / 503 until retries are exhausted, that fail permanently (401), and
whose result never becomes ready.
"""
import json
import unittest
from unittest.mock import patch

from lumintu_scraper_client import (
    LumintuScraperClient,
    BatchResult,
    STATUS_SUCCESS,
    STATUS_FAILED,
    STATUS_PENDING,
)

ACTOR_PATH_SUFFIX = "api/shopee/scrape"
RESULT_PATH_SUFFIX = "scrape/result"


class FakeResponse:
    def __init__(self, status_code=200, payload=None, url="https://fake.test/"):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.url = url
        self.text = json.dumps(self._payload)

    @property
    def ok(self):
        return 200 <= self.status_code < 400

    def json(self):
        return self._payload


def completed_payload(request_id, url):
    return {
        "request_id": request_id,
        "status": "completed",
        "status_code": 200,
        "message": "",
        "created_at": "2026-11-15T00:00:00",
        "updated_at": "2026-11-15T00:00:01",
        "elapsed_time": 1.0,
        "request_data": {"url": url},
        "result": {"title": f"product for {url}"},
        "timestamp": "2026-11-15T00:00:01",
    }


class FakeShopeeApi:
    """
    Routes fake HTTP calls per product URL keyword:
      - "ok"          -> actor accepted, result completes immediately
      - "ratelimit"   -> actor call always 429
      - "servererror" -> actor call always 503
      - "authfail"    -> actor call always 401 (permanent)
      - "neverready"  -> actor accepted, result stays "running" forever
      - "apifailed"   -> actor accepted, result ends with status "failed"
    """

    def __init__(self):
        self.calls = []  # (method, path, request body)

    def __call__(self, method, url, **kwargs):
        body = kwargs.get("json") or {}
        self.calls.append((method, url, body))

        if url.endswith(ACTOR_PATH_SUFFIX):
            return self._actor(url, body)
        if url.endswith(RESULT_PATH_SUFFIX):
            return self._result(url, body)
        raise AssertionError(f"Unexpected request: {method} {url}")

    def _actor(self, url, body):
        product_url = body.get("url", "")
        if "ratelimit" in product_url:
            return FakeResponse(429, {"error": "rate limit"}, url)
        if "servererror" in product_url:
            return FakeResponse(503, {"error": "server error"}, url)
        if "authfail" in product_url:
            return FakeResponse(401, {"error": "unauthorized"}, url)
        request_id = "req-" + product_url.rsplit("/", 1)[-1]
        return FakeResponse(200, {"request_id": request_id, "status": "pending"}, url)

    def _result(self, url, body):
        request_id = body.get("request_id", "")
        if "neverready" in request_id:
            return FakeResponse(200, {"request_id": request_id, "status": "running"}, url)
        if "apifailed" in request_id:
            return FakeResponse(200, {
                "request_id": request_id,
                "status": "failed",
                "message": "product not found on shopee",
            }, url)
        return FakeResponse(200, completed_payload(request_id, "fake"), url)

    def actor_call_urls(self):
        return [b.get("url") for m, u, b in self.calls if u.endswith(ACTOR_PATH_SUFFIX)]


def make_client():
    # backoff_factor=0 keeps the existing retry policy but without real sleeps.
    return LumintuScraperClient(
        api_key="FAKE_KEY", max_retries=3, backoff_factor=0
    )


def run_batch(fake_api, items, previous=None, **kwargs):
    client = make_client()
    kwargs.setdefault("interval", 0)
    kwargs.setdefault("max_attempts", 2)
    with patch(
            "lumintu_scraper_client._http_client.requests.request",
            side_effect=fake_api,
    ):
        return client.batch().scrape_shopee_products(items, previous=previous, **kwargs)


def item_of(batch, url_part):
    return next(i for i in batch.items if url_part in i.url)


class BatchScrapeTest(unittest.TestCase):
    def test_mixed_batch_keeps_successes_and_records_failures(self):
        fake_api = FakeShopeeApi()
        items = [
            {"url": "https://shopee.co.id/ok-1", "country": "ID"},
            {"url": "https://shopee.co.id/ratelimit-1", "country": "ID"},
            {"url": "https://shopee.co.id/servererror-1", "country": "ID"},
            {"url": "https://shopee.co.id/ok-2", "country": "ID"},
        ]

        batch = run_batch(fake_api, items)

        self.assertEqual(len(batch.items), 4)

        ok1 = item_of(batch, "ok-1")
        ok2 = item_of(batch, "ok-2")
        for ok in (ok1, ok2):
            self.assertEqual(ok.status, STATUS_SUCCESS)
            self.assertIsNotNone(ok.request_id)
            self.assertEqual(ok.result["result"]["title"], "product for fake")

        rate = item_of(batch, "ratelimit-1")
        self.assertEqual(rate.status, STATUS_FAILED)
        self.assertTrue(rate.error)
        self.assertFalse(rate.permanent)
        # actor call retried max_retries times before giving up
        self.assertEqual(fake_api.actor_call_urls().count(rate.url), 3)

        srv = item_of(batch, "servererror-1")
        self.assertEqual(srv.status, STATUS_FAILED)
        self.assertTrue(srv.error)
        self.assertFalse(srv.permanent)

        # the failure of item 2/3 did not abort the batch: item 4 was scraped
        self.assertIn("https://shopee.co.id/ok-2", fake_api.actor_call_urls())

    def test_api_level_failure_and_unfinished_item(self):
        fake_api = FakeShopeeApi()
        items = [
            {"url": "https://shopee.co.id/apifailed-1", "country": "ID"},
            {"url": "https://shopee.co.id/neverready-1", "country": "ID"},
            {"url": "https://shopee.co.id/ok-1", "country": "ID"},
        ]

        batch = run_batch(fake_api, items)

        failed = item_of(batch, "apifailed-1")
        self.assertEqual(failed.status, STATUS_FAILED)
        self.assertEqual(failed.error, "product not found on shopee")
        self.assertIsNotNone(failed.request_id)

        unfinished = item_of(batch, "neverready-1")
        self.assertEqual(unfinished.status, STATUS_PENDING)
        self.assertEqual(unfinished.error_type, "TimeoutError")
        self.assertIsNotNone(unfinished.request_id)

        self.assertEqual(item_of(batch, "ok-1").status, STATUS_SUCCESS)

    def test_permanent_failure_is_not_retried_on_resume(self):
        fake_api = FakeShopeeApi()
        items = [{"url": "https://shopee.co.id/authfail-1", "country": "ID"}]

        batch = run_batch(fake_api, items)
        failed = item_of(batch, "authfail-1")
        self.assertEqual(failed.status, STATUS_FAILED)
        self.assertEqual(failed.error_type, "AuthenticationError")
        self.assertTrue(failed.permanent)
        self.assertEqual(batch.pending, [])

        calls_before = len(fake_api.calls)
        resumed = run_batch(fake_api, items, previous=batch)
        self.assertEqual(len(fake_api.calls), calls_before)  # no new requests
        self.assertEqual(item_of(resumed, "authfail-1").status, STATUS_FAILED)
        self.assertEqual(resumed.pending, [])

    def test_resume_only_reprocesses_remaining_items(self):
        items = [
            {"url": "https://shopee.co.id/ok-1", "country": "ID"},
            {"url": "https://shopee.co.id/ratelimit-1", "country": "ID"},
            {"url": "https://shopee.co.id/neverready-1", "country": "ID"},
        ]

        first_api = FakeShopeeApi()
        first = run_batch(first_api, items)
        self.assertEqual(len(first.succeeded), 1)
        self.assertEqual(len(first.failed), 1)
        self.assertEqual(len(first.unfinished), 1)
        self.assertEqual(len(first.pending), 2)

        # second run: the previously rate-limited and never-ready items now
        # behave (keywords no longer match the failure branches once we swap
        # the URLs the fake sees is not possible, so use a friendlier fake).
        class RecoveringApi(FakeShopeeApi):
            def _actor(self, url, body):
                product_url = body.get("url", "")
                request_id = "req-recovered-" + product_url.rsplit("-", 1)[-1]
                return FakeResponse(200, {"request_id": request_id, "status": "pending"}, url)

        second_api = RecoveringApi()
        resumed = run_batch(second_api, items, previous=first)

        # only the two non-successful items were requested again
        self.assertEqual(
            sorted(second_api.actor_call_urls()),
            ["https://shopee.co.id/neverready-1", "https://shopee.co.id/ratelimit-1"],
        )

        self.assertEqual(len(resumed.items), 3)
        self.assertEqual(resumed.pending, [])
        # the previously successful item was reused untouched
        reused = item_of(resumed, "ok-1")
        self.assertIs(reused, item_of(first, "ok-1"))
        self.assertEqual(reused.result["result"]["title"], "product for fake")
        # recovered items now carry real results
        self.assertEqual(item_of(resumed, "ratelimit-1").status, STATUS_SUCCESS)
        self.assertEqual(item_of(resumed, "neverready-1").status, STATUS_SUCCESS)

    def test_resume_accepts_serialized_previous_batch(self):
        fake_api = FakeShopeeApi()
        items = [
            {"url": "https://shopee.co.id/ok-1", "country": "ID"},
            {"url": "https://shopee.co.id/ratelimit-1", "country": "ID"},
        ]
        first = run_batch(fake_api, items)

        # serialize to JSON and back, as a caller persisting the batch would
        payload = json.loads(json.dumps(first.to_dict()))
        restored = BatchResult.from_dict(payload)
        self.assertEqual(restored.to_dict(), first.to_dict())

        class RecoveringApi(FakeShopeeApi):
            def _actor(self, url, body):
                return FakeResponse(
                    200, {"request_id": "req-recovered", "status": "pending"}, url
                )

        second_api = RecoveringApi()
        resumed = run_batch(second_api, items, previous=payload)
        self.assertEqual(second_api.actor_call_urls(),
                         ["https://shopee.co.id/ratelimit-1"])
        self.assertEqual(resumed.pending, [])

    def test_resume_fully_successful_batch_returns_empty_pending(self):
        fake_api = FakeShopeeApi()
        items = [
            {"url": "https://shopee.co.id/ok-1", "country": "ID"},
            {"url": "https://shopee.co.id/ok-2", "country": "ID"},
        ]
        first = run_batch(fake_api, items)
        self.assertEqual(len(first.succeeded), 2)
        self.assertEqual(first.pending, [])

        calls_before = len(fake_api.calls)
        again = run_batch(fake_api, items, previous=first)
        self.assertEqual(len(fake_api.calls), calls_before)  # zero new requests
        self.assertEqual(again.pending, [])
        self.assertEqual(len(again.succeeded), 2)


if __name__ == "__main__":
    unittest.main()
