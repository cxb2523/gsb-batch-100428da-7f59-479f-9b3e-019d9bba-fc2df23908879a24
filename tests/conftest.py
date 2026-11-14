"""Pytest fixtures shared by the offline collector tests."""

import sys
from pathlib import Path

import httpx
import pytest

# Make the repo root importable when pytest is invoked from anywhere.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def _block_real_http():
    """Fail loudly if code under test attempts real HTTP traffic.

    Collector tests inject their own ``httpx.AsyncClient`` fakes; this guard
    blocks the real transports so the offline guarantee is enforceable rather
    than conventional. A test that replaces ``httpx.AsyncClient`` entirely is
    unaffected because its fake never reaches a transport.
    """

    def _deny_async(self, request, *args, **kwargs):
        raise AssertionError(
            f"real HTTP request is not allowed in offline tests: {request.url}"
        )

    def _deny_sync(self, request, *args, **kwargs):
        raise AssertionError(
            f"real HTTP request is not allowed in offline tests: {request.url}"
        )

    original_async = httpx.AsyncHTTPTransport.handle_async_request
    original_sync = httpx.HTTPTransport.handle_request
    httpx.AsyncHTTPTransport.handle_async_request = _deny_async
    httpx.HTTPTransport.handle_request = _deny_sync
    try:
        yield
    finally:
        httpx.AsyncHTTPTransport.handle_async_request = original_async
        httpx.HTTPTransport.handle_request = original_sync
