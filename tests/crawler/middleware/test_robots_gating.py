"""Tests for the robots.txt gating between fetching rules and sending requests.

These cover the guarantee that ordinary requests for an origin whose
robots.txt fetch is still in flight wait for the result (bounded) instead of
being allowed immediately, and that robots.txt requests themselves never wait
or get blocked by the rules they are fetching.
"""

import asyncio
import time

import pytest
from yarl import URL

from feedsearch_crawler.crawler.middleware.robots import RobotsMiddleware
from feedsearch_crawler.crawler.request import Request
from feedsearch_crawler.exceptions import RobotsBlockedError

ROBOTS_URL = URL("https://example.com/robots.txt")
PAGE_URL = URL("https://example.com/page")


class TestRobotsGating:
    """Requests wait for the robots.txt result of their origin."""

    @pytest.mark.asyncio
    async def test_request_waits_until_registration(self):
        """A request for an expected origin must not pass before registration."""
        middleware = RobotsMiddleware(user_agent="TestBot", timeout=5)
        middleware.expect_robots_txt(ROBOTS_URL)

        passed = asyncio.Event()

        async def send():
            await middleware.pre_request(Request(url=PAGE_URL))
            passed.set()

        task = asyncio.create_task(send())
        # Give the waiter a chance to run; it must still be blocked.
        await asyncio.sleep(0.05)
        assert not passed.is_set()

        middleware.register_robots_txt(ROBOTS_URL, "User-agent: *\nAllow: /")
        await asyncio.wait_for(task, timeout=1)
        assert passed.is_set()

    @pytest.mark.asyncio
    async def test_waiter_released_with_empty_result(self):
        """An empty robots.txt registers as no rules and releases waiters."""
        middleware = RobotsMiddleware(user_agent="TestBot", timeout=5)
        middleware.expect_robots_txt(ROBOTS_URL)

        task = asyncio.create_task(middleware.pre_request(Request(url=PAGE_URL)))
        await asyncio.sleep(0.02)
        assert not task.done()

        middleware.register_robots_txt(ROBOTS_URL, "")
        await asyncio.wait_for(task, timeout=1)
        assert middleware.cache[RobotsMiddleware.cache_key(ROBOTS_URL)] is None

    @pytest.mark.asyncio
    async def test_waiter_released_with_unavailable_result(self):
        """A failed fetch (registered as None) releases waiters."""
        middleware = RobotsMiddleware(user_agent="TestBot", timeout=5)
        middleware.expect_robots_txt(ROBOTS_URL)

        task = asyncio.create_task(middleware.pre_request(Request(url=PAGE_URL)))
        await asyncio.sleep(0.02)
        assert not task.done()

        middleware.register_robots_txt(ROBOTS_URL, None)
        await asyncio.wait_for(task, timeout=1)

    @pytest.mark.asyncio
    async def test_waiter_released_with_malformed_result(self):
        """Malformed content that parses to no rules releases waiters as allowed."""
        middleware = RobotsMiddleware(user_agent="TestBot", timeout=5)
        middleware.expect_robots_txt(ROBOTS_URL)

        task = asyncio.create_task(middleware.pre_request(Request(url=PAGE_URL)))
        await asyncio.sleep(0.02)
        assert not task.done()

        middleware.register_robots_txt(ROBOTS_URL, "\x00\x01<<<not robots txt>>>")
        await asyncio.wait_for(task, timeout=1)
        # Whatever the parser makes of garbage, it must not block the page.
        await middleware.pre_request(Request(url=PAGE_URL))

    @pytest.mark.asyncio
    async def test_waiter_released_when_parsing_raises(self, monkeypatch):
        """A parser exception is treated as no rules and releases waiters."""
        from urllib.robotparser import RobotFileParser

        def boom(self, lines):
            raise ValueError("bad robots.txt")

        monkeypatch.setattr(RobotFileParser, "parse", boom)

        middleware = RobotsMiddleware(user_agent="TestBot", timeout=5)
        middleware.expect_robots_txt(ROBOTS_URL)

        task = asyncio.create_task(middleware.pre_request(Request(url=PAGE_URL)))
        await asyncio.sleep(0.02)
        assert not task.done()

        middleware.register_robots_txt(ROBOTS_URL, "User-agent: *\nDisallow: /")
        await asyncio.wait_for(task, timeout=1)
        assert middleware.cache[RobotsMiddleware.cache_key(ROBOTS_URL)] is None
        # And the origin stays permissive afterwards.
        await middleware.pre_request(Request(url=PAGE_URL))

    @pytest.mark.asyncio
    async def test_wait_times_out_and_treats_as_no_rules(self):
        """Waiting is bounded: after timeout requests pass and stop waiting."""
        middleware = RobotsMiddleware(user_agent="TestBot", timeout=0.1)
        middleware.expect_robots_txt(ROBOTS_URL)

        start = time.perf_counter()
        await middleware.pre_request(Request(url=PAGE_URL))
        elapsed = time.perf_counter() - start

        assert 0.05 <= elapsed < 1.0
        # The origin was marked as having no rules, so a later request is instant.
        start = time.perf_counter()
        await middleware.pre_request(Request(url=URL("https://example.com/other")))
        assert time.perf_counter() - start < 0.05
        assert middleware.cache[RobotsMiddleware.cache_key(ROBOTS_URL)] is None

    @pytest.mark.asyncio
    async def test_disallowed_request_blocked_after_registration(self):
        """Once rules are registered, waiting requests are checked and blocked."""
        middleware = RobotsMiddleware(user_agent="TestBot", timeout=5)
        middleware.expect_robots_txt(ROBOTS_URL)

        task = asyncio.create_task(
            middleware.pre_request(Request(url=URL("https://example.com/private/x")))
        )
        await asyncio.sleep(0.02)
        assert not task.done()

        middleware.register_robots_txt(ROBOTS_URL, "User-agent: *\nDisallow: /private")
        with pytest.raises(RobotsBlockedError):
            await asyncio.wait_for(task, timeout=1)

    @pytest.mark.asyncio
    async def test_robots_request_never_waits(self):
        """The robots.txt request itself is exempt from waiting."""
        middleware = RobotsMiddleware(user_agent="TestBot", timeout=5)
        middleware.expect_robots_txt(ROBOTS_URL)

        start = time.perf_counter()
        await middleware.pre_request(Request(url=ROBOTS_URL))
        assert time.perf_counter() - start < 0.05

    @pytest.mark.asyncio
    async def test_robots_request_never_blocked_by_own_rules(self):
        """Disallow-all rules must still leave /robots.txt fetchable."""
        middleware = RobotsMiddleware(user_agent="TestBot", timeout=5)
        middleware.register_robots_txt(ROBOTS_URL, "User-agent: *\nDisallow: /")

        await middleware.pre_request(Request(url=ROBOTS_URL))

    @pytest.mark.asyncio
    async def test_unexpected_origin_does_not_wait(self):
        """Origins without a pending or registered fetch stay permissive."""
        middleware = RobotsMiddleware(user_agent="TestBot", timeout=5)
        middleware.expect_robots_txt(ROBOTS_URL)

        start = time.perf_counter()
        await middleware.pre_request(Request(url=URL("https://third-party.com/x")))
        assert time.perf_counter() - start < 0.05

    @pytest.mark.asyncio
    async def test_registration_keys_match_across_url_forms(self):
        """Retried/redirected registration keys must match later lookups.

        A trailing query, fragment, or an explicit default port must not change
        the key, or the registration would be invisible to ordinary requests.
        """
        middleware = RobotsMiddleware(user_agent="TestBot", timeout=5)
        middleware.expect_robots_txt(ROBOTS_URL)

        middleware.register_robots_txt(
            URL("https://EXAMPLE.com:443/robots.txt?retry=1#frag"),
            "User-agent: *\nDisallow: /private",
        )

        with pytest.raises(RobotsBlockedError):
            await middleware.pre_request(
                Request(url=URL("https://example.com/private/x"))
            )
        await middleware.pre_request(Request(url=URL("https://example.com/public")))
