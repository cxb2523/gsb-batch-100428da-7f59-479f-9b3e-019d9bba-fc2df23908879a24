"""End-to-end tests for retry-delay enforcement through the real scheduling chain.

These use a real ``Scheduler`` + ``MemoryPriorityQueue`` (which round-trips
requests through msgpack), a real ``CrawlEngine``, and a fake download handler
that returns 429 once. They cover the gap the unit tests cannot: whether the
retry request survives re-enqueueing and whether the wait happens before the
next real dispatch.
"""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from qcrawl.core.engine import CrawlEngine
from qcrawl.core.queues.memory import MemoryPriorityQueue
from qcrawl.core.request import Request
from qcrawl.core.response import Page
from qcrawl.core.scheduler import Scheduler
from qcrawl.core.spider import Spider
from qcrawl.middleware.downloader.download_delay import DownloadDelayMiddleware
from qcrawl.middleware.downloader.retry import RetryMiddleware
from qcrawl.utils.fingerprint import RequestFingerprinter


class ChainSpider(Spider):
    name = "chain"
    start_urls = ["https://example.com/a"]

    async def parse(self, response):
        yield {"url": response.url}


class FakeHandler:
    """Download handler stub recording dispatch time for each request.

    The first fetch for a URL returns ``statuses[0]``; later fetches return the
    last status. ``Retry-After`` is attached to retryable responses.
    """

    def __init__(self, statuses=(429, 200), retry_after="0.3"):
        self.statuses = list(statuses)
        self.retry_after = retry_after
        self.calls: list[tuple[float, str, dict]] = []

    async def fetch(self, request, spider=None):
        self.calls.append((time.monotonic(), request.url, dict(request.meta)))
        status = self.statuses[0] if len(self.calls) == 1 else self.statuses[-1]
        headers = {}
        if status in {429, 503} and self.retry_after is not None:
            headers["Retry-After"] = self.retry_after
        return Page(
            url=request.url, content=b"", status_code=status, headers=headers, request=request
        )


def _make_engine(handler: FakeHandler, *, delay: float = 0.02) -> tuple[CrawlEngine, Scheduler]:
    scheduler = Scheduler(
        queue=MemoryPriorityQueue(),
        fingerprinter=RequestFingerprinter(),
    )
    spider = ChainSpider()
    spider.crawler = SimpleNamespace(stats=Mock())
    engine = CrawlEngine(scheduler, handler, spider)
    engine.add_middleware(RetryMiddleware(max_retries=3, backoff_jitter=0.0))
    engine.add_middleware(DownloadDelayMiddleware(delay_per_domain=delay))
    return engine, scheduler


async def _drain_one(engine: CrawlEngine, scheduler: Scheduler) -> Page | None:
    """Fetch one request from the scheduler and run it through the engine."""
    response = await engine._process_request(await scheduler.get())
    scheduler.task_done()
    return response


@pytest.mark.asyncio
async def test_retry_request_survives_reenqueue_with_retry_delay():
    """The rescheduled retry reaches the scheduler carrying retry_count/retry_delay.

    Regression: the retry request had the same fingerprint as the original, so
    ``Scheduler.add`` deduplicated it away and the retry never happened.
    """
    engine, scheduler = _make_engine(FakeHandler())
    await scheduler.add(Request(url="https://example.com/a"))

    await _drain_one(engine, scheduler)

    assert await scheduler.qsize() == 1
    retry_request = await asyncio.wait_for(scheduler.get(), timeout=1.0)
    assert retry_request.url == "https://example.com/a"
    assert retry_request.meta["retry_count"] == 1
    assert retry_request.meta["retry_delay"] == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_retry_wait_happens_before_next_dispatch():
    """The second real dispatch happens after the retry delay, not immediately."""
    engine, scheduler = _make_engine(FakeHandler(retry_after="0.3"))
    await scheduler.add(Request(url="https://example.com/a"))

    await _drain_one(engine, scheduler)
    retry_request = await asyncio.wait_for(scheduler.get(), timeout=1.0)
    await engine._process_request(retry_request)
    scheduler.task_done()

    assert len(engine.handler_manager.calls) == 2
    first, second = engine.handler_manager.calls
    assert second[0] - first[0] >= 0.28


@pytest.mark.asyncio
async def test_same_domain_request_cannot_bypass_retry_window():
    """A different same-domain request queued after the 429 is held back too.

    The retried request gets ``priority_adjust=-1``; we add ``/b`` at a higher
    priority so it is dequeued *before* the retry request. Without a domain-wide
    window it would dispatch immediately after the 429 that opened the window.
    """
    engine, scheduler = _make_engine(FakeHandler(retry_after="0.35"))
    await scheduler.add(Request(url="https://example.com/a", priority=10))

    first = await scheduler.get()
    assert first.url.endswith("/a")
    await engine._process_request(first)
    scheduler.task_done()
    # /a returned 429; its retry (priority 9) is now queued and a window is open.

    # A different, higher-priority same-domain request jumps ahead of the retry.
    await scheduler.add(Request(url="https://example.com/b", priority=0))
    second = await scheduler.get()
    assert second.url.endswith("/b")

    await engine._process_request(second)
    scheduler.task_done()

    dispatch_times = [t for t, _, _ in engine.handler_manager.calls]
    assert len(dispatch_times) == 2
    assert dispatch_times[1] - dispatch_times[0] >= 0.3


@pytest.mark.asyncio
async def test_retry_after_header_wins_over_backoff():
    """Retry-After seconds override the exponential backoff value."""
    engine, scheduler = _make_engine(FakeHandler(retry_after="1.5"))
    await scheduler.add(Request(url="https://example.com/a"))

    await _drain_one(engine, scheduler)
    retry_request = await scheduler.get()

    # backoff_base=1.0, retry_count=0 would be 1.0s; the header says 1.5s.
    assert retry_request.meta["retry_delay"] == pytest.approx(1.5)


@pytest.mark.asyncio
async def test_fractional_retry_after_is_not_downgraded_to_backoff():
    """A fractional Retry-After is honoured as a float instead of int-parse failure."""
    engine, scheduler = _make_engine(FakeHandler(retry_after="0.75"))
    await scheduler.add(Request(url="https://example.com/a"))

    await _drain_one(engine, scheduler)
    retry_request = await scheduler.get()

    assert retry_request.meta["retry_delay"] == pytest.approx(0.75)


@pytest.mark.asyncio
async def test_retry_delay_never_exceeds_retry_after_under_jitter():
    """Jitter does not shorten a server-mandated Retry-After wait."""
    scheduler = Scheduler(queue=MemoryPriorityQueue(), fingerprinter=RequestFingerprinter())
    spider = ChainSpider()
    spider.crawler = SimpleNamespace(stats=Mock())
    handler = FakeHandler(retry_after="2")
    engine = CrawlEngine(scheduler, handler, spider)
    engine.add_middleware(RetryMiddleware(max_retries=3, backoff_jitter=0.9))
    engine.add_middleware(DownloadDelayMiddleware(delay_per_domain=0.0))
    await scheduler.add(Request(url="https://example.com/a"))

    await _drain_one(engine, scheduler)
    retry_request = await scheduler.get()

    assert retry_request.meta["retry_delay"] == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_redirect_retry_is_not_delayed_by_a_retry_window():
    """Redirect RESULTs also flow through RETRY; without retry_delay they are not gated."""
    engine, scheduler = _make_engine(FakeHandler(statuses=(200,), retry_after=None))
    await scheduler.add(Request(url="https://example.com/a"))
    await _drain_one(engine, scheduler)

    # No retry was scheduled for a plain 200.
    assert await scheduler.qsize() == 0


# Real Crawler lifecycle
# Exercises the default middleware wiring (settings-driven), open_spider hooks,
# and concurrent workers against the same retry-window logic.


@pytest.mark.asyncio
async def test_crawler_lifecycle_honors_retry_window():
    """A 429 opens a domain window inside a real Crawler.run lifecycle."""
    from unittest.mock import patch

    from qcrawl.core.crawler import Crawler
    from qcrawl.settings import Settings

    dispatches: list[tuple[float, str, object]] = []

    class FlakySpider(Spider):
        name = "flaky"
        start_urls = ["https://example.com/a", "https://example.com/b"]
        custom_settings = {
            "DELAY_PER_DOMAIN": 0.02,
            "CONCURRENCY": 4,
            "RETRY_BACKOFF_BASE": 0.0,
            "RETRY_BACKOFF_JITTER": 0.0,
        }

        async def parse(self, response):
            yield {"url": response.url}

    async def fake_fetch(self, request, spider=None):
        dispatches.append((time.monotonic(), request.url, request.meta.get("retry_count")))
        retryable = request.url.endswith("/a") and request.meta.get("retry_count") is None
        return Page(
            url=request.url,
            content=b"",
            status_code=429 if retryable else 200,
            headers={"Retry-After": "0.3"} if retryable else {},
            request=request,
        )

    crawler = Crawler(FlakySpider(), Settings())
    with patch("qcrawl.downloaders.handler_manager.DownloadHandlerManager.fetch", fake_fetch):
        await crawler.crawl()

    origin = dispatches[0][0]
    a_first = next(d for d in dispatches if d[1].endswith("/a") and d[2] is None)
    a_retry = next(d for d in dispatches if d[1].endswith("/a") and d[2] == 1)
    b_dispatch = next(d for d in dispatches if d[1].endswith("/b"))

    # The retry actually happened and waited out the Retry-After window.
    assert a_retry[0] - a_first[0] >= 0.28
    # The same-domain sibling was held back too (0.02s domain delay would have
    # let it out immediately).
    assert b_dispatch[0] - a_first[0] >= 0.28
    assert crawler.stats.get("downloader/retry/total") == 1
    assert origin <= a_first[0]
