import asyncio
import logging
import math
import random
import time
from typing import TYPE_CHECKING

from qcrawl.middleware.base import DownloaderMiddleware, MiddlewareResult
from qcrawl.utils.url import get_domain

if TYPE_CHECKING:
    from qcrawl.core.request import Request
    from qcrawl.core.spider import Spider

logger = logging.getLogger(__name__)

_DOMAIN_KEY_META = "_domain_delay_key"
# Private meta keys recording the retry window the engine hook published, so
# `process_request` can reuse it instead of opening a fresh window from "now"
# (which would double the wait when the retry sat in the queue for a while).
_WINDOW_UNTIL_META = "_retry_delay_window_until"
_WINDOW_ATTEMPT_META = "_retry_delay_window_attempt"


def _coerce_retry_delay(value: object) -> float | None:
    """Return a usable non-negative retry delay in seconds, or None.

    A missing, malformed or meaningless value must never fail a crawl, so
    strings, containers, booleans (an `int` subclass) and non-finite numbers
    are all rejected. A negative value is rejected as well. `0` is returned as
    `0.0`; callers treat it as "no per-request window" and fall back to the
    per-domain delay.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        delay = float(value)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None
    if not math.isfinite(delay) or delay < 0:
        return None
    return delay


class DownloadDelayMiddleware(DownloaderMiddleware):
    """Per-domain minimum inter-request delay middleware.

    Arguments:
        delay_per_domain: minimum seconds between requests to same domain
        randomize: when true, jitter the per-domain delay to 50-150% each request

    Notes:
      - Honors per-request `request.meta['retry_delay']` when present: the retry
        delay becomes a *domain-wide* wait window (not just a pause for that one
        request), so other requests to the same domain cannot slip through it.
      - The wait happens in `process_request`, i.e. before the request is handed
        to the downloader; the domain slot is stamped when the request is
        released, so concurrent same-domain requests are spaced apart instead of
        being released in a burst.
      - Records the last-download timestamp on response/exception as well.
    """

    def __init__(self, delay_per_domain: float = 0.25, randomize: bool = False) -> None:
        try:
            d = float(delay_per_domain)
        except Exception:
            raise TypeError("delay_per_domain must be a number") from None
        if d < 0:
            raise ValueError("delay_per_domain must be >= 0")
        self._delay = d
        self._randomize = bool(randomize)
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        # Absolute monotonic deadlines published by retry requests. Every
        # request to the domain waits until this instant, not only the retrying
        # request itself.
        self._blocked_until: dict[str, float] = {}

    @classmethod
    def from_crawler(cls, crawler):
        """Create from crawler, reading the `DELAY_PER_DOMAIN` and `RANDOMIZE_DELAY` settings."""
        from qcrawl.settings import Settings

        settings: Settings = getattr(crawler, "runtime_settings", None) or Settings()
        return cls(delay_per_domain=settings.DELAY_PER_DOMAIN, randomize=settings.RANDOMIZE_DELAY)

    def _domain_key(self, url: str) -> str:
        try:
            d = get_domain(url)
            return d or "default"
        except Exception:
            return "default"

    def _get_lock(self, domain_key: str) -> asyncio.Lock:
        """Get or create a lock for the given domain."""
        if domain_key not in self._locks:
            self._locks[domain_key] = asyncio.Lock()
        return self._locks[domain_key]

    def _base_delay(self) -> float:
        """Per-domain delay for this request, with optional jitter applied."""
        base_delay = self._delay
        if self._randomize and base_delay > 0:
            base_delay *= random.uniform(0.5, 1.5)
        return base_delay

    def _publish_retry_window(
        self, domain_key: str, retry_delay: float, *, start: float | None = None
    ) -> float:
        """Record a domain-wide "do not dispatch before" deadline.

        The deadline is `start + retry_delay`, where `start` defaults to the
        last recorded activity for the domain (the failed response/exception
        that produced the retry) and falls back to now. Extending an existing
        window is monotonic: a shorter retry delay never shortens it.

        Called synchronously so same-domain requests already queued behind the
        domain lock also observe the window.

        Returns:
            The deadline that applies to the domain after the update.
        """
        now = time.monotonic()
        if start is None:
            start = self._last.get(domain_key, now)
        deadline = max(start, now) + retry_delay
        if deadline > self._blocked_until.get(domain_key, 0.0):
            self._blocked_until[domain_key] = deadline
        return self._blocked_until.get(domain_key, deadline)

    async def on_retry_scheduled(self, request: "Request") -> None:
        """Engine hook: a RETRY result was just rescheduled.

        Publishing here (rather than only in `process_request`) closes the race
        where other same-domain requests reach the downloader before the retry
        request is dequeued. Retries without a usable `retry_delay` (redirects,
        or malformed values) are ignored.
        """
        meta = getattr(request, "meta", None)
        if not isinstance(meta, dict):
            return
        retry_delay = _coerce_retry_delay(meta.get("retry_delay"))
        if not retry_delay:
            return
        deadline = self._publish_retry_window(self._domain_key(request.url), retry_delay)
        # Remember what this attempt already waited for, so process_request
        # reuses the same window instead of opening a second, later one.
        meta[_WINDOW_UNTIL_META] = deadline
        meta[_WINDOW_ATTEMPT_META] = meta.get("retry_count")

    def _published_deadline(self, meta: dict) -> float | None:
        """Return the absolute deadline this attempt's engine hook published.

        Returns None when no marker exists, the marker refers to a different
        retry attempt, or the stored value is malformed — in which case the
        caller should open a fresh window.
        """
        if _WINDOW_ATTEMPT_META not in meta or _WINDOW_UNTIL_META not in meta:
            return None
        if meta[_WINDOW_ATTEMPT_META] != meta.get("retry_count"):
            return None
        deadline = meta[_WINDOW_UNTIL_META]
        if not isinstance(deadline, (int, float)) or isinstance(deadline, bool):
            return None
        return float(deadline)

    def _clear_window_meta(self, meta: dict) -> None:
        meta.pop(_WINDOW_UNTIL_META, None)
        meta.pop(_WINDOW_ATTEMPT_META, None)

    async def process_request(self, request: "Request", spider: "Spider") -> MiddlewareResult:
        meta = getattr(request, "meta", None)
        if not isinstance(meta, dict):
            meta = {}
            request.meta = meta

        domain_key = self._domain_key(request.url)
        retry_delay = _coerce_retry_delay(meta.get("retry_delay"))

        # A retry delay is a domain-wide window. Publish it before awaiting
        # anything, so same-domain requests already queued also respect it.
        #
        # If the engine hook already opened this attempt's window, reuse that
        # absolute deadline instead of stacking a fresh now+delay on top —
        # otherwise a retry that waited in the queue would wait twice. Reusing
        # the absolute deadline also repairs the domain map if another request
        # dropped the (already elapsed) entry in the meantime.
        if retry_delay:
            published = self._published_deadline(meta)
            now = time.monotonic()
            if published is None:
                self._publish_retry_window(domain_key, retry_delay, start=now)
            elif published > now:
                current = self._blocked_until.get(domain_key, 0.0)
                if published > current:
                    self._blocked_until[domain_key] = published
            # else: the published window already elapsed — nothing left to wait for.
        self._clear_window_meta(meta)

        base_delay = self._base_delay()
        lock = self._get_lock(domain_key)

        # Serialize the decision per domain so concurrent requests cannot all
        # pass the gate using the same previous timestamp.
        async with lock:
            while True:
                now = time.monotonic()
                ready_at = now

                last = self._last.get(domain_key)
                if last is not None:
                    ready_at = max(ready_at, last + base_delay)

                blocked_until = self._blocked_until.get(domain_key)
                if blocked_until is not None:
                    if blocked_until > ready_at:
                        ready_at = blocked_until
                    if blocked_until <= now:
                        # Window elapsed; drop the stale entry.
                        del self._blocked_until[domain_key]

                if ready_at <= now:
                    break
                await asyncio.sleep(ready_at - now)
                # Re-loop: another request may have published a retry window
                # while this one was sleeping.

            # Stamp the slot when the request is actually released, not only
            # when its response arrives: a request that is still in flight must
            # still hold the domain's spacing slot.
            self._last[domain_key] = time.monotonic()

        # mark which domain we used so response/exception can update timestamp
        meta[_DOMAIN_KEY_META] = domain_key
        return MiddlewareResult.continue_()

    async def process_response(
        self, request: "Request", response, spider: "Spider"
    ) -> MiddlewareResult:
        self._record_completion(request, "response")
        return MiddlewareResult.keep(response)

    async def process_exception(
        self, request: "Request", exception: BaseException, spider: "Spider"
    ) -> MiddlewareResult:
        self._record_completion(request, "exception")
        return MiddlewareResult.continue_()

    def _record_completion(self, request: "Request", phase: str) -> None:
        """Update the last-download timestamp for the request's domain."""
        meta = getattr(request, "meta", None)
        slot_key = None
        if isinstance(meta, dict):
            slot_key = meta.pop(_DOMAIN_KEY_META, None)

        if isinstance(slot_key, str):
            try:
                self._last[slot_key] = time.monotonic()
            except Exception:
                logger.exception("Failed to update last-download time for %s (%s)", slot_key, phase)

    async def open_spider(self, spider: "Spider") -> None:
        self._last.clear()
        self._locks.clear()
        self._blocked_until.clear()
        logger.info("delay_per_domain: %.3f seconds", self._delay)
