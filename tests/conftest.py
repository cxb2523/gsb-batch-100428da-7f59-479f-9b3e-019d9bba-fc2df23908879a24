"""Shared test fixtures.

These lines MUST run before any `proxyrotate` import: the package reads
HOME (default config/proxy paths) and PROXYROTATE_CACHE_DIR (log file)
at import time. Redirecting them here keeps the suite fully hermetic —
tests never touch the real user configuration, proxy list, or cache.
"""

import os
import tempfile

_TEST_HOME = tempfile.mkdtemp(prefix="proxyrotate-test-home-")
_TEST_CACHE = tempfile.mkdtemp(prefix="proxyrotate-test-cache-")
os.environ["HOME"] = _TEST_HOME
os.environ["PROXYROTATE_CACHE_DIR"] = _TEST_CACHE

import logging

import pytest

# Keep test-time log records quiet (no lastResort stderr output) even when
# setup_logging() was never called.
logging.getLogger("proxyrotate").addHandler(logging.NullHandler())


@pytest.fixture
def make_proxy():
    from proxyrotate.models import Proxy

    def factory(url: str) -> Proxy:
        return Proxy.parse(url)

    return factory


@pytest.fixture
def proxy_file(tmp_path):
    """Factory writing a proxy list file; returns its path."""

    def factory(content: str):
        path = tmp_path / "proxies.txt"
        path.write_text(content, encoding="utf-8")
        return path

    return factory


# Fixture re-exports from the CLI test module: tests resolve them by
# parameter name, and importing them here makes them available suite-wide.
# (test_cli.py lives in the same directory pytest puts on sys.path.)
from test_cli import fake_singbox as fake_singbox  # noqa: PLC0414
from test_cli import make_fake_singbox  # noqa: F401  (shared with test_cli)
