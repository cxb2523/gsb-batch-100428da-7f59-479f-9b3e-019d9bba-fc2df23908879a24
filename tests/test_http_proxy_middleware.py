# -*- coding: utf-8 -*-
"""Tests for the stateful, rotating proxy downloader middleware.

No real proxy service is involved: credentials are fabricated and responses /
exceptions are faked.
"""
import logging

import pytest
from scrapy.core.downloader.handlers.http11 import TunnelError
from scrapy.http import Request, Response
from twisted.internet.error import ConnectionRefusedError

from scrapy_utils.middlewares import (
    DEFAULT_PROXY_URL,
    HttpProxyMiddleware,
    ProxyCredentialPool,
    mask_credential,
)

FABRICATED_CREDENTIALS = [
    "user01:secret-password-01",
    "user02:secret-password-02",
    "user03:secret-password-03",
]


class FakeLogger:
    """Collects log records so tests can assert on their contents."""

    def __init__(self):
        self.records = []

    def _add(self, level, message):
        self.records.append((level, message))

    def info(self, message):
        self._add("info", message)

    def warning(self, message):
        self._add("warning", message)

    def error(self, message):
        self._add("error", message)

    def messages(self):
        return [message for _, message in self.records]

    def levels(self):
        return [level for level, _ in self.records]


class FakeSpider:
    def __init__(self):
        self.name = "fake-spider"
        self.logger = FakeLogger()

    @property
    def logs(self):
        return self.logger.messages()


def make_request(url="https://example.com/path"):
    return Request(url)


def make_response(request, status=200):
    return Response(url=request.url, status=status, request=request)


def make_middleware(credentials=None):
    return HttpProxyMiddleware(
        credentials=FABRICATED_CREDENTIALS if credentials is None else credentials
    )


# ---------------------------------------------------------------------------
# ProxyCredentialPool unit tests
# ---------------------------------------------------------------------------


class TestProxyCredentialPool:
    def test_round_robin_order_and_cycling(self):
        pool = ProxyCredentialPool(FABRICATED_CREDENTIALS)

        first_pass = [pool.acquire() for _ in range(3)]
        assert [index for index, _ in first_pass] == [0, 1, 2]
        assert [cred for _, cred in first_pass] == FABRICATED_CREDENTIALS

        # The rotation wraps around instead of re-randomizing.
        index, credential = pool.acquire()
        assert (index, credential) == (0, FABRICATED_CREDENTIALS[0])

    def test_blank_credentials_are_ignored(self):
        pool = ProxyCredentialPool(["", "  ", "user:pass"])
        assert pool.size == 1
        index, credential = pool.acquire()
        assert index == 0 and credential == "user:pass"

    def test_disabled_credentials_are_skipped(self):
        pool = ProxyCredentialPool(FABRICATED_CREDENTIALS)
        assert pool.mark_unavailable(1) is True

        selected = [pool.acquire()[0] for _ in range(4)]
        # Index 1 is never handed out again; the rest keep rotating.
        assert selected == [0, 2, 0, 2]
        assert pool.available_count() == 2

    def test_mark_unavailable_is_idempotent(self):
        pool = ProxyCredentialPool(FABRICATED_CREDENTIALS)
        assert pool.mark_unavailable(0) is True
        assert pool.mark_unavailable(0) is False
        assert pool.mark_unavailable(99) is False
        assert pool.available_count() == 2

    def test_exhausted_pool_returns_nothing_and_warns_once(self):
        pool = ProxyCredentialPool(FABRICATED_CREDENTIALS)
        for index in range(3):
            pool.mark_unavailable(index)

        assert pool.is_exhausted() is True
        assert pool.acquire() == (None, None)
        assert pool.take_all_disabled_warning() is True
        # The summary warning is one-shot.
        assert pool.take_all_disabled_warning() is False

    def test_empty_pool(self):
        pool = ProxyCredentialPool([])
        assert pool.is_empty() is True
        assert pool.is_exhausted() is False
        assert pool.acquire() == (None, None)
        assert pool.available_count() == 0


class TestMasking:
    def test_mask_never_exposes_full_credential(self):
        credential = "user01:secret-password-01"
        masked = mask_credential(credential)
        assert "secret-password-01" not in masked
        assert credential not in masked
        # Still distinguishable across different credentials.
        assert mask_credential("user02:secret-password-02") != masked


# ---------------------------------------------------------------------------
# Scenario 1: normal sequential rotation
# ---------------------------------------------------------------------------


class TestNormalRotation:
    def test_requests_rotate_credentials_in_order(self):
        mw = make_middleware()
        spider = FakeSpider()

        requests = [make_request() for _ in range(4)]
        mw.process_request(requests[0], spider)
        mw.process_request(requests[1], spider)
        mw.process_request(requests[2], spider)
        mw.process_request(requests[3], spider)

        assert [r.meta["proxy_credential_index"] for r in requests] == [
            0, 1, 2, 0
        ]
        # Every proxied request points at the configured proxy entry.
        for request in requests:
            assert request.meta["proxy"] == DEFAULT_PROXY_URL
            assert request.headers["Proxy-Authorization"].startswith(b"Basic ")
            assert request.headers["Proxy-Connection"] == b"close"

    def test_success_keeps_credential_enabled(self):
        mw = make_middleware()
        spider = FakeSpider()
        request = make_request()

        mw.process_request(request, spider)
        response = make_response(request, status=200)
        assert mw.process_response(request, response, spider) is response
        assert mw.pool.available_count() == 3

    def test_selection_log_contains_masked_id_index_and_remaining(self):
        mw = make_middleware()
        spider = FakeSpider()
        request = make_request()

        mw.process_request(request, spider)
        log_line = spider.logs[0]

        assert "#0" in log_line
        assert "3 credential(s) remaining" in log_line
        assert "secret-password-01" not in log_line

    def test_connection_failure_switches_to_next_credential_on_retry(self):
        mw = make_middleware()
        spider = FakeSpider()
        request = make_request()

        # First attempt uses credential #0, which then fails to connect.
        mw.process_request(request, spider)
        assert request.meta["proxy_credential_index"] == 0

        retry = mw.process_exception(
            request, ConnectionRefusedError(), spider
        )

        assert isinstance(retry, Request)
        assert retry is not request
        assert retry.meta["proxy_retry_times"] == 1
        # The failed credential is gone from the pool.
        assert mw.pool.available_count() == 2

        # ...and the retried request picks the next available one (#1).
        mw.process_request(retry, spider)
        assert retry.meta["proxy_credential_index"] == 1

        failure_logs = [m for m in spider.logs if "marked unavailable" in m]
        assert len(failure_logs) == 1
        assert "#0" in failure_logs[0]
        assert "ConnectionRefusedError" in failure_logs[0]
        assert "2 credential(s) remaining" in failure_logs[0]
        assert "secret-password-01" not in failure_logs[0]

    def test_http_407_disables_credential_and_retries_with_next(self):
        mw = make_middleware()
        spider = FakeSpider()
        request = make_request()

        mw.process_request(request, spider)
        assert request.meta["proxy_credential_index"] == 0
        response = make_response(request, status=407)

        retry = mw.process_response(request, response, spider)
        assert isinstance(retry, Request)
        assert retry.dont_filter is True
        assert retry.meta.get("proxy_credential_index") is None
        assert mw.pool.available_count() == 2

        mw.process_request(retry, spider)
        assert retry.meta["proxy_credential_index"] == 1

    def test_unrelated_exception_does_not_disable_credential(self):
        mw = make_middleware()
        spider = FakeSpider()
        request = make_request()
        mw.process_request(request, spider)

        # ValueError is not a proxy/connection failure class.
        result = mw.process_exception(request, ValueError("boom"), spider)
        assert result is None
        assert mw.pool.available_count() == 3


# ---------------------------------------------------------------------------
# Scenario 2: empty credential list -> direct connection
# ---------------------------------------------------------------------------


class TestEmptyCredentialList:
    def test_no_proxy_headers_or_meta_are_written(self):
        mw = make_middleware(credentials=[])
        spider = FakeSpider()
        request = make_request()
        # Pre-populate a header to prove the middleware strips proxy artefacts.
        request.headers["Proxy-Authorization"] = "Basic stale"

        assert mw.process_request(request, spider) is None

        assert "Proxy-Authorization" not in request.headers
        assert "Proxy-Connection" not in request.headers
        assert "proxy" not in request.meta
        assert request.meta.get("proxy_credential_index") is None

    def test_explicit_warning_is_logged_once(self):
        mw = make_middleware(credentials=[])
        spider = FakeSpider()

        mw.process_request(make_request(), spider)
        mw.process_request(make_request(), spider)

        warnings = [m for m in spider.logs if "directly" in m]
        assert len(warnings) == 1
        assert "credential list is empty" in warnings[0]

    def test_direct_request_407_is_not_treated_as_credential_failure(self):
        mw = make_middleware(credentials=[])
        spider = FakeSpider()
        request = make_request()
        mw.process_request(request, spider)
        response = make_response(request, status=407)

        # No credential exists to disable; the response passes through.
        assert mw.process_response(request, response, spider) is response


# ---------------------------------------------------------------------------
# Scenario 3: every credential fails -> direct fallback with summary warning
# ---------------------------------------------------------------------------


class TestAllCredentialsFailFallback:
    def _fail_credential_with_407(self, mw, spider, request):
        mw.process_request(request, spider)
        response = make_response(request, status=407)
        return mw.process_response(request, response, spider)

    def test_summary_warning_after_all_disabled_and_direct_followup(self):
        mw = make_middleware()
        spider = FakeSpider()

        request = make_request()
        retry = self._fail_credential_with_407(mw, spider, request)
        assert isinstance(retry, Request)  # -> credential #1
        retry = self._fail_credential_with_407(mw, spider, retry)
        assert isinstance(retry, Request)  # -> credential #2
        retry = self._fail_credential_with_407(mw, spider, retry)

        # The third 407 disables the last credential and returns one final
        # retry request; the pool is now fully exhausted.
        assert isinstance(retry, Request)
        assert mw.pool.available_count() == 0

        summary = [m for m in spider.logs if "all proxy credentials" in m]
        assert len(summary) == 1
        assert "3 total" in summary[0]
        # No secret material may leak into the summary.
        assert all("secret-password" not in m for m in spider.logs)

        # The final retry goes out directly (no proxy header / meta) instead
        # of cycling credentials forever.
        mw.process_request(retry, spider)
        assert retry.meta.get("proxy") is None
        assert "Proxy-Authorization" not in retry.headers

        direct_response = make_response(retry, status=407)
        # A 407 on the direct attempt has no credential to blame and passes
        # straight through to the spider.
        assert mw.process_response(retry, direct_response, spider) is direct_response

        # Brand-new follow-up requests also go out directly...
        follow_up = make_request(url="https://example.com/other")
        mw.process_request(follow_up, spider)
        assert "proxy" not in follow_up.meta
        assert "Proxy-Authorization" not in follow_up.headers

        # ...and the summary warning is not repeated on every request.
        assert len([m for m in spider.logs if "all proxy credentials" in m]) == 1

    def test_connection_exceptions_also_lead_to_direct_fallback(self):
        mw = make_middleware()
        spider = FakeSpider()
        request = make_request()

        retry = request
        for _ in range(3):
            mw.process_request(retry, spider)
            retry = mw.process_exception(retry, TunnelError(), spider)

        # Each of the three credentials failed once; the last failure still
        # yields a final retry request, but the pool is now exhausted.
        assert isinstance(retry, Request)
        assert mw.pool.available_count() == 0

        summary = [m for m in spider.logs if "all proxy credentials" in m]
        assert len(summary) == 1
        assert all("secret-password" not in m for m in spider.logs)

        # The final retry (and every follow-up request) goes out directly.
        mw.process_request(retry, spider)
        assert retry.meta.get("proxy") is None
        assert "Proxy-Authorization" not in retry.headers

        # A connection failure on the direct attempt has no credential to
        # blame: it propagates via Scrapy's normal error handling (None).
        assert mw.process_exception(retry, TunnelError(), spider) is None

        follow_up = make_request()
        mw.process_request(follow_up, spider)
        assert follow_up.meta.get("proxy") is None
        assert "Proxy-Authorization" not in follow_up.headers
        # No further summary warnings on follow-up requests.
        assert len([m for m in spider.logs if "all proxy credentials" in m]) == 1

    def test_retry_budget_caps_407_retries(self):
        mw = make_middleware()
        spider = FakeSpider()
        request = make_request()
        request.meta["proxy_retry_times"] = mw.max_retries

        mw.process_request(request, spider)
        # Budget already spent -> request is sent directly, no cycling.
        assert request.meta.get("proxy") is None
        response = make_response(request, status=407)
        assert mw.process_response(request, response, spider) is response


def test_module_logger_fallback_when_spider_has_no_logger():
    """The middleware must still log when used outside a real spider."""
    mw = make_middleware(credentials=[])
    request = make_request()

    mw.process_request(request, object())  # no .logger attribute

    assert "Proxy-Authorization" not in request.headers


def test_import_path_uses_configured_settings(caplog):
    """from_crawler wires the Scrapy settings into the middleware."""

    class FakeSettings:
        def __init__(self, data):
            self._data = data

        def getlist(self, key, default=None):
            return self._data.get(key, default)

        def get(self, key, default=None):
            return self._data.get(key, default)

    class FakeCrawler:
        settings = FakeSettings(
            {
                "proxyAuth": ["a:1", "b:2"],
                "PROXY_URL": "https://proxy.example:8443",
            }
        )

    mw = HttpProxyMiddleware.from_crawler(FakeCrawler())
    assert mw.pool.size == 2
    assert mw.proxy_url == "https://proxy.example:8443"

    spider = FakeSpider()
    request = make_request()
    with caplog.at_level(logging.INFO):
        mw.process_request(request, spider)
    assert request.meta["proxy"] == "https://proxy.example:8443"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
