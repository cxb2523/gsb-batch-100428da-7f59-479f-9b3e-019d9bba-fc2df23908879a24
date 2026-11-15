"""Acceptance tests for the fetch-escalation bookkeeping in enrich._get.

Each site result must keep the direct attempt's outcome AND the proxy
attempt's outcome side by side, and name which one was adopted. Simulated
with stub sessions / stubbed proxy fetches, plus one real local HTTP
server for the direct-success path.
"""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

from facebook_page_info_scraper import enrich, proxy


# --------------------------------------------------------------------------
# stubs
# --------------------------------------------------------------------------

class StubResponse:
    """Just enough of a requests.Response for _get_requests."""

    def __init__(self, body="", status=200, headers=None):
        self._body = body.encode()
        self.status_code = status
        self.headers = headers or {}

    def iter_content(self, size):
        yield self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class StubSession:
    """A requests.Session stand-in: returns `behavior` or raises it."""

    def __init__(self, behavior):
        self.behavior = behavior
        self.headers = {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if isinstance(self.behavior, Exception):
            raise self.behavior
        return self.behavior


def stub_proxy(monkeypatch, answer):
    """Replace proxy.fetch with a stub; returns the list of URLs it saw."""
    calls = []

    def fake_fetch(url, **kwargs):
        calls.append(url)
        return answer

    monkeypatch.setattr(proxy, "fetch", fake_fetch)
    return calls


PROXY_OK = {
    "provider": "custom", "status": 200, "headers": {},
    "body": "<html>contact us</html>", "error": None,
}
PROXY_FAIL = {
    "provider": "custom", "status": None, "headers": {},
    "body": None, "error": "custom: timeout",
}

DNS_ERROR = requests.ConnectionError(
    "HTTPSConnectionPool(host='dead.example', port=443): Max retries "
    "exceeded (Caused by NameResolutionError('getaddrinfo failed'))"
)


def collect(session, **kwargs):
    kwargs.setdefault("use_fingerprint", False)
    result, _, _ = enrich._collect(
        "https://example.com", session, 5, False, **kwargs
    )
    return result


# --------------------------------------------------------------------------
# scenario 1: direct times out, proxy rescues the site
# --------------------------------------------------------------------------

def test_direct_timeout_proxy_succeeds(monkeypatch):
    session = StubSession(requests.Timeout())
    proxy_calls = stub_proxy(monkeypatch, dict(PROXY_OK))

    result = collect(session, use_proxy=True)

    assert proxy_calls == ["https://example.com"]
    # the proxy answer is the adopted one
    assert result["fetch_source"] == "proxy"
    assert result["fetch_outcome"] == "ok"
    assert result["tier"] == "custom"
    assert result["status"] == "ok"
    # but the direct attempt's real ending is still there
    assert result["direct_attempt"]["outcome"] == "timeout"
    assert result["direct_attempt"]["tier"] == "requests"
    assert result["proxy_attempt"]["outcome"] == "ok"
    assert result["proxy_attempt"]["tier"] == "custom"


# --------------------------------------------------------------------------
# scenario 2: direct DNS failure, proxy fails too
# --------------------------------------------------------------------------

def test_direct_dns_failure_proxy_also_fails(monkeypatch):
    session = StubSession(DNS_ERROR)
    proxy_calls = stub_proxy(monkeypatch, dict(PROXY_FAIL))

    result = collect(session, use_proxy=True)

    assert proxy_calls == ["https://example.com"]
    # the direct result stays the final answer - a failed proxy must not
    # mask the real reason the site did not load
    assert result["fetch_source"] == "direct"
    assert result["fetch_outcome"] == "dns"
    assert result["tier"] == "requests"
    assert result["status"] == "unreachable"
    assert result["error"] == "ConnectionError"
    # and the proxy's own failure is recorded alongside, not on top
    assert result["direct_attempt"]["outcome"] == "dns"
    assert result["proxy_attempt"]["outcome"] == "error"
    assert result["proxy_attempt"]["error"] == "custom: timeout"
    assert result["proxy_attempt"]["tier"] == "custom"


# --------------------------------------------------------------------------
# scenario 3: direct succeeds - the proxy must not be called
# --------------------------------------------------------------------------

def test_direct_success_proxy_never_called(monkeypatch):
    session = StubSession(StubResponse("<html>hi</html>", 200))
    proxy_calls = stub_proxy(monkeypatch, dict(PROXY_OK))

    result = collect(session, use_proxy=True)

    assert proxy_calls == []
    assert result["fetch_source"] == "direct"
    assert result["fetch_outcome"] == "ok"
    assert result["tier"] == "requests"
    assert result["status"] == "ok"
    assert result["direct_attempt"]["outcome"] == "ok"
    assert result["proxy_attempt"] == {"skipped": True, "reason": "direct_ok"}


def test_direct_success_against_local_http_server(monkeypatch):
    """Same as above, but through a real local HTTP server end to end."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"<html><a href='mailto:info@example.com'>mail</a></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    proxy_calls = stub_proxy(monkeypatch, dict(PROXY_OK))
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        result, candidates, host = enrich._collect(
            url, None, 5, False, use_fingerprint=False, use_proxy=True
        )
    finally:
        server.shutdown()
        server.server_close()

    assert proxy_calls == []
    assert result["fetch_source"] == "direct"
    assert result["fetch_outcome"] == "ok"
    assert result["status"] == "ok"
    assert result["proxy_attempt"] == {"skipped": True, "reason": "direct_ok"}
    assert [c.email for c in candidates] == ["info@example.com"]


# --------------------------------------------------------------------------
# proxy_mode="always": the proxy result is final, direct is skipped
# --------------------------------------------------------------------------

def test_proxy_mode_always_records_direct_skipped(monkeypatch):
    # direct would succeed, but must never be attempted
    session = StubSession(StubResponse("<html>hi</html>", 200))
    proxy_calls = stub_proxy(monkeypatch, dict(PROXY_FAIL))

    result = collect(session, use_proxy=True, proxy_mode="always")

    assert session.calls == []
    assert proxy_calls == ["https://example.com"]
    # the proxy result is the final answer even though it failed
    assert result["fetch_source"] == "proxy"
    assert result["fetch_outcome"] == "error"
    assert result["tier"] == "custom"
    assert result["status"] == "unreachable"
    assert result["error"] == "custom: timeout"
    assert result["direct_attempt"] == {
        "skipped": True, "reason": "proxy_mode=always",
    }
    assert result["proxy_attempt"]["error"] == "custom: timeout"


def test_proxy_mode_always_success(monkeypatch):
    session = StubSession(StubResponse("<html>hi</html>", 200))
    stub_proxy(monkeypatch, dict(PROXY_OK))

    result = collect(session, use_proxy=True, proxy_mode="always")

    assert session.calls == []
    assert result["fetch_source"] == "proxy"
    assert result["fetch_outcome"] == "ok"
    assert result["tier"] == "custom"
    assert result["direct_attempt"] == {
        "skipped": True, "reason": "proxy_mode=always",
    }


# --------------------------------------------------------------------------
# no proxy configured: behavior unchanged
# --------------------------------------------------------------------------

def test_no_proxy_configured_behavior_unchanged(monkeypatch):
    session = StubSession(requests.Timeout())
    proxy_calls = stub_proxy(monkeypatch, dict(PROXY_OK))

    result = collect(session, use_proxy=False)

    assert proxy_calls == []
    assert result["fetch_source"] == "direct"
    assert result["fetch_outcome"] == "timeout"
    assert result["tier"] == "requests"
    assert result["status"] == "unreachable"
    assert result["error"] == "timeout"
    assert result["proxy_attempt"] == {
        "skipped": True, "reason": "not_configured",
    }


def test_non_retryable_outcome_skips_proxy(monkeypatch):
    session = StubSession(StubResponse("not here", 404))
    proxy_calls = stub_proxy(monkeypatch, dict(PROXY_OK))

    result = collect(session, use_proxy=True)

    assert proxy_calls == []
    assert result["fetch_source"] == "direct"
    assert result["fetch_outcome"] == "not_found"
    assert result["proxy_attempt"] == {
        "skipped": True, "reason": "outcome not_found not retryable",
    }


# --------------------------------------------------------------------------
# summarise_enrichment: stats are not disturbed by the new fields
# --------------------------------------------------------------------------

def test_summarise_enrichment_unaffected_by_new_fields():
    records = [
        {
            "enrichment": {
                "status": "ok", "tier": "requests", "fetch_source": "direct",
                "direct_attempt": {"outcome": "ok"},
                "proxy_attempt": {"skipped": True, "reason": "direct_ok"},
            },
            "enriched_email": "info@example.se",
            "enriched_confidence": "high",
            "_enrich_status": "ok",
            "_enrich_tier": "requests",
        },
        {
            "enrichment": {
                "status": "unreachable", "tier": "custom",
                "fetch_source": "direct",
                "direct_attempt": {"outcome": "dns"},
                "proxy_attempt": {"outcome": "error"},
            },
            "_enrich_status": "unreachable",
            "_enrich_tier": "custom",
        },
        {
            "enrichment": {
                "status": "ok", "tier": "custom", "fetch_source": "proxy",
                "direct_attempt": {"outcome": "timeout"},
                "proxy_attempt": {"outcome": "ok"},
            },
            "_enrich_status": "no_contact_found",
            "_enrich_tier": "custom",
        },
    ]

    summary = enrich.summarise_enrichment(records)

    assert summary["records"] == 3
    assert summary["attempted"] == 3
    assert summary["found_email"] == 1
    assert summary["by_confidence"] == {"high": 1}
    assert summary["by_tier"] == {"requests": 1, "custom": 2}
    assert summary["unreachable"] == 1
    assert summary["no_contact"] == 1
    assert summary["challenged"] == 0
