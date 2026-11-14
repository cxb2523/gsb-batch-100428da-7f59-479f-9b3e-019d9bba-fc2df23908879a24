"""Offline tests for the retry chain in both the sync and async sessions.

Nothing here touches the network: the underlying curl_cffi ``Session.request`` /
``AsyncSession.request`` calls are replaced with scripted fake responses, and
the clocks/sleep functions are faked so total-time limits are deterministic.
"""

import time
from email.utils import formatdate
from types import SimpleNamespace

import pytest
from curl_cffi.requests import Response
from curl_cffi.requests.models import Headers
from curl_cffi.requests.session import AsyncSession as CurlAsyncSession
from curl_cffi.requests.session import Session as CurlSession

from stealth_requests import AsyncStealthSession, StealthSession
from stealth_requests.session import RETRY_DELAY, parse_retry_after

URL = 'https://example.test/path?keep=query'


# --- Fakes ---------------------------------------------------------------


class FakeClock:
    """Deterministic stand-in for time.perf_counter advanced by the fake sleeps."""

    def __init__(self):
        self.now = 1000.0

    def perf_counter(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    async def async_sleep(self, seconds):
        self.now += seconds


def make_response(status_code, retry_after=None):
    """Build a real curl_cffi Response without performing any I/O."""
    resp = Response()
    resp.status_code = status_code
    headers = {}
    if retry_after is not None:
        headers['Retry-After'] = retry_after
    resp.headers = Headers(headers)
    return resp


def install_transport(monkeypatch, session, responses, clock):
    """Replace the curl_cffi transport with a scripted sequence of responses."""
    calls = []

    def fake_request(self, method, url, *args, **kwargs):
        calls.append(SimpleNamespace(method=method, url=url, kwargs=kwargs))
        if len(calls) > len(responses):
            raise AssertionError('transport called more times than scripted responses')
        return responses[len(calls) - 1]

    async def fake_async_request(self, method, url, *args, **kwargs):
        calls.append(SimpleNamespace(method=method, url=url, kwargs=kwargs))
        if len(calls) > len(responses):
            raise AssertionError('transport called more times than scripted responses')
        return responses[len(calls) - 1]

    is_async = isinstance(session, AsyncStealthSession)
    monkeypatch.setattr(
        CurlAsyncSession if is_async else CurlSession,
        'request',
        fake_async_request if is_async else fake_request,
    )
    monkeypatch.setattr('stealth_requests.session.time.perf_counter', clock.perf_counter)
    if is_async:
        monkeypatch.setattr('stealth_requests.session.asyncio.sleep', clock.async_sleep)
    else:
        monkeypatch.setattr('stealth_requests.session.time.sleep', clock.sleep)
    return calls


# --- parse_retry_after unit tests ---------------------------------------


class TestParseRetryAfter:
    def test_delta_seconds(self):
        assert parse_retry_after('10') == 10.0

    def test_delta_seconds_with_whitespace(self):
        assert parse_retry_after('  3 ') == 3.0

    def test_http_date_in_the_future(self):
        value = formatdate(time.time() + 30, usegmt=True)
        delay = parse_retry_after(value)
        assert delay is not None
        assert 29.0 <= delay <= 30.0

    def test_http_date_in_the_past_clamps_to_zero(self):
        value = formatdate(time.time() - 60, usegmt=True)
        assert parse_retry_after(value) == 0.0

    def test_missing_header(self):
        assert parse_retry_after(None) is None
        assert parse_retry_after('') is None

    def test_unparseable_header_falls_back(self):
        assert parse_retry_after('not-a-date-or-int') is None

    def test_float_delta_is_not_valid_delta_seconds(self):
        # RFC 9110 delta-seconds are integers; a float is treated as unparseable.
        assert parse_retry_after('1.5') is None

    def test_naive_date_is_rejected(self):
        assert parse_retry_after('Wed, 21 Oct 2099 07:28:00') is None


# --- Sync session --------------------------------------------------------


class TestSyncRetries:
    def test_first_attempt_succeeds(self, monkeypatch):
        clock = FakeClock()
        with StealthSession() as session:
            calls = install_transport(monkeypatch, session, [make_response(200)], clock)

            resp = session.get(URL, retry=3)

        assert resp.status_code == 200
        assert len(calls) == 1
        assert resp.attempt_count == 1
        assert resp.retry_count == 0
        assert resp.retry_delays == ()

    def test_retries_until_success_and_records_default_delays(self, monkeypatch):
        clock = FakeClock()
        responses = [make_response(429), make_response(503), make_response(200)]
        with StealthSession() as session:
            calls = install_transport(monkeypatch, session, responses, clock)

            resp = session.get(URL, retry=3)

        assert resp.status_code == 200
        assert len(calls) == 3
        assert resp.attempt_count == 3
        assert resp.retry_count == 2
        assert resp.retry_delays == (RETRY_DELAY, RETRY_DELAY)

    def test_retries_exhausted_returns_last_response(self, monkeypatch):
        clock = FakeClock()
        responses = [make_response(503), make_response(503), make_response(429)]
        with StealthSession() as session:
            calls = install_transport(monkeypatch, session, responses, clock)

            resp = session.get(URL, retry=2)

        assert resp.status_code == 429
        assert len(calls) == 3
        assert resp.attempt_count == 3
        assert resp.retry_delays == (RETRY_DELAY, RETRY_DELAY)

    def test_non_retryable_status_is_not_retried(self, monkeypatch):
        clock = FakeClock()
        with StealthSession() as session:
            calls = install_transport(monkeypatch, session, [make_response(404)], clock)

            resp = session.get(URL, retry=3)

        assert resp.status_code == 404
        assert len(calls) == 1
        assert resp.attempt_count == 1
        assert resp.retry_delays == ()

    def test_retry_after_delta_seconds_is_used(self, monkeypatch):
        clock = FakeClock()
        responses = [make_response(429, retry_after='7'), make_response(200)]
        with StealthSession() as session:
            install_transport(monkeypatch, session, responses, clock)

            resp = session.get(URL, retry=2)

        assert resp.attempt_count == 2
        assert resp.retry_delays == (7.0,)

    def test_retry_after_http_date_is_used(self, monkeypatch):
        clock = FakeClock()
        # HTTP-dates are parsed against wall-clock time, not the fake perf_counter.
        http_date = formatdate(time.time() + 12, usegmt=True)
        responses = [make_response(503, retry_after=http_date), make_response(200)]
        with StealthSession() as session:
            install_transport(monkeypatch, session, responses, clock)

            resp = session.get(URL, retry=1)

        assert resp.attempt_count == 2
        # HTTP-dates have 1-second resolution and parsing uses real wall time, so allow slack.
        assert resp.retry_delays == (pytest.approx(12.0, abs=1.5),)

    def test_missing_retry_after_uses_default_delay(self, monkeypatch):
        clock = FakeClock()
        responses = [make_response(503), make_response(200)]
        with StealthSession() as session:
            install_transport(monkeypatch, session, responses, clock)

            resp = session.get(URL, retry=1, retry_delay=4)

        assert resp.retry_delays == (4.0,)

    def test_unparseable_retry_after_uses_default_delay(self, monkeypatch):
        clock = FakeClock()
        responses = [make_response(503, retry_after='garbage'), make_response(200)]
        with StealthSession() as session:
            install_transport(monkeypatch, session, responses, clock)

            resp = session.get(URL, retry=1, retry_delay=2.5)

        assert resp.retry_delays == (2.5,)

    def test_max_retry_delay_caps_the_wait(self, monkeypatch):
        clock = FakeClock()
        responses = [make_response(429, retry_after='30'), make_response(200)]
        with StealthSession() as session:
            install_transport(monkeypatch, session, responses, clock)

            resp = session.get(URL, retry=1, max_retry_delay=5)

        assert resp.retry_delays == (5.0,)

    def test_max_retry_delay_also_caps_default_delay(self, monkeypatch):
        clock = FakeClock()
        responses = [make_response(429), make_response(200)]
        with StealthSession() as session:
            install_transport(monkeypatch, session, responses, clock)

            resp = session.get(URL, retry=1, retry_delay=10, max_retry_delay=3)

        assert resp.retry_delays == (3.0,)

    def test_max_retry_time_stops_before_the_wait(self, monkeypatch):
        # Clock starts at 1000; the default 2s wait would end at 1002 > 1001.
        clock = FakeClock()
        responses = [make_response(503), make_response(503), make_response(200)]
        with StealthSession() as session:
            calls = install_transport(monkeypatch, session, responses, clock)

            resp = session.get(URL, retry=2, retry_delay=2, max_retry_time=1)

        assert resp.status_code == 503
        assert len(calls) == 1
        assert resp.attempt_count == 1
        assert resp.retry_delays == ()

    def test_max_retry_time_allows_waits_that_stay_within_budget(self, monkeypatch):
        # Two 1s waits: 1000 -> 1001 -> 1002, budget of 2 is never exceeded.
        clock = FakeClock()
        responses = [make_response(503), make_response(503), make_response(200)]
        with StealthSession() as session:
            calls = install_transport(monkeypatch, session, responses, clock)

            resp = session.get(URL, retry=2, retry_delay=1, max_retry_time=2)

        assert resp.status_code == 200
        assert len(calls) == 3
        assert resp.retry_delays == (1.0, 1.0)

    def test_retry_after_past_total_budget_ends_early(self, monkeypatch):
        clock = FakeClock()
        responses = [make_response(429, retry_after='10'), make_response(200)]
        with StealthSession() as session:
            calls = install_transport(monkeypatch, session, responses, clock)

            resp = session.get(URL, retry=1, max_retry_time=5)

        assert resp.status_code == 429
        assert len(calls) == 1
        assert resp.retry_delays == ()

    def test_session_level_retry_configuration(self, monkeypatch):
        clock = FakeClock()
        responses = [make_response(429, retry_after='99'), make_response(200)]
        with StealthSession(max_retry_delay=4, max_retry_time=100) as session:
            install_transport(monkeypatch, session, responses, clock)

            resp = session.get(URL, retry=1)

        assert resp.retry_delays == (4.0,)

    def test_per_request_config_overrides_session_config(self, monkeypatch):
        clock = FakeClock()
        responses = [make_response(429, retry_after='99'), make_response(200)]
        with StealthSession(max_retry_delay=4) as session:
            install_transport(monkeypatch, session, responses, clock)

            resp = session.get(URL, retry=1, max_retry_delay=1.5)

        assert resp.retry_delays == (1.5,)

    def test_referer_is_updated_after_retries(self, monkeypatch):
        clock = FakeClock()
        responses = [
            make_response(503),
            make_response(200),
            make_response(200),
        ]
        with StealthSession() as session:
            calls = install_transport(monkeypatch, session, responses, clock)

            session.get(URL, retry=2)
            session.get('https://example.test/other')

        # Query string and fragment are stripped when remembering the Referer.
        second_request_headers = calls[-1].kwargs['headers']
        assert second_request_headers['Referer'] == 'https://example.test/path'
        # Retried request reused the same (empty at first) Referer on every attempt.
        assert all(call.kwargs['headers'] == {} for call in calls[:2])


# --- Async session -------------------------------------------------------


@pytest.mark.asyncio
class TestAsyncRetries:
    async def test_first_attempt_succeeds(self, monkeypatch):
        clock = FakeClock()
        async with AsyncStealthSession() as session:
            calls = install_transport(monkeypatch, session, [make_response(200)], clock)

            resp = await session.get(URL, retry=3)

        assert len(calls) == 1
        assert resp.attempt_count == 1
        assert resp.retry_delays == ()

    async def test_retries_until_success(self, monkeypatch):
        clock = FakeClock()
        responses = [make_response(429), make_response(503), make_response(200)]
        async with AsyncStealthSession() as session:
            calls = install_transport(monkeypatch, session, responses, clock)

            resp = await session.get(URL, retry=3)

        assert len(calls) == 3
        assert resp.attempt_count == 3
        assert resp.retry_delays == (RETRY_DELAY, RETRY_DELAY)

    async def test_retry_after_delta_seconds(self, monkeypatch):
        clock = FakeClock()
        responses = [make_response(429, retry_after='7'), make_response(200)]
        async with AsyncStealthSession() as session:
            install_transport(monkeypatch, session, responses, clock)

            resp = await session.get(URL, retry=1)

        assert resp.retry_delays == (7.0,)

    async def test_retry_after_http_date(self, monkeypatch):
        clock = FakeClock()
        # HTTP-dates are parsed against wall-clock time, not the fake perf_counter.
        http_date = formatdate(time.time() + 9, usegmt=True)
        responses = [make_response(503, retry_after=http_date), make_response(200)]
        async with AsyncStealthSession() as session:
            install_transport(monkeypatch, session, responses, clock)

            resp = await session.get(URL, retry=1)

        # HTTP-dates have 1-second resolution and parsing uses real wall time, so allow slack.
        assert resp.retry_delays == (pytest.approx(9.0, abs=1.5),)

    async def test_max_retry_delay_caps_the_wait(self, monkeypatch):
        clock = FakeClock()
        responses = [make_response(429, retry_after='30'), make_response(200)]
        async with AsyncStealthSession() as session:
            install_transport(monkeypatch, session, responses, clock)

            resp = await session.get(URL, retry=1, max_retry_delay=5)

        assert resp.retry_delays == (5.0,)

    async def test_max_retry_time_ends_early(self, monkeypatch):
        clock = FakeClock()
        responses = [make_response(503), make_response(200)]
        async with AsyncStealthSession() as session:
            calls = install_transport(monkeypatch, session, responses, clock)

            resp = await session.get(URL, retry=2, retry_delay=2, max_retry_time=1)

        assert resp.status_code == 503
        assert len(calls) == 1
        assert resp.attempt_count == 1
        assert resp.retry_delays == ()

    async def test_referer_is_updated_after_retries(self, monkeypatch):
        clock = FakeClock()
        responses = [make_response(503), make_response(200), make_response(200)]
        async with AsyncStealthSession() as session:
            calls = install_transport(monkeypatch, session, responses, clock)

            await session.get(URL, retry=1)
            await session.get('https://example.test/other')

        assert calls[-1].kwargs['headers']['Referer'] == 'https://example.test/path'


# --- Sync / async parity -------------------------------------------------


PARITY_CASES = [
    pytest.param(
        [make_response(429), make_response(503), make_response(200)],
        dict(retry=3),
        id='default-delays',
    ),
    pytest.param(
        [make_response(429, retry_after='7'), make_response(429, retry_after='3'), make_response(200)],
        dict(retry=3),
        id='retry-after-delta',
    ),
    pytest.param(
        [make_response(429, retry_after='99'), make_response(200)],
        dict(retry=2, max_retry_delay=4),
        id='capped-delay',
    ),
    pytest.param(
        [make_response(503), make_response(503), make_response(200)],
        dict(retry=3, retry_delay=2, max_retry_time=3),
        id='total-budget-stops-early',
    ),
    pytest.param(
        [make_response(429, retry_after='10'), make_response(200)],
        dict(retry=1, max_retry_time=5),
        id='retry-after-over-budget',
    ),
    pytest.param(
        [make_response(200)],
        dict(retry=3),
        id='first-attempt-ok',
    ),
    pytest.param(
        [make_response(503), make_response(503)],
        dict(retry=1),
        id='retries-exhausted',
    ),
]


def run_sync(responses, options):
    clock = FakeClock()
    patch = pytest.MonkeyPatch()
    try:
        with StealthSession() as session:
            calls = install_transport(patch, session, responses, clock)
            resp = session.get(URL, **options)
        return resp, len(calls), clock.now - 1000.0
    finally:
        patch.undo()


async def run_async(responses, options):
    clock = FakeClock()
    patch = pytest.MonkeyPatch()
    try:
        async with AsyncStealthSession() as session:
            calls = install_transport(patch, session, responses, clock)
            resp = await session.get(URL, **options)
        return resp, len(calls), clock.now - 1000.0
    finally:
        patch.undo()


@pytest.mark.parametrize('responses, options', PARITY_CASES)
def test_sync_and_async_paths_match(responses, options):
    sync_resp, sync_calls, sync_waited = run_sync(responses, options)

    async def runner():
        # Re-script fresh responses for the async run since fakes hold no mutable state anyway.
        return await run_async(
            [make_response(r.status_code, r.headers.get('Retry-After')) for r in responses],
            options,
        )

    async_resp, async_calls, async_waited = pytest_asyncio_run(runner())

    assert sync_resp.status_code == async_resp.status_code
    assert sync_calls == async_calls
    assert sync_resp.attempt_count == async_resp.attempt_count
    assert sync_resp.retry_delays == async_resp.retry_delays
    assert sync_waited == async_waited


def pytest_asyncio_run(coro):
    """Run a coroutine to completion without depending on pytest-asyncio markers here."""
    import asyncio

    return asyncio.new_event_loop().run_until_complete(coro)
