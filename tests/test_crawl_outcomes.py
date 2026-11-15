"""Integration tests for per-URL crawl outcomes, redirect handling, and shared backoff.

A small self-contained HTTP service (stdlib only, ephemeral port) serves:
  * a 302 -> 302 -> 200 redirect chain,
  * an always-429 path,
  * a robots.txt-forbidden path,
  * (separately) a closed port that guarantees a connection failure,
  * a >6-hop redirect loop to exercise the redirect limit.
"""
import http.server
import socketserver
import threading

import pytest

from siteprobe.crawler.engine import MAX_REDIRECT_HOPS, AsyncCrawler, BackoffController
from siteprobe.crawler.storage import CrawlStorage
from siteprobe.models.config import CrawlConfig
from siteprobe.models.crawl import UrlOutcomeType

ROBOTS_TXT = """\
User-agent: *
Disallow: /private
"""

ROOT_HTML = """\
<!DOCTYPE html>
<html><head><title>Outcome Test Root</title></head>
<body>
  <a href="/chain/start">Redirect chain</a>
  <a href="/rate-limited">Always rate limited</a>
  <a href="/private">Robots forbidden</a>
  <a href="/loop/0">Long redirect loop</a>
  <a href="http://127.0.0.1:{dead_port}/dead">Dead port link</a>
</body></html>
"""

CHAIN_END_HTML = """\
<!DOCTYPE html>
<html><head><title>Chain End</title></head><body><h1>Landed</h1></body></html>
"""


class OutcomeTestHandler(http.server.BaseHTTPRequestHandler):
    requested_paths = []
    _lock = threading.Lock()

    def _record(self):
        with self._lock:
            self.requested_paths.append(self.path)

    def do_GET(self):
        self._record()
        path = self.path

        if path == "/robots.txt":
            self._send(200, ROBOTS_TXT, "text/plain")
            return
        if path == "/sitemap.xml":
            self.send_response(404)
            self.end_headers()
            return
        if path == "/":
            dead_port = self.server.dead_port
            self._send(200, ROOT_HTML.format(dead_port=dead_port), "text/html")
            return

        # 302 -> 302 -> 200
        if path == "/chain/start":
            self._redirect("/chain/mid")
            return
        if path == "/chain/mid":
            self._redirect("/chain/end")
            return
        if path == "/chain/end":
            self._send(200, CHAIN_END_HTML, "text/html")
            return

        # Always unavailable: crawler must retry, then record an abandonment outcome.
        if path == "/rate-limited":
            self.send_response(429)
            self.send_header("Retry-After", "0")
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"rate limited")
            return

        # Served only if the crawler ignores robots.txt (the test asserts it never does).
        if path == "/private":
            self._send(200, "<html><body>private</body></html>", "text/html")
            return

        # Long redirect loop: /loop/0 -> /loop/1 -> ... (> MAX_REDIRECT_HOPS hops).
        if path.startswith("/loop/"):
            n = int(path.rsplit("/", 1)[1])
            self._redirect(f"/loop/{n + 1}")
            return

        self.send_response(404)
        self.end_headers()

    def _send(self, code, body, content_type):
        payload = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def log_message(self, format, *args):
        pass


class _OutcomeServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, dead_port):
        super().__init__(("127.0.0.1", 0), OutcomeTestHandler)
        self.dead_port = dead_port


@pytest.fixture
def local_server():
    import socket

    # Bound but never listening -> connect() is refused for the whole test run.
    dead_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    dead_socket.bind(("127.0.0.1", 0))
    dead_port = dead_socket.getsockname()[1]

    OutcomeTestHandler.requested_paths = []
    server = _OutcomeServer(dead_port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield base_url, dead_port
    finally:
        server.shutdown()
        server.server_close()
        dead_socket.close()


@pytest.mark.asyncio
async def test_every_discovered_url_gets_one_final_outcome(local_server):
    base_url, dead_port = local_server
    config = CrawlConfig(max_pages=50, concurrency=2, crawl_delay_ms=10, timeout_seconds=5.0, trust_env=False)
    storage = CrawlStorage(":memory:")
    crawler = AsyncCrawler(base_url, config=config, storage=storage)

    summary = await crawler.crawl()

    by_url = {o.url: o for o in summary.url_outcomes}

    # Exactly one final outcome per discovered URL - no duplicates, none missing.
    assert len(by_url) == len(summary.url_outcomes)
    assert set(by_url) == set(crawler.visited_urls)

    # 1. Plain successful fetch
    root = by_url[base_url + "/"]
    assert root.outcome == UrlOutcomeType.SUCCESS
    assert root.status_code == 200
    assert root.redirect_hops == 0

    # 2. Redirect then success: landing point + hop count recorded
    chain_url = base_url + "/chain/start"
    chain = by_url[chain_url]
    assert chain.outcome == UrlOutcomeType.REDIRECT_SUCCESS
    assert chain.final_url == base_url + "/chain/end"
    assert chain.redirect_hops == 2
    assert [h.status_code for h in chain.redirect_chain] == [302, 302, 200]
    assert [h.url for h in chain.redirect_chain] == [
        base_url + "/chain/start",
        base_url + "/chain/mid",
        base_url + "/chain/end",
    ]

    # The landing page must be retrievable by the ORIGINAL requested URL.
    stored = await storage.get_page(chain_url)
    assert stored is not None
    assert stored.url == chain_url
    assert stored.final_url == base_url + "/chain/end"
    assert stored.redirect_hops == 2
    assert len(stored.redirect_chain) == 3

    # 3. Redirect limit exceeded (> 6 hops) is an explicit outcome, not a silent return
    loop = by_url[base_url + "/loop/0"]
    assert loop.outcome == UrlOutcomeType.REDIRECT_LIMIT_EXCEEDED
    assert loop.redirect_hops == MAX_REDIRECT_HOPS + 1
    assert loop.final_url == f"{base_url}/loop/{MAX_REDIRECT_HOPS + 1}"

    # 4. robots.txt refusal is recorded and the forbidden URL is never requested
    blocked = by_url[base_url + "/private"]
    assert blocked.outcome == UrlOutcomeType.ROBOTS_DISALLOWED
    assert not any(p == "/private" for p in OutcomeTestHandler.requested_paths)

    # 5. Rate limiting / service unavailable: retried then abandoned with an outcome.
    #    The final 429/503 response is still stored (original total_crawled/broken semantics),
    #    keyed by the originally requested URL.
    limited = by_url[base_url + "/rate-limited"]
    assert limited.outcome == UrlOutcomeType.RATE_LIMITED_ABANDONED
    assert limited.status_code == 429
    assert limited.detail
    abandoned_page = await storage.get_page(base_url + "/rate-limited")
    assert abandoned_page is not None
    assert abandoned_page.status_code == 429
    assert abandoned_page.is_indexable is False

    # 6. Connection failure (closed port) is recorded as such
    dead_url = f"http://127.0.0.1:{dead_port}/dead"
    failed = by_url[dead_url]
    assert failed.outcome == UrlOutcomeType.CONNECTION_FAILED
    assert failed.status_code == 0
    assert failed.detail

    # Statistics stay self-consistent with their original semantics.
    stored_pages = await storage.get_all_pages()
    assert summary.total_crawled == len(stored_pages)
    assert summary.broken_links_count == sum(1 for p in stored_pages if p.status_code >= 400)
    # Stored: root(200), chain origin(200), rate-limited(429), dead(0).
    # robots-disallowed and over-limit redirects store no page, exactly as before.
    assert summary.total_crawled == 4
    assert summary.broken_links_count == 1
    assert summary.total_internal_discovered == len(crawler.visited_urls)

    # Every recorded outcome type appears exactly as expected in this scenario.
    outcome_kinds = {o.outcome for o in summary.url_outcomes}
    assert outcome_kinds == {
        UrlOutcomeType.SUCCESS,
        UrlOutcomeType.REDIRECT_SUCCESS,
        UrlOutcomeType.REDIRECT_LIMIT_EXCEEDED,
        UrlOutcomeType.ROBOTS_DISALLOWED,
        UrlOutcomeType.RATE_LIMITED_ABANDONED,
        UrlOutcomeType.CONNECTION_FAILED,
    }

    await storage.close()


@pytest.mark.asyncio
async def test_tight_max_pages_leaves_no_url_without_outcome(local_server):
    base_url, _dead_port = local_server
    config = CrawlConfig(max_pages=3, concurrency=1, crawl_delay_ms=5, timeout_seconds=5.0, trust_env=False)
    storage = CrawlStorage(":memory:")
    crawler = AsyncCrawler(base_url, config=config, storage=storage)

    summary = await crawler.crawl()

    # Budget is honored...
    assert summary.total_crawled <= config.max_pages
    # ...and every URL that was discovered still carries exactly one final outcome.
    urls = [o.url for o in summary.url_outcomes]
    assert len(urls) == len(set(urls))
    assert set(urls) == set(crawler.visited_urls)

    await storage.close()


class _BackoffHandler(http.server.BaseHTTPRequestHandler):
    request_order = []
    _lock = threading.Lock()

    def do_GET(self):
        with self._lock:
            self.request_order.append(self.path)
        if self.path == "/backoff/rate":
            self.send_response(429)
            self.end_headers()
            return
        if self.path == "/backoff/ok":
            body = b"<html><body>ok</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/robots.txt":
            self.send_response(404)
            self.end_headers()
            return
        if self.path == "/":
            body = (
                b'<html><body><a href="/backoff/rate">r</a>'
                b'<a href="/backoff/ok">o</a></body></html>'
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        pass


@pytest.mark.asyncio
async def test_backoff_affects_requests_not_yet_sent(monkeypatch):
    """A 429 must raise the delay seen by a later, not-yet-sent request, not just a field."""
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _BackoffHandler)
    server.allow_reuse_address = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    _BackoffHandler.request_order = []

    import asyncio

    real_sleep = asyncio.sleep
    slept = []

    async def recording_sleep(delay):
        slept.append(delay)
        return await real_sleep(0)

    monkeypatch.setattr("siteprobe.crawler.engine.asyncio.sleep", recording_sleep)

    base_delay = 0.02
    config = CrawlConfig(
        max_pages=10,
        concurrency=1,  # force FIFO: root -> rate-limited (retried) -> ok
        crawl_delay_ms=int(base_delay * 1000),
        timeout_seconds=5.0,
        trust_env=False,
    )
    storage = CrawlStorage(":memory:")
    crawler = AsyncCrawler(base_url, config=config, storage=storage)
    summary = await crawler.crawl()

    # The 429 path ended in an explicit abandonment outcome...
    outcomes = {o.url: o for o in summary.url_outcomes}
    assert outcomes[base_url + "/backoff/rate"].outcome == UrlOutcomeType.RATE_LIMITED_ABANDONED

    # ...and escalated the shared delay beyond the configured base delay.
    assert crawler.backoff.delay_seconds > base_delay

    # The later /backoff/ok request had not been sent when the 429s happened,
    # yet its pre-request sleep (the final recorded one) used the raised delay.
    assert _BackoffHandler.request_order[-1] == "/backoff/ok"
    assert slept[-1] > base_delay

    server.shutdown()
    server.server_close()
    await storage.close()


def test_backoff_controller_escalation_and_recovery():
    backoff = BackoffController(0.1)
    assert backoff.delay_seconds == 0.1

    backoff.on_rate_limited()
    assert backoff.delay_seconds == pytest.approx(0.2)
    backoff.on_rate_limited()
    assert backoff.delay_seconds == pytest.approx(0.4)

    # Shared state: any worker reads the escalated value before sending.
    assert backoff.delay_seconds > 0.1

    backoff.on_success()
    assert backoff.delay_seconds < 0.4  # relaxes toward base
    for _ in range(20):
        backoff.on_success()
    assert backoff.delay_seconds == pytest.approx(0.1)  # never below base

    # Connection-error backoff only kicks in after repeated failures.
    bo2 = BackoffController(0.1)
    for _ in range(3):
        bo2.on_connection_error()
    assert bo2.delay_seconds == pytest.approx(0.1)
    bo2.on_connection_error()
    assert bo2.delay_seconds > 0.1

    # Rate-limit escalation is capped.
    bo3 = BackoffController(0.1)
    for _ in range(10):
        bo3.on_rate_limited()
    assert bo3.delay_seconds <= 5.0
