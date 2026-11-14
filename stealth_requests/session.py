import asyncio
import re
import time
from email.utils import parsedate_to_datetime
from functools import partialmethod
from urllib.parse import urlparse, urlunparse

from curl_cffi.requests.session import AsyncSession, HttpMethod, Session

from .response import StealthResponse

RETRY_DELAY = 2  # Seconds
RETRYABLE_STATUS_CODES = {
    408,  # Request Timeout
    429,  # Too Many Requests
    500,  # Internal Server Error
    502,  # Bad Gateway
    503,  # Service Unavailable
    504,  # Gateway Timeout
    520,  # Cloudflare Unknown Error
    521,  # Cloudflare Web Server Is Down
    522,  # Cloudflare Connection Timed Out
    523,  # Cloudflare Origin Is Unreachable
    524,  # Cloudflare A Timeout Occurred
}


# curl_cffi resolves 'chrome' to the newest Chrome it ships.
IMPERSONATE = 'chrome'


def impersonated_chrome_version() -> int | None:
    # REAL_TARGET_MAP is private, so fall back to None if it ever moves.
    try:
        from curl_cffi.requests.impersonate import REAL_TARGET_MAP

        match = re.search(r'\d+', REAL_TARGET_MAP[IMPERSONATE])
        return int(match.group()) if match else None
    except Exception:
        return None


CHROME_VERSION = impersonated_chrome_version()

# Chrome reports this token on every Mac, whatever the hardware or OS version.
PLATFORM = 'Macintosh; Intel Mac OS X 10_15_7'
PLATFORM_HINT = '"macOS"'


def identity() -> dict[str, str]:
    # Without a known version, leave curl_cffi's own consistent headers alone.
    if CHROME_VERSION is None:
        return {}

    user_agent = (
        f'Mozilla/5.0 ({PLATFORM}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{CHROME_VERSION}.0.0.0 Safari/537.36'
    )
    return {'User-Agent': user_agent, 'Sec-CH-UA-Platform': PLATFORM_HINT}


def parse_retry_after(value: str | None, now: float | None = None) -> float | None:
    """Parse a Retry-After header into a number of seconds to wait.

    The header is either an integer number of delta-seconds or an HTTP-date.
    Returns None when the header is missing or cannot be parsed, so callers
    can fall back to their default interval.
    """
    if not value:
        return None

    value = value.strip()

    # Delta-seconds form: a non-negative integer, per RFC 9110.
    if value.isdigit():
        return float(int(value))

    # HTTP-date form, e.g. 'Wed, 21 Oct 2099 07:28:00 GMT'.
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None

    if retry_at is None:
        return None

    if retry_at.tzinfo is None:
        # A date without timezone info is not a valid HTTP-date; treat it as unparseable
        # rather than guessing the local timezone.
        return None

    if now is None:
        now = time.time()
    return max(0.0, retry_at.timestamp() - now)


class RetryController:
    """Shared retry bookkeeping so the sync and async sessions behave identically."""

    def __init__(
        self,
        retry: int = 0,
        retry_delay: float = RETRY_DELAY,
        max_retry_delay: float | None = None,
        max_retry_time: float | None = None,
        clock=time.perf_counter,
    ):
        assert retry >= 0
        assert retry_delay >= 0
        assert max_retry_delay is None or max_retry_delay >= 0
        assert max_retry_time is None or max_retry_time >= 0

        self.retry = retry
        self.retry_delay = retry_delay
        self.max_retry_delay = max_retry_delay
        self.max_retry_time = max_retry_time
        self._clock = clock

        self._start = clock()
        self.attempt_count = 0
        self.retry_delays: list[float] = []

    def register_attempt(self) -> None:
        self.attempt_count += 1

    def next_delay(self, resp) -> float | None:
        """Return the seconds to wait before the next attempt, or None to stop retrying."""
        # No retries left: the last attempt was the final one.
        if self.attempt_count > self.retry:
            return None

        delay = parse_retry_after(resp.headers.get('Retry-After'))
        if delay is None:
            # Missing or unparseable Retry-After: fall back to the default interval.
            delay = self.retry_delay

        if self.max_retry_delay is not None:
            delay = min(delay, self.max_retry_delay)

        if self.max_retry_time is not None:
            elapsed = self._clock() - self._start
            # Don't start a wait that would push total retry time past the caller's budget.
            if elapsed + delay > self.max_retry_time:
                return None

        self.retry_delays.append(delay)
        return delay


class BaseStealthSession:
    def __init__(self, *args, **kwargs):
        timeout = kwargs.pop('timeout', 30)

        # Retry configuration shared by every request unless overridden per call.
        self.retry_delay = kwargs.pop('retry_delay', RETRY_DELAY)
        self.max_retry_delay = kwargs.pop('max_retry_delay', None)
        self.max_retry_time = kwargs.pop('max_retry_time', None)

        headers = kwargs.pop('headers', {})
        for header, value in identity().items():
            headers.setdefault(header, value)

        self.last_request_url = None

        super().__init__(impersonate=IMPERSONATE, timeout=timeout, headers=headers, *args, **kwargs)


class StealthSession(BaseStealthSession, Session):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def request(
        self,
        method: HttpMethod,
        url: str,
        *args,
        retry: int = 0,
        retry_delay: float | None = None,
        max_retry_delay: float | None = None,
        max_retry_time: float | None = None,
        **kwargs,
    ) -> StealthResponse:
        assert retry >= 0

        referer = {'Referer': self.last_request_url} if self.last_request_url else {}
        extra_headers = referer | kwargs.pop('headers', {})

        controller = RetryController(
            retry=retry,
            retry_delay=self.retry_delay if retry_delay is None else retry_delay,
            max_retry_delay=self.max_retry_delay if max_retry_delay is None else max_retry_delay,
            max_retry_time=self.max_retry_time if max_retry_time is None else max_retry_time,
            clock=time.perf_counter,
        )

        start = time.perf_counter()
        for attempt in range(retry + 1):
            controller.register_attempt()
            resp = super().request(method, url, *args, headers=extra_headers, **kwargs)

            if resp.status_code not in RETRYABLE_STATUS_CODES:
                break

            delay = controller.next_delay(resp)
            if delay is None:
                break

            # Retry after delay
            time.sleep(delay)
        elapsed = time.perf_counter() - start

        parsed = urlparse(url)
        self.last_request_url = urlunparse(parsed._replace(query='', fragment=''))

        return StealthResponse(resp, elapsed, controller.attempt_count, tuple(controller.retry_delays))

    head = partialmethod(request, 'HEAD')
    get = partialmethod(request, 'GET')
    post = partialmethod(request, 'POST')
    put = partialmethod(request, 'PUT')
    patch = partialmethod(request, 'PATCH')
    delete = partialmethod(request, 'DELETE')
    options = partialmethod(request, 'OPTIONS')


class AsyncStealthSession(BaseStealthSession, AsyncSession):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def request(
        self,
        method: HttpMethod,
        url: str,
        *args,
        retry: int = 0,
        retry_delay: float | None = None,
        max_retry_delay: float | None = None,
        max_retry_time: float | None = None,
        **kwargs,
    ) -> StealthResponse:
        assert retry >= 0

        referer = {'Referer': self.last_request_url} if self.last_request_url else {}
        extra_headers = referer | kwargs.pop('headers', {})

        controller = RetryController(
            retry=retry,
            retry_delay=self.retry_delay if retry_delay is None else retry_delay,
            max_retry_delay=self.max_retry_delay if max_retry_delay is None else max_retry_delay,
            max_retry_time=self.max_retry_time if max_retry_time is None else max_retry_time,
            clock=time.perf_counter,
        )

        start = time.perf_counter()
        for attempt in range(retry + 1):
            controller.register_attempt()
            resp = await super().request(method, url, *args, headers=extra_headers, **kwargs)

            if resp.status_code not in RETRYABLE_STATUS_CODES:
                break

            delay = controller.next_delay(resp)
            if delay is None:
                break

            # Retry after delay
            await asyncio.sleep(delay)
        elapsed = time.perf_counter() - start

        parsed = urlparse(url)
        self.last_request_url = urlunparse(parsed._replace(query='', fragment=''))

        return StealthResponse(resp, elapsed, controller.attempt_count, tuple(controller.retry_delays))

    head = partialmethod(request, 'HEAD')
    get = partialmethod(request, 'GET')
    post = partialmethod(request, 'POST')
    put = partialmethod(request, 'PUT')
    patch = partialmethod(request, 'PATCH')
    delete = partialmethod(request, 'DELETE')
    options = partialmethod(request, 'OPTIONS')
