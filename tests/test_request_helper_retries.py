"""
Tests for the retry behaviour of RequestHelper against a local fake Moodle endpoint.

The tests never touch the network: a small aiohttp server running on 127.0.0.1
pretends to be the Moodle REST endpoint.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web

from moodle_dl.moodle.request_helper import (
    InvalidTokenRequestError,
    RequestHelper,
    RequestRejectedError,
    RequestRetriesExhaustedError,
)
from moodle_dl.types import MoodleURL

REST_PATH = '/webservice/rest/server.php'


def run(coroutine):
    return asyncio.run(coroutine)


def make_opts() -> SimpleNamespace:
    return SimpleNamespace(
        max_parallel_api_calls=2,
        log_responses=False,
        skip_cert_verify=False,
        allow_insecure_ssl=False,
        use_all_ciphers=False,
    )


class FakeMoodleServer:
    """A local fake Moodle REST endpoint that returns canned responses in order."""

    def __init__(self, responses):
        # responses: list of (status, payload). The last entry is repeated.
        self.responses = list(responses)
        self.request_count = 0
        self.runner = None
        self.port = None

    async def _handler(self, request):
        index = min(self.request_count, len(self.responses) - 1)
        self.request_count += 1
        status, payload = self.responses[index]
        return web.json_response(payload, status=status)

    async def __aenter__(self):
        app = web.Application()
        app.router.add_post(REST_PATH, self._handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.runner.cleanup()

    def request_helper(self) -> RequestHelper:
        return RequestHelper(
            config=SimpleNamespace(),
            opts=make_opts(),
            moodle_url=MoodleURL(True, f'127.0.0.1:{self.port}', '/'),
            token='dummy-token',
        )


def test_retryable_status_is_retried_until_success():
    async def scenario():
        async with FakeMoodleServer(
            [
                (429, {'error': 'too many requests'}),
                (408, {'error': 'timeout'}),
                (409, {'error': 'conflict'}),
                (200, {'ok': True}),
            ]
        ) as server:
            with patch('moodle_dl.moodle.request_helper.asyncio.sleep', new=AsyncMock()):
                result = await server.request_helper().async_post('core_webservice_get_site_info')
            assert result == {'ok': True}
            assert server.request_count == 4

    run(scenario())


def test_exhausted_retries_raise_dedicated_error():
    async def scenario():
        async with FakeMoodleServer([(429, {'error': 'too many requests'})]) as server:
            with patch('moodle_dl.moodle.request_helper.asyncio.sleep', new=AsyncMock()):
                with pytest.raises(RequestRetriesExhaustedError) as error_info:
                    await server.request_helper().async_post('core_webservice_get_site_info')
            assert isinstance(error_info.value, ConnectionError)
            assert server.request_count == RequestHelper.MAX_RETRIES

    run(scenario())


def test_retryable_moodle_error_is_retried_and_then_exhausted():
    async def scenario():
        async with FakeMoodleServer([(200, {'error': 'Unable to lock', 'errorcode': 'ex_unabletolock'})]) as server:
            with patch('moodle_dl.moodle.request_helper.asyncio.sleep', new=AsyncMock()):
                with pytest.raises(RequestRetriesExhaustedError):
                    await server.request_helper().async_post('mod_forum_get_forums_by_courses')
            assert server.request_count == RequestHelper.MAX_RETRIES

    run(scenario())


def test_non_retryable_status_is_rejected_immediately():
    async def scenario():
        async with FakeMoodleServer([(500, {'error': 'server error'})]) as server:
            with pytest.raises(ConnectionError) as error_info:
                await server.request_helper().async_post('core_webservice_get_site_info')
            assert not isinstance(error_info.value, RequestRetriesExhaustedError)
            assert server.request_count == 1

    run(scenario())


def test_invalid_token_is_a_dedicated_error():
    async def scenario():
        async with FakeMoodleServer(
            [
                (
                    200,
                    {
                        'exception': 'moodle_exception',
                        'errorcode': 'invalidtoken',
                        'message': 'Invalid token',
                    },
                )
            ]
        ) as server:
            with pytest.raises(InvalidTokenRequestError) as error_info:
                await server.request_helper().async_post('core_webservice_get_site_info')
            assert 'token has expired' in str(error_info.value)
            assert isinstance(error_info.value, RequestRejectedError)
            # A token error is not retried.
            assert server.request_count == 1

    run(scenario())


def test_non_retryable_moodle_rejection_is_rejected():
    async def scenario():
        async with FakeMoodleServer([(200, {'error': 'No permission', 'errorcode': 'nopermission'})]) as server:
            with pytest.raises(RequestRejectedError) as error_info:
                await server.request_helper().async_post('mod_forum_get_forums_by_courses')
            assert not isinstance(error_info.value, RequestRetriesExhaustedError)
            assert not isinstance(error_info.value, InvalidTokenRequestError)
            assert server.request_count == 1

    run(scenario())
