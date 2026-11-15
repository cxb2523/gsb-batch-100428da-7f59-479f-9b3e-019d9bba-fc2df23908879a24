"""Tests for retry-delay enforcement in DownloadDelayMiddleware.

The retry request's ``meta['retry_delay']`` must become a real wait *before*
the request is dispatched, and the same wait must bound other same-domain
requests. A per-request pause alone lets same-domain requests through.
"""

import asyncio
import time

import pytest

from qcrawl.core.request import Request
from qcrawl.middleware.base import Action
from qcrawl.middleware.downloader.download_delay import DownloadDelayMiddleware

# Note: uses the 'spider' and 'http_response' fixtures from tests/middleware/conftest.py


@pytest.mark.asyncio
async def test_retry_delay_waits_at_dispatch_not_at_response(spider, http_response):
    """A retried request's wait happens in process_request (pre-download)."""
    middleware = DownloadDelayMiddleware(delay_per_domain=0.0)
    failed = Request(url="https://example.com/page")
    await middleware.process_request(failed, spider)
    await middleware.process_response(failed, http_response, spider)

    retry_request = failed.copy()
    retry_request.meta["retry_count"] = 1
    retry_request.meta["retry_delay"] = 0.2

    start = time.monotonic()
    await middleware.process_request(retry_request, spider)
    elapsed = time.monotonic() - start

    assert elapsed >= 0.18


@pytest.mark.asyncio
async def test_retry_delay_window_applies_to_other_same_domain_requests(spider, http_response):
    """Other requests to the retried domain wait out the retry window too."""
    middleware = DownloadDelayMiddleware(delay_per_domain=0.0)
    failed = Request(url="https://example.com/failed")
    await middleware.process_request(failed, spider)
    await middleware.process_response(failed, http_response, spider)

    retry_request = failed.copy()
    retry_request.meta["retry_delay"] = 0.25
    other = Request(url="https://example.com/other")

    start = time.monotonic()
    durations: dict[str, float] = {}

    async def timed(name, request):
        await middleware.process_request(request, spider)
        durations[name] = time.monotonic() - start

    # Both compete for the same domain gate.
    await asyncio.gather(timed("retry", retry_request), timed("other", other))

    # Whichever entered last had to wait out the published window.
    assert max(durations.values()) >= 0.22


@pytest.mark.asyncio
async def test_retry_window_bounds_request_already_queued_for_domain(spider, http_response):
    """A same-domain request queued behind the gate observes a window published later."""
    middleware = DownloadDelayMiddleware(delay_per_domain=0.0)
    first = Request(url="https://example.com/first")
    await middleware.process_request(first, spider)

    waiting = Request(url="https://example.com/waiting")
    retry_request = Request(url="https://example.com/retry", meta={"retry_delay": 0.2})

    async def enter(request):
        await middleware.process_request(request, spider)

    start = time.monotonic()
    await asyncio.gather(enter(waiting), enter(retry_request))
    elapsed = time.monotonic() - start

    assert elapsed >= 0.18


@pytest.mark.asyncio
async def test_on_retry_scheduled_publishes_domain_window(spider, http_response):
    """The engine hook opens the domain window before the retry is dequeued."""
    middleware = DownloadDelayMiddleware(delay_per_domain=0.0)
    first = Request(url="https://example.com/first")
    await middleware.process_request(first, spider)
    await middleware.process_response(first, http_response, spider)

    retry_request = first.copy()
    retry_request.meta["retry_count"] = 1
    retry_request.meta["retry_delay"] = 0.2

    await middleware.on_retry_scheduled(retry_request)

    other = Request(url="https://example.com/other")
    start = time.monotonic()
    await middleware.process_request(other, spider)
    elapsed = time.monotonic() - start

    assert elapsed >= 0.18


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "meta",
    [
        {},
        {"retry_delay": 0},
        {"retry_delay": "5"},
        {"retry_delay": True},
        {"retry_delay": None},
        {"retry_delay": -1},
    ],
)
async def test_on_retry_scheduled_ignores_missing_or_invalid_retry_delay(spider, meta):
    """The hook is a no-op when retry_delay is absent, zero or malformed."""
    middleware = DownloadDelayMiddleware(delay_per_domain=0.0)
    request = Request(url="https://example.com/page", meta=dict(meta))

    start = time.monotonic()
    await middleware.on_retry_scheduled(request)

    assert time.monotonic() - start < 0.05
    assert middleware._blocked_until == {}


@pytest.mark.asyncio
async def test_on_retry_scheduled_handles_request_without_meta(spider):
    """A request whose meta is not a dict does not break the engine hook."""
    middleware = DownloadDelayMiddleware(delay_per_domain=0.0)
    request = Request(url="https://example.com/page")
    request.meta = None  # type: ignore[assignment]

    await middleware.on_retry_scheduled(request)

    assert middleware._blocked_until == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_value", ["1", True, False, None, [], {}, float("nan"), float("inf"), -1, -0.5]
)
async def test_invalid_retry_delay_never_raises(spider, http_response, bad_value):
    """Malformed retry_delay values fall back to the domain delay, never raise."""
    middleware = DownloadDelayMiddleware(delay_per_domain=0.05)
    request1 = Request(url="https://example.com/page1")
    await middleware.process_request(request1, spider)
    await middleware.process_response(request1, http_response, spider)

    request2 = Request(url="https://example.com/page2", meta={"retry_delay": bad_value})

    start = time.monotonic()
    result = await middleware.process_request(request2, spider)
    elapsed = time.monotonic() - start

    assert result.action == Action.CONTINUE
    # Domain default applies; the bogus value must neither extend nor break it.
    assert 0.04 <= elapsed < 0.2


@pytest.mark.asyncio
async def test_zero_retry_delay_falls_back_to_domain_delay(spider, http_response):
    """retry_delay == 0 means "no retry window", so the domain default applies."""
    middleware = DownloadDelayMiddleware(delay_per_domain=0.1)
    request1 = Request(url="https://example.com/page1")
    await middleware.process_request(request1, spider)
    await middleware.process_response(request1, http_response, spider)

    request2 = Request(url="https://example.com/page2", meta={"retry_delay": 0})

    start = time.monotonic()
    await middleware.process_request(request2, spider)
    elapsed = time.monotonic() - start

    assert elapsed >= 0.09


@pytest.mark.asyncio
async def test_float_retry_delay_is_accepted(spider, http_response):
    """Fractional delays survive as floats instead of being integer-parsed."""
    middleware = DownloadDelayMiddleware(delay_per_domain=0.0)
    request1 = Request(url="https://example.com/page1")
    await middleware.process_request(request1, spider)
    await middleware.process_response(request1, http_response, spider)

    request2 = Request(url="https://example.com/page2", meta={"retry_delay": 0.15})
    start = time.monotonic()
    await middleware.process_request(request2, spider)
    elapsed = time.monotonic() - start

    assert elapsed >= 0.13


@pytest.mark.asyncio
async def test_concurrent_same_domain_requests_are_spaced(spider, http_response):
    """Concurrent same-domain requests cannot slip through the delay gate together."""
    middleware = DownloadDelayMiddleware(delay_per_domain=0.1)
    dispatched_at: list[float] = []
    start = time.monotonic()

    async def make_request(i: int) -> None:
        request = Request(url=f"https://example.com/page{i}")
        await middleware.process_request(request, spider)
        dispatched_at.append(time.monotonic() - start)
        await middleware.process_response(request, http_response, spider)

    await asyncio.gather(*(make_request(i) for i in range(4)))

    dispatched_at.sort()
    assert dispatched_at[0] < 0.05
    for earlier, later in zip(dispatched_at, dispatched_at[1:], strict=False):
        assert later - earlier >= 0.09


@pytest.mark.asyncio
async def test_retry_window_does_not_leak_across_domains(spider, http_response):
    """A retry window for one domain must not delay another domain."""
    middleware = DownloadDelayMiddleware(delay_per_domain=0.0)
    first = Request(url="https://example.com/first")
    await middleware.process_request(first, spider)
    await middleware.process_response(first, http_response, spider)

    retry_request = first.copy()
    retry_request.meta["retry_delay"] = 0.4
    await middleware.on_retry_scheduled(retry_request)

    other_domain = Request(url="https://other.com/page")
    start = time.monotonic()
    await middleware.process_request(other_domain, spider)
    elapsed = time.monotonic() - start

    assert elapsed < 0.05


@pytest.mark.asyncio
async def test_retry_window_never_shortens(spider):
    """A later, shorter retry delay does not truncate an already-published window."""
    middleware = DownloadDelayMiddleware(delay_per_domain=0.0)
    long_window = Request(url="https://example.com/page", meta={"retry_delay": 0.4})
    short_window = Request(url="https://example.com/page", meta={"retry_delay": 0.05})

    await middleware.on_retry_scheduled(long_window)
    first_deadline = middleware._blocked_until["example.com"]

    await middleware.on_retry_scheduled(short_window)

    assert middleware._blocked_until["example.com"] >= first_deadline


@pytest.mark.asyncio
async def test_engine_published_window_is_not_extended_at_dispatch(spider, http_response):
    """A retry that already waited in the queue is not made to wait twice.

    The engine hook opens the window when the retry is rescheduled. By the time
    ``process_request`` runs, the window may be (nearly) over; it must not open
    a fresh full-length one starting from that moment.
    """
    middleware = DownloadDelayMiddleware(delay_per_domain=0.0)
    first = Request(url="https://example.com/first")
    await middleware.process_request(first, spider)
    await middleware.process_response(first, http_response, spider)

    retry_request = Request(
        url="https://example.com/first",
        meta={"retry_count": 1, "retry_delay": 0.2},
    )
    await middleware.on_retry_scheduled(retry_request)

    # Time passes while the retry sits in the scheduler queue.
    await asyncio.sleep(0.22)

    start = time.monotonic()
    await middleware.process_request(retry_request, spider)
    elapsed = time.monotonic() - start

    assert elapsed < 0.05


@pytest.mark.asyncio
async def test_published_window_survives_stale_entry_cleanup(spider, http_response):
    """Another request clearing an elapsed entry does not double the retry's wait.

    A sibling request can observe the window as elapsed and delete the domain
    entry before the retry request is dispatched. The retry must then reuse its
    own absolute deadline (already passed) rather than opening a new window.
    """
    middleware = DownloadDelayMiddleware(delay_per_domain=0.0)
    retry_request = Request(
        url="https://example.com/retry",
        meta={"retry_count": 1, "retry_delay": 0.15},
    )
    await middleware.on_retry_scheduled(retry_request)

    # A sibling to the same domain dispatches after the window elapsed and
    # removes the stale entry as a side effect.
    await asyncio.sleep(0.16)
    sibling = Request(url="https://example.com/sibling")
    await middleware.process_request(sibling, spider)
    assert "example.com" not in middleware._blocked_until

    start = time.monotonic()
    await middleware.process_request(retry_request, spider)
    elapsed = time.monotonic() - start

    assert elapsed < 0.05


@pytest.mark.asyncio
async def test_retry_request_without_engine_hook_still_waits(spider, http_response):
    """A retry request that never met the engine hook still enforces its delay."""
    middleware = DownloadDelayMiddleware(delay_per_domain=0.0)
    first = Request(url="https://example.com/first")
    await middleware.process_request(first, spider)
    await middleware.process_response(first, http_response, spider)

    retry_request = Request(
        url="https://example.com/first",
        meta={"retry_count": 1, "retry_delay": 0.2},
    )

    start = time.monotonic()
    await middleware.process_request(retry_request, spider)
    elapsed = time.monotonic() - start

    assert elapsed >= 0.18


@pytest.mark.asyncio
async def test_window_markers_do_not_leak_into_outgoing_request(spider, http_response):
    """Private window bookkeeping is removed from request.meta before dispatch."""
    middleware = DownloadDelayMiddleware(delay_per_domain=0.0)
    retry_request = Request(
        url="https://example.com/page",
        meta={"retry_count": 1, "retry_delay": 0.05},
    )
    await middleware.on_retry_scheduled(retry_request)

    await middleware.process_request(retry_request, spider)

    assert not any(key.startswith("_retry_delay_window") for key in retry_request.meta)


@pytest.mark.asyncio
async def test_open_spider_clears_retry_windows(spider):
    """A new crawl starts with no inherited retry windows."""
    middleware = DownloadDelayMiddleware(delay_per_domain=0.0)
    request = Request(url="https://example.com/page", meta={"retry_delay": 1.0})
    await middleware.on_retry_scheduled(request)
    assert middleware._blocked_until

    await middleware.open_spider(spider)

    assert middleware._blocked_until == {}
