"""End-to-end tests for robots.txt gating against a local aiohttp server.

The crawler queues robots.txt at the highest priority, but page requests are
served by separate workers concurrently. These tests assert that a page is
never sent before its origin's robots.txt result has been registered, that
disallowed paths never reach the server, and that a slow or missing
robots.txt cannot stall the crawl.
"""

import asyncio
import time
from typing import Dict, List

import pytest
from aiohttp import web
from yarl import URL

from feedsearch_crawler.crawler.crawler import Crawler
from feedsearch_crawler.crawler.request import Request


class _RecordingCrawler(Crawler):
    """Minimal crawler that records page responses."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.page_responses: List[tuple] = []

    async def process_item(self, item) -> None:
        pass

    async def parse_response_content(self, text):
        return text

    async def parse_response(self, request, response):
        self.page_responses.append((str(request.url), response.status_code))
        if False:  # pragma: no cover - keeps this an async generator
            yield


class _Recorder:
    """Server-side event log with monotonic timestamps."""

    def __init__(self) -> None:
        self.events: List[tuple] = []
        self.robots_served_at: float | None = None
        self.page_hits: Dict[str, int] = {}

    def record(self, name: str) -> None:
        self.events.append((name, time.monotonic()))

    def page_times(self) -> List[float]:
        return [t for name, t in self.events if name == "page"]


async def _start_server(recorder: _Recorder, robots_txt, robots_delay: float = 0.0):
    """Start a local server; robots_txt may be a string, an int status, or None (hang)."""

    async def robots_handler(request: web.Request) -> web.Response:
        if isinstance(robots_txt, int):
            recorder.record("robots_served")
            recorder.robots_served_at = time.monotonic()
            return web.Response(status=robots_txt, text="not found")
        if robots_txt is None:
            # Sleep past every timeout in the crawl, then answer so that
            # server cleanup does not wait for an abandoned request.
            await asyncio.sleep(1.5)
        if robots_delay:
            await asyncio.sleep(robots_delay)
        recorder.record("robots_served")
        recorder.robots_served_at = time.monotonic()
        return web.Response(text=robots_txt or "", content_type="text/plain")

    async def page_handler(request: web.Request) -> web.Response:
        recorder.record("page")
        recorder.page_hits[request.path] = recorder.page_hits.get(request.path, 0) + 1
        return web.Response(
            text=f"<html><head><title>{request.path}</title></head></html>",
            content_type="text/html",
        )

    async def sitemap_handler(request: web.Request) -> web.Response:
        return web.Response(status=404, text="no sitemap")

    app = web.Application()
    app.router.add_get("/robots.txt", robots_handler)
    # Static sitemap path before the catch-all, which would otherwise match it.
    app.router.add_get("/sitemap.xml", sitemap_handler)
    app.router.add_get("/{tail:.*}", page_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, port


async def _crawl(recorder: _Recorder, robots_txt, paths, robots_delay=0.0, **kwargs):
    runner, port = await _start_server(recorder, robots_txt, robots_delay)
    try:
        crawler = _RecordingCrawler(
            concurrency=kwargs.pop("concurrency", 4),
            request_timeout=kwargs.pop("request_timeout", 2.0),
            total_timeout=kwargs.pop("total_timeout", 10.0),
            delay=0,
            requests_per_host_per_sec=0,
            max_retries=kwargs.pop("max_retries", 0),
            **kwargs,
        )
        await crawler.crawl([f"http://127.0.0.1:{port}{path}" for path in paths])
        return crawler, port
    finally:
        await runner.cleanup()


class TestRobotsGatingEndToEnd:
    """Concurrency between the robots.txt fetch and page requests."""

    @pytest.mark.asyncio
    async def test_pages_wait_for_delayed_robots(self):
        """No page is sent before the delayed robots.txt has been served."""
        recorder = _Recorder()
        paths = ["/p1", "/p2", "/p3", "/p4", "/p5"]

        _crawler, _ = await _crawl(
            recorder, "User-agent: *\nAllow: /", paths, robots_delay=0.3
        )

        assert recorder.robots_served_at is not None
        page_times = recorder.page_times()
        assert len(page_times) == len(paths), recorder.events
        # Every page request is served after the robots.txt response.
        assert min(page_times) >= recorder.robots_served_at

    @pytest.mark.asyncio
    async def test_pages_wait_even_with_single_worker(self):
        """With concurrency=1 the gating must not deadlock behind the slot."""
        recorder = _Recorder()

        _crawler, _ = await _crawl(
            recorder,
            "User-agent: *\nAllow: /",
            ["/only"],
            robots_delay=0.2,
            concurrency=1,
        )

        assert recorder.robots_served_at is not None
        page_times = recorder.page_times()
        assert len(page_times) == 1
        assert page_times[0] >= recorder.robots_served_at

    @pytest.mark.asyncio
    async def test_disallowed_path_is_never_requested(self):
        """A Disallow rule stops the request before it reaches the server."""
        recorder = _Recorder()

        crawler, _ = await _crawl(
            recorder,
            "User-agent: *\nDisallow: /private",
            ["/private/secret", "/public/page"],
        )

        assert "/private/secret" not in recorder.page_hits
        assert recorder.page_hits.get("/public/page") == 1
        assert crawler.stats_collector.robots_txt_blocks >= 1
        # The blocked request is not counted as a successful response.
        successful_urls = [
            url for url, status in crawler.page_responses if status == 200
        ]
        assert not any("/private/secret" in url for url in successful_urls)

    @pytest.mark.asyncio
    async def test_robots_404_pages_are_allowed(self):
        """A 404 robots.txt means no rules: pages still load, crawl ends."""
        recorder = _Recorder()

        start = time.perf_counter()
        _crawler, _ = await _crawl(recorder, 404, ["/p1", "/p2"])
        duration = time.perf_counter() - start

        assert recorder.page_hits.get("/p1") == 1
        assert recorder.page_hits.get("/p2") == 1
        assert duration < 5

    @pytest.mark.asyncio
    async def test_robots_timeout_pages_are_allowed_with_bounded_wait(self):
        """A hanging robots.txt times out and pages proceed, bounding total time."""
        recorder = _Recorder()

        start = time.perf_counter()
        _crawler, _ = await _crawl(
            recorder,
            None,  # never answers
            ["/p1"],
            request_timeout=0.2,
            robots_timeout=0.3,
            total_timeout=5.0,
        )
        duration = time.perf_counter() - start

        assert recorder.page_hits.get("/p1") == 1
        # Bounded by the robots wait + request timeout, not the robots hang.
        assert duration < 3

    @pytest.mark.asyncio
    async def test_respect_robots_false_does_not_wait_or_block(self):
        """With respect_robots disabled, pages are sent without waiting."""
        recorder = _Recorder()

        _crawler, _ = await _crawl(
            recorder,
            "User-agent: *\nDisallow: /",
            ["/private/page"],
            robots_delay=0.3,
            respect_robots=False,
        )

        # The page was fetched despite the disallow rule and before robots
        # had been served.
        assert recorder.page_hits.get("/private/page") == 1
        page_times = recorder.page_times()
        assert page_times[0] < recorder.robots_served_at


class TestBlockedRequestAccounting:
    """A blocked request has a checkable outcome and costs no download slot."""

    @pytest.mark.asyncio
    async def test_blocked_request_skips_download_slot_and_success_stats(self):
        from feedsearch_crawler.crawler.middleware.robots import RobotsMiddleware

        crawler = _RecordingCrawler(concurrency=1, total_timeout=1.0)
        crawler._download_semaphore = asyncio.Semaphore(1)
        crawler._parse_semaphore = asyncio.Semaphore(1)

        middleware: RobotsMiddleware = crawler._robots_middleware
        middleware.register_robots_txt(
            URL("https://example.com/robots.txt"), "User-agent: *\nDisallow: /private"
        )

        class _MustNotFetch:
            async def fetch(self, request):  # pragma: no cover - must not run
                raise AssertionError("blocked request reached the downloader")

        crawler._downloader = _MustNotFetch()

        # Occupy the only download slot: the blocked request must still finish,
        # which proves it never tries to take the slot.
        await crawler._download_semaphore.acquire()
        try:
            request = Request(url=URL("https://example.com/private/x"))
            await asyncio.wait_for(crawler._handle_request(request), timeout=1.0)
        finally:
            crawler._download_semaphore.release()

        assert crawler.stats_collector.requests_successful == 0
        assert crawler.stats_collector.requests_failed == 1
        assert crawler.stats_collector.robots_txt_blocks == 1
        assert crawler.stats_collector.errors_by_category.get("robots") == 1
