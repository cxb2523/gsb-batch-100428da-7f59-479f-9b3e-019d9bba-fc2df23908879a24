# SPDX-License-Identifier: MIT

import pytest

import url_to_llm_text.get_html_text as html_module
from url_to_llm_text import ScraperConfig
from url_to_llm_text.diagnostics import FailureCategory, ScrapeDiagnostics, Stage

from fakes import (
    FakePage,
    FakeProxyError,
    FakeTimeoutError,
    build_fake_async_playwright,
)

URL = "https://example.com/products"
SAMPLE_HTML = "<html><body><h1>Hello</h1></body></html>"


@pytest.fixture
def fake_browser(monkeypatch):
    """Ensure playwright looks installed; individual tests configure the page."""

    page_holder = {}

    def _install(page):
        page_holder["page"] = page
        monkeypatch.setattr(
            html_module,
            "async_playwright",
            build_fake_async_playwright(page),
        )
        return page

    # The module-level import succeeded in this environment; make sure the
    # guard for a missing install starts from the "installed" state.
    monkeypatch.setattr(html_module, "async_playwright", build_fake_async_playwright(FakePage("")))
    return _install


async def test_success_returns_source_and_records_success(fake_browser):
    fake_browser(FakePage(SAMPLE_HTML))
    diag = ScrapeDiagnostics()

    result = await html_module.get_page_source(URL, config=ScraperConfig(), diagnostics=diag)

    assert result == SAMPLE_HTML
    assert diag.ok is True
    record = diag.last_record
    assert record.success is True
    assert record.stage == Stage.FETCH
    assert record.url == URL


async def test_success_without_diagnostics_unchanged(fake_browser):
    fake_browser(FakePage(SAMPLE_HTML))

    result = await html_module.get_page_source(URL, config=ScraperConfig())

    assert result == SAMPLE_HTML


async def test_empty_page_source_is_recorded_as_fetch_failure(fake_browser):
    fake_browser(FakePage(""))
    diag = ScrapeDiagnostics()

    result = await html_module.get_page_source(URL, config=ScraperConfig(), diagnostics=diag)

    assert result == ""
    assert diag.ok is False
    failure = diag.last_failure
    assert failure.stage == Stage.FETCH
    assert failure.category == FailureCategory.EMPTY_PAGE_SOURCE
    assert failure.url == URL


async def test_empty_page_source_without_diagnostics_returns_empty_string(fake_browser):
    fake_browser(FakePage(""))

    result = await html_module.get_page_source(URL, config=ScraperConfig())

    assert result == ""


async def test_playwright_not_installed(monkeypatch):
    monkeypatch.setattr(html_module, "async_playwright", None)
    diag = ScrapeDiagnostics()

    result = await html_module.get_page_source(URL, diagnostics=diag)

    assert result == ""
    failure = diag.last_failure
    assert failure.category == FailureCategory.PLAYWRIGHT_NOT_INSTALLED
    assert failure.stage == Stage.FETCH
    assert failure.exception_type == "ImportError"
    assert failure.url == URL


async def test_playwright_not_installed_without_diagnostics(monkeypatch):
    monkeypatch.setattr(html_module, "async_playwright", None)

    result = await html_module.get_page_source(URL)

    assert result == ""


async def test_navigation_timeout_is_classified(fake_browser, monkeypatch):
    timeout_error = FakeTimeoutError("Timeout 30000ms exceeded while waiting for navigation")
    fake_browser(FakePage(goto_error=timeout_error))
    diag = ScrapeDiagnostics()

    result = await html_module.get_page_source(URL, config=ScraperConfig(), diagnostics=diag)

    assert result == ""
    failure = diag.last_failure
    assert failure.category == FailureCategory.NAVIGATION_TIMEOUT
    assert failure.exception_type == "FakeTimeoutError"
    assert failure.url == URL
    assert "Timeout" in failure.message


async def test_real_playwright_timeout_class_is_recognized(fake_browser):
    if html_module.PlaywrightTimeoutError is None:
        pytest.skip("playwright is not importable")
    page = FakePage(goto_error=html_module.PlaywrightTimeoutError("real timeout"))
    fake_browser(page)
    diag = ScrapeDiagnostics()

    result = await html_module.get_page_source(URL, config=ScraperConfig(), diagnostics=diag)

    assert result == ""
    assert diag.last_failure.category == FailureCategory.NAVIGATION_TIMEOUT


async def test_proxy_launch_error_is_classified(fake_browser):
    fake_browser(FakePage(goto_error=FakeProxyError("Failed to parse proxy configuration")))
    diag = ScrapeDiagnostics()

    result = await html_module.get_page_source(URL, config=ScraperConfig(), diagnostics=diag)

    assert result == ""
    assert diag.last_failure.category == FailureCategory.INVALID_PROXY
    assert diag.last_failure.url == URL


async def test_unparseable_proxy_string_is_invalid_proxy(fake_browser):
    # A bare scheme with no host cannot be normalized by parse_proxy().
    diag = ScrapeDiagnostics()

    result = await html_module.get_page_source(
        URL, proxy="http://", config=ScraperConfig(), diagnostics=diag
    )

    assert result == ""
    failure = diag.last_failure
    assert failure.category == FailureCategory.INVALID_PROXY
    assert failure.stage == Stage.FETCH
    assert failure.url == URL


async def test_generic_fetch_exception_is_fetch_error(fake_browser):
    fake_browser(FakePage(goto_error=RuntimeError("browser crashed unexpectedly")))
    diag = ScrapeDiagnostics()

    result = await html_module.get_page_source(URL, config=ScraperConfig(), diagnostics=diag)

    assert result == ""
    failure = diag.last_failure
    assert failure.category == FailureCategory.FETCH_ERROR
    assert failure.exception_type == "RuntimeError"
    assert failure.url == URL


async def test_valid_proxy_is_forwarded_to_launch(fake_browser):
    page = fake_browser(FakePage(SAMPLE_HTML))
    diag = ScrapeDiagnostics()

    result = await html_module.get_page_source(
        URL,
        proxy="http://user:pass@proxy.local:8080",
        config=ScraperConfig(),
        diagnostics=diag,
    )

    assert result == SAMPLE_HTML
    # The fake chromium remembers the launch kwargs on its browser.
    fake_playwright = html_module.async_playwright.last_playwright()
    proxy_arg = fake_playwright.chromium.browser.launch_kwargs["proxy"]
    assert proxy_arg == {
        "server": "http://proxy.local:8080",
        "username": "user",
        "password": "pass",
    }
    assert diag.ok is True
    assert page.navigations[0]["url"] == URL
