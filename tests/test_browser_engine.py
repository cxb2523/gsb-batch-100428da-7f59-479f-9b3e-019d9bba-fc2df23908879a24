"""Tests for the opt-in Patchright browser-engine selection.

The engine is an operator-only switch (``ANANSI_BROWSER_ENGINE``), validated at
import like ``ANANSI_IMPERSONATE``. Patchright is an optional extra; when the
operator opts in but it is not installed the launch must fail loud with an
actionable message — never silently fall back to vanilla Playwright, which
would misrepresent the stealth level actually in use.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from anansi import security
from anansi.fetchers import browser as browser_mod
from anansi.fetchers.browser import BrowserFetcher


def test_validate_browser_engine_accepts_allowed() -> None:
    assert security.validate_browser_engine("playwright") == "playwright"
    assert security.validate_browser_engine("patchright") == "patchright"


def test_validate_browser_engine_rejects_unknown() -> None:
    with pytest.raises(security.InvalidBrowserEngineError):
        security.validate_browser_engine("scrapling")


def test_default_engine_is_playwright() -> None:
    """Env unset → vanilla Playwright. Zero default-behaviour change."""
    assert security.BROWSER_ENGINE == "playwright"


def test_load_async_playwright_default_is_playwright() -> None:
    from playwright.async_api import async_playwright

    assert browser_mod._load_async_playwright("playwright") is async_playwright


def test_load_async_playwright_patchright(monkeypatch: pytest.MonkeyPatch) -> None:
    """engine=patchright resolves patchright's entrypoint when installed."""
    sentinel = object()
    fake_api = types.ModuleType("patchright.async_api")
    fake_api.async_playwright = sentinel
    fake_pkg = types.ModuleType("patchright")
    fake_pkg.async_api = fake_api
    monkeypatch.setitem(sys.modules, "patchright", fake_pkg)
    monkeypatch.setitem(sys.modules, "patchright.async_api", fake_api)

    assert browser_mod._load_async_playwright("patchright") is sentinel


def test_load_async_playwright_patchright_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt-in without the extra installed fails loud, not silently downgraded."""
    monkeypatch.setitem(sys.modules, "patchright", None)  # forces ImportError

    with pytest.raises(RuntimeError, match=r"anansi-scraper\[patchright\]"):
        browser_mod._load_async_playwright("patchright")


async def test_ensure_browser_launches_via_resolved_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_ensure_browser must go through the engine resolver, not a direct import."""
    launched = MagicMock()
    launched.chromium.launch = AsyncMock(return_value=MagicMock())
    entry = MagicMock()
    entry.return_value.start = AsyncMock(return_value=launched)
    resolver = MagicMock(return_value=entry)
    monkeypatch.setattr(browser_mod, "_load_async_playwright", resolver)

    fetcher = BrowserFetcher()
    await fetcher._ensure_browser()

    resolver.assert_called_once_with()
    launched.chromium.launch.assert_awaited_once()
