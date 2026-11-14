"""Async robots.txt cache — one RobotFileParser per domain origin, 1-hour TTL."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser


class RobotsFetchOutcome(str, Enum):
    """How the most recent robots.txt fetch for an origin actually ended."""

    # A response was received and parsed — including explicit non-200 answers
    # such as 404, which the robot parser treats as "no rules present".
    OK = "ok"
    # The fetch itself failed (connection error, timeout, ...); no rules were
    # obtained, so the cache falls back to allowing everything.
    UNREACHABLE = "unreachable"


class RobotsOutcome(str, Enum):
    """Result reported by a status query for a single origin.

    ``ok`` / ``unreachable`` describe the underlying fetch when this query is
    backed by the result of an actual fetch; ``cached`` means no fetch was
    performed because a fresh cache entry was reused.
    """

    OK = "ok"
    UNREACHABLE = "unreachable"
    CACHED = "cached"


@dataclass(frozen=True)
class RobotsStatus:
    """Read-only snapshot of the last known robots.txt state for one origin."""

    outcome: RobotsOutcome
    # The fetch-level outcome behind the entry. Equals ``outcome`` right after a
    # fetch and stays populated when ``outcome`` is RobotsOutcome.CACHED.
    fetch_outcome: RobotsFetchOutcome | None
    reason: str
    # Wall-clock time (time.time()) of the last fetch, or None when this origin
    # has never been fetched.
    fetched_at: float | None


class RobotsCache:
    """
    Fetches and caches robots.txt per domain. Thread-safe for async use.

    On fetch error or non-200 response, assumes all URLs are allowed so
    scraping is not silently blocked by transient network issues. The basis of
    that decision is observable: every origin keeps the outcome of its most
    recent fetch (``RobotsFetchOutcome.OK`` — including explicit 4xx answers —
    or ``RobotsFetchOutcome.UNREACHABLE`` for failed/timed-out fetches), a
    short reason summary and a timestamp. Query them read-only via
    :meth:`status_for`.

    A per-origin lock provides single-flight: the first burst of concurrent
    requests to a domain fetches robots.txt once, and the rest await that result
    instead of each issuing an identical fetch. The cache is bounded so a crawl
    over many hosts does not grow it without limit.
    """

    _MAX_ENTRIES = 512

    def __init__(self, user_agent: str = "*", ttl: float = 3600.0) -> None:
        self._cache: dict[str, tuple[RobotFileParser, float, RobotsStatus]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._ua = user_agent
        self._ttl = ttl

    @staticmethod
    def _origin_of(url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def _origin_lock(self, origin: str) -> asyncio.Lock:
        lock = self._locks.get(origin)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[origin] = lock
        return lock

    def _store(self, origin: str, rp: RobotFileParser, now: float, status: RobotsStatus) -> None:
        self._cache[origin] = (rp, now + self._ttl, status)
        if len(self._cache) <= self._MAX_ENTRIES:
            return
        # Drop expired entries first, then the soonest-to-expire, until under cap.
        for key in [k for k, (_, exp, _) in self._cache.items() if exp <= now]:
            self._cache.pop(key, None)
            self._locks.pop(key, None)
        while len(self._cache) > self._MAX_ENTRIES:
            oldest = min(self._cache, key=lambda k: self._cache[k][1])
            self._cache.pop(oldest, None)
            self._locks.pop(oldest, None)

    def _fetch_status(self, origin: str) -> RobotsStatus | None:
        """Return the fetch-level status recorded for *origin*, or None."""
        entry = self._cache.get(origin)
        return entry[2] if entry is not None else None

    def status_for(self, url: str) -> RobotsStatus | None:
        """Return a read-only status snapshot for *url*'s origin.

        - Fresh cache entry: ``outcome`` is :attr:`RobotsOutcome.CACHED` while
          ``fetch_outcome`` still reports how the underlying fetch ended.
        - Expired or evicted entry: the last fetch-level status is returned
          unchanged (its ``outcome`` is ``ok``/``unreachable``).
        - Origin never seen: ``None``.
        """
        origin = self._origin_of(url)
        entry = self._cache.get(origin)
        if entry is None:
            return None
        _, expires, status = entry
        if time.monotonic() < expires:
            return RobotsStatus(
                outcome=RobotsOutcome.CACHED,
                fetch_outcome=status.fetch_outcome,
                reason=status.reason,
                fetched_at=status.fetched_at,
            )
        return status

    async def allowed(self, url: str) -> bool:
        """Return True if robots.txt permits fetching *url*."""
        origin = self._origin_of(url)
        now = time.monotonic()

        cached = self._cache.get(origin)
        if cached and now < cached[1]:
            return cached[0].can_fetch(self._ua, url)

        async with self._origin_lock(origin):
            # Re-check: another coroutine may have fetched while we waited.
            now = time.monotonic()
            cached = self._cache.get(origin)
            if cached and now < cached[1]:
                return cached[0].can_fetch(self._ua, url)

            robots_url = urljoin(origin, "/robots.txt")
            rp = RobotFileParser(robots_url)
            # An un-parsed RobotFileParser denies every URL, so seed it with an
            # empty rule set: the fallback for a missing robots.txt is "allow".
            # A real 200 response replaces this with the parsed rules below.
            rp.parse([])
            status = RobotsStatus(
                outcome=RobotsOutcome.UNREACHABLE,
                fetch_outcome=RobotsFetchOutcome.UNREACHABLE,
                reason="robots.txt fetch not attempted yet",
                fetched_at=time.time(),
            )
            try:
                from anansi.fetchers.http import HTTPFetcher
                async with HTTPFetcher(timeout=10.0, max_retries=1) as f:
                    result = await f.fetch(robots_url)
                if result.status == 200:
                    rp.parse(result.html.splitlines())
                    reason = "robots.txt fetched and parsed (HTTP 200)"
                else:
                    # An explicit answer (e.g. 404/403): keep the empty rule set
                    # (everything allowed), but the response was reachable.
                    reason = f"robots.txt returned HTTP {result.status}; no rules applied"
                status = RobotsStatus(
                    outcome=RobotsOutcome.OK,
                    fetch_outcome=RobotsFetchOutcome.OK,
                    reason=reason,
                    fetched_at=time.time(),
                )
            except Exception as exc:
                # assume allowed if robots.txt is unreachable — record why so the
                # allowance can later be told apart from an explicit permit.
                status = RobotsStatus(
                    outcome=RobotsOutcome.UNREACHABLE,
                    fetch_outcome=RobotsFetchOutcome.UNREACHABLE,
                    reason=f"robots.txt unreachable ({type(exc).__name__}: {exc})",
                    fetched_at=time.time(),
                )

            self._store(origin, rp, now, status)
            return rp.can_fetch(self._ua, url)

    async def crawl_delay(self, url: str) -> float | None:
        """Return the Crawl-delay for url's domain from robots.txt, or None."""
        origin = self._origin_of(url)
        now = time.monotonic()
        cached = self._cache.get(origin)
        if not (cached and now < cached[1]):
            await self.allowed(url)  # populates cache as side effect
            cached = self._cache.get(origin)
        if not cached:
            return None
        rp = cached[0]
        delay = rp.crawl_delay(self._ua)
        if delay is None:
            delay = rp.crawl_delay("*")  # fall back to wildcard agent
        return float(delay) if delay is not None else None
