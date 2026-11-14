"""Tests for RobotsCache.crawl_delay(), fetch-outcome tracking and crawler integration."""

from __future__ import annotations

import asyncio
import logging
import time

import httpx
import pytest
import respx

from anansi.robots import RobotsCache, RobotsFetchOutcome, RobotsOutcome


_ROBOTS_WITH_DELAY = """\
User-agent: *
Crawl-delay: 5
Disallow: /private/
"""

_ROBOTS_NO_DELAY = """\
User-agent: *
Disallow: /private/
"""

_ROBOTS_AGENT_SPECIFIC = """\
User-agent: mybot
Crawl-delay: 3

User-agent: *
Crawl-delay: 10
"""


@pytest.fixture
def robots_mock():
    with respx.mock:
        yield


async def test_crawl_delay_returns_float(robots_mock) -> None:
    respx.get("https://example.com/robots.txt").mock(
        return_value=httpx.Response(200, text=_ROBOTS_WITH_DELAY)
    )
    cache = RobotsCache(user_agent="*")
    delay = await cache.crawl_delay("https://example.com/page")
    assert delay == 5.0


async def test_crawl_delay_no_directive_returns_none(robots_mock) -> None:
    respx.get("https://example.com/robots.txt").mock(
        return_value=httpx.Response(200, text=_ROBOTS_NO_DELAY)
    )
    cache = RobotsCache(user_agent="*")
    delay = await cache.crawl_delay("https://example.com/page")
    assert delay is None


async def test_crawl_delay_fallback_to_wildcard(robots_mock) -> None:
    respx.get("https://example.com/robots.txt").mock(
        return_value=httpx.Response(200, text=_ROBOTS_AGENT_SPECIFIC)
    )
    # Using an agent that has no explicit Crawl-delay — should fall back to *
    cache = RobotsCache(user_agent="unknownbot")
    delay = await cache.crawl_delay("https://example.com/page")
    assert delay == 10.0


async def test_crawl_delay_reuses_cache(robots_mock) -> None:
    """crawl_delay() must not issue a second HTTP request within TTL."""
    route = respx.get("https://example.com/robots.txt").mock(
        return_value=httpx.Response(200, text=_ROBOTS_WITH_DELAY)
    )
    cache = RobotsCache(user_agent="*")
    await cache.crawl_delay("https://example.com/page1")
    await cache.crawl_delay("https://example.com/page2")
    assert route.call_count == 1


async def test_crawl_delay_unreachable_robots_returns_none(robots_mock) -> None:
    respx.get("https://example.com/robots.txt").mock(
        return_value=httpx.Response(404)
    )
    cache = RobotsCache(user_agent="*")
    delay = await cache.crawl_delay("https://example.com/page")
    assert delay is None


async def test_crawler_applies_crawl_delay_to_domain_gap(tmp_path) -> None:
    """Crawler should update domain throttle gap from robots.txt Crawl-delay."""
    from anansi.spider.crawler import Crawler
    from anansi.spider.spider import Spider

    class _S(Spider):
        name = "robots_test"
        start_urls = ["https://example.com/"]

    with respx.mock:
        respx.get("https://example.com/robots.txt").mock(
            return_value=httpx.Response(200, text=_ROBOTS_WITH_DELAY)
        )
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(200, text="<html><body>hi</body></html>")
        )

        crawler = Crawler(
            _S,
            delay=0.0,
            delay_jitter=0.0,
            domain_delay=0.0,
            respect_robots=True,
            auto_browser=False,
            db_path=tmp_path / "crawls.db",
            adaptive_rate_limiting=False,
        )
        _ = [item async for item in crawler.run()]

    assert crawler._domain_crawl_delays.get("example.com") == 5.0
    assert crawler._domain_throttle._gaps["example.com"] == 5.0


async def test_crawler_does_not_reduce_gap_below_domain_delay(tmp_path) -> None:
    """Domain gap should not be overridden when crawl_delay < current gap."""
    from anansi.spider.crawler import Crawler
    from anansi.spider.spider import Spider

    class _S(Spider):
        name = "robots_test2"
        start_urls = ["https://example.com/"]

    robots_txt = "User-agent: *\nCrawl-delay: 1\n"

    with respx.mock:
        respx.get("https://example.com/robots.txt").mock(
            return_value=httpx.Response(200, text=robots_txt)
        )
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(200, text="<html><body>hi</body></html>")
        )

        crawler = Crawler(
            _S,
            delay=0.0,
            delay_jitter=0.0,
            domain_delay=5.0,  # existing gap is 5s — higher than crawl_delay=1
            respect_robots=True,
            auto_browser=False,
            db_path=tmp_path / "crawls.db",
            adaptive_rate_limiting=True,
        )
        _ = [item async for item in crawler.run()]

    # Gap must not be reduced below the configured domain_delay
    assert crawler._domain_throttle._gaps["example.com"] >= 1.0


async def test_robots_single_flight_fetches_once(robots_mock) -> None:
    route = respx.get("https://example.com/robots.txt").mock(
        return_value=httpx.Response(200, text=_ROBOTS_WITH_DELAY)
    )
    cache = RobotsCache(user_agent="*")
    results = await asyncio.gather(
        *(cache.allowed("https://example.com/page") for _ in range(10))
    )
    assert all(results)
    assert route.call_count == 1  # 10 concurrent calls → one robots.txt fetch


async def test_robots_cache_is_bounded(robots_mock) -> None:
    respx.get(url__regex=r"https://.*/robots\.txt").mock(
        return_value=httpx.Response(200, text=_ROBOTS_NO_DELAY)
    )
    cache = RobotsCache(user_agent="*")
    cache._MAX_ENTRIES = 5
    for i in range(20):
        await cache.allowed(f"https://host{i}.com/page")
    assert len(cache._cache) <= 5


# ── Fetch-outcome observability ──────────────────────────────────────────────


async def test_status_ok_on_successful_200_fetch(robots_mock) -> None:
    """A 200 robots.txt records outcome=ok with a timestamp and reason."""
    respx.get("https://example.com/robots.txt").mock(
        return_value=httpx.Response(200, text=_ROBOTS_NO_DELAY)
    )
    cache = RobotsCache(user_agent="*")
    assert cache.status_for("https://example.com/page") is None  # never fetched

    before = time.time()
    assert await cache.allowed("https://example.com/page") is True
    after = time.time()

    status = cache.status_for("https://example.com/page")
    assert status is not None
    # First query after the fetch is served from the fresh cache entry.
    assert status.outcome is RobotsOutcome.CACHED
    assert status.fetch_outcome is RobotsFetchOutcome.OK
    assert "200" in status.reason
    assert status.fetched_at is not None
    assert before <= status.fetched_at <= after


async def test_status_unreachable_when_fetch_raises(robots_mock) -> None:
    """A fetch exception records outcome=unreachable but still allows the URL."""
    respx.get("https://broken.example/robots.txt").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    cache = RobotsCache(user_agent="*")

    allowed = await cache.allowed("https://broken.example/private/secret")
    assert allowed is True  # decision unchanged: missing rules → allow

    status = cache.status_for("https://broken.example/page")
    assert status is not None
    assert status.outcome is RobotsOutcome.CACHED
    assert status.fetch_outcome is RobotsFetchOutcome.UNREACHABLE
    assert "ConnectError" in status.reason
    assert status.fetched_at is not None


async def test_status_ok_for_explicit_4xx_response(robots_mock) -> None:
    """A definitive 4xx answer is a successful (rule-less) fetch, not unreachable."""
    respx.get("https://gone.example/robots.txt").mock(
        return_value=httpx.Response(404)
    )
    cache = RobotsCache(user_agent="*")
    assert await cache.allowed("https://gone.example/page") is True

    status = cache.status_for("https://gone.example/page")
    assert status is not None
    assert status.fetch_outcome is RobotsFetchOutcome.OK
    assert "404" in status.reason


async def test_cache_hit_is_reported_as_cached_outcome(robots_mock) -> None:
    """Repeated allowed() calls reuse the entry and report outcome=cached."""
    route = respx.get("https://example.com/robots.txt").mock(
        return_value=httpx.Response(200, text=_ROBOTS_NO_DELAY)
    )
    cache = RobotsCache(user_agent="*")
    await cache.allowed("https://example.com/a")
    await cache.allowed("https://example.com/b")
    assert route.call_count == 1

    status = cache.status_for("https://example.com/b")
    assert status is not None
    assert status.outcome is RobotsOutcome.CACHED
    assert status.fetch_outcome is RobotsFetchOutcome.OK


async def test_expired_entry_refetches_and_updates_outcome(robots_mock, monkeypatch) -> None:
    """After TTL expiry the next allowed() re-fetches and refreshes the outcome."""
    import anansi.robots as robots_mod

    fake_now = [1000.0]
    monkeypatch.setattr(robots_mod.time, "monotonic", lambda: fake_now[0])

    responses: list[httpx.Response | Exception] = [
        httpx.ConnectError("down"),
        httpx.Response(200, text=_ROBOTS_NO_DELAY),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        value = responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    route = respx.get("https://example.com/robots.txt").mock(side_effect=handler)
    cache = RobotsCache(user_agent="*", ttl=10.0)

    await cache.allowed("https://example.com/page")
    first = cache.status_for("https://example.com/page")
    assert first.fetch_outcome is RobotsFetchOutcome.UNREACHABLE

    # Within TTL: cache hit, no second fetch.
    fake_now[0] += 5.0
    await cache.allowed("https://example.com/page")
    assert route.call_count == 1
    assert cache.status_for("https://example.com/page").outcome is RobotsOutcome.CACHED

    # Expire the entry: the stale status still reports the last fetch outcome.
    fake_now[0] += 6.0
    expired = cache.status_for("https://example.com/page")
    assert expired is not None
    assert expired.outcome is RobotsOutcome.UNREACHABLE

    first_ts = first.fetched_at
    await cache.allowed("https://example.com/page")
    assert route.call_count == 2

    refreshed = cache.status_for("https://example.com/page")
    assert refreshed is not None
    assert refreshed.fetch_outcome is RobotsFetchOutcome.OK
    assert refreshed.fetched_at > (first_ts or 0.0)


# ── Crawler integration: rule-missing allowance must be distinguishable ─────


async def test_crawler_unreachable_robots_still_crawls_and_logs_marker(
    tmp_path, caplog
) -> None:
    """Unreachable robots.txt must not stop the crawl, and the fallback-allow
    decision must be logged with the explicit 规则缺失、按允许处理 marker."""
    from anansi.spider.crawler import Crawler
    from anansi.spider.spider import Spider

    class _S(Spider):
        name = "robots_unreachable_test"
        start_urls = ["https://example.com/"]

        async def parse(self, response):
            from anansi.core import Item
            yield Item(data={"url": response.url})

    with respx.mock:
        respx.get("https://example.com/robots.txt").mock(
            side_effect=httpx.ConnectTimeout("timed out")
        )
        page_route = respx.get("https://example.com/").mock(
            return_value=httpx.Response(200, text="<html><body>hi</body></html>")
        )

        crawler = Crawler(
            _S,
            delay=0.0,
            delay_jitter=0.0,
            domain_delay=0.0,
            respect_robots=True,
            auto_browser=False,
            db_path=tmp_path / "crawls.db",
            adaptive_rate_limiting=False,
        )
        with caplog.at_level(logging.WARNING):
            items = [item async for item in crawler.run()]

    # Crawl continued despite the missing robots.txt — the page was actually
    # fetched (once for host warm-up, once for the real request).
    assert page_route.call_count >= 1
    assert len(items) == 1

    status = crawler._robots.status_for("https://example.com/")
    assert status is not None
    assert status.fetch_outcome is RobotsFetchOutcome.UNREACHABLE

    marker_records = [
        r for r in caplog.records
        if "规则缺失、按允许处理" in r.message and "example.com" in r.message
    ]
    assert marker_records, "expected an explicit rule-missing-allow warning"
    assert any(r.levelno == logging.WARNING for r in marker_records)
    # The log carries the machine-discriminable outcome token.
    assert any("unreachable" in r.message for r in marker_records)


async def test_crawler_rule_based_block_logs_outcome_without_marker(
    tmp_path, caplog
) -> None:
    """A genuine Disallow rule skips the URL and logs the origin's ok outcome;
    the rule-missing marker must not appear."""
    from anansi.spider.crawler import Crawler
    from anansi.spider.spider import Spider

    class _S(Spider):
        name = "robots_block_test"
        start_urls = ["https://example.com/private/data"]

        async def parse(self, response):
            from anansi.core import Item
            yield Item(data={"url": response.url})

    with respx.mock:
        respx.get("https://example.com/robots.txt").mock(
            return_value=httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
        )
        page_route = respx.get("https://example.com/private/data").mock(
            return_value=httpx.Response(200, text="<html><body>secret</body></html>")
        )

        crawler = Crawler(
            _S,
            delay=0.0,
            delay_jitter=0.0,
            domain_delay=0.0,
            respect_robots=True,
            auto_browser=False,
            db_path=tmp_path / "crawls.db",
            adaptive_rate_limiting=False,
        )
        with caplog.at_level(logging.DEBUG):
            items = [item async for item in crawler.run()]

    assert page_route.call_count == 0  # blocked by an explicit rule
    assert items == []
    status = crawler._robots.status_for("https://example.com/private/data")
    assert status is not None
    assert status.fetch_outcome is RobotsFetchOutcome.OK

    skip_records = [r for r in caplog.records if "disallows" in r.message]
    assert skip_records
    assert any("outcome=" in r.message for r in skip_records)
    assert not any("规则缺失、按允许处理" in r.message for r in caplog.records)
