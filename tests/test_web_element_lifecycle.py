"""Local, network-free Playwright tests for WebElement lifecycle handling.

The tests load a self-made HTML fixture from ``tests/fixtures/web_fixture.html``
via ``file://`` URLs, so no external website is required. A real Chromium build
is still needed (run ``playwright install chromium`` once); the tests skip
cleanly when Playwright or its browser is unavailable.
"""
import pathlib
import warnings

import pytest

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "web_fixture.html"
FIXTURE_URL = FIXTURE.as_uri()

playwright_api = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_api.sync_playwright

from tarsier.core.web import (  # noqa: E402  (after importorskip)
    PageClosedError,
    PageNotActiveError,
    WebDesktop,
    WebElement,
    WebElementAmbiguityWarning,
    WebElementTimeoutError,
)


def _browser_available() -> bool:
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:
        return False


_BROWSER_OK = _browser_available()
requires_browser = pytest.mark.skipif(
    not _BROWSER_OK, reason="Playwright Chromium is not installed (run: playwright install chromium)"
)


@pytest.fixture()
def web():
    desktop = WebDesktop(headless=True)
    try:
        yield desktop
    finally:
        desktop.close()


def _open_tab(web: WebDesktop) -> WebElement:
    """Open a new tab on the local fixture and make it the active tab."""
    return web.new_page(FIXTURE_URL)


# --------------------------------------------------------------------------
# 1. Normal locating, typing and reading stay unchanged.
# --------------------------------------------------------------------------
@requires_browser
def test_normal_locate_type_read_and_click(web):
    page = web.goto(FIXTURE_URL)

    textbox = page.wait_for_element(role="textbox", name="Search query")
    assert textbox.role == "textbox"
    assert textbox.name == "Search query"
    textbox.type("tarsier")

    button = page.wait_for_element(role="button", name="Search")
    button.click()

    result = page.find(selector="#result")
    assert result.read() == "Result: tarsier"


@requires_browser
def test_find_returns_element_and_actions_chain(web):
    page = web.new_page(FIXTURE_URL)
    result = page.find(selector="#result").scroll_into_view()
    assert isinstance(result, WebElement)
    # `click` still returns the element so call sites can chain.
    assert page.button("Search").click() is not None


# --------------------------------------------------------------------------
# 2. Reusing an element after its tab was closed is reported, not silently
#    applied to the wrong page.
# --------------------------------------------------------------------------
@requires_browser
def test_reusing_element_after_tab_close_raises_page_closed(web):
    first_tab = web.goto(FIXTURE_URL)
    stale_button = first_tab.wait_for_element(role="button", name="Search")

    # Closing a tab shifts WebDesktop's active page; opening another makes the
    # stale element's ownership unambiguous for the assertion below.
    first_tab.page.close()
    _open_tab(web)

    with pytest.raises(PageClosedError) as excinfo:
        stale_button.click()
    message = str(excinfo.value)
    assert "closed" in message
    assert "name='Search'" in message

    with pytest.raises(PageClosedError):
        stale_button.read()


@requires_browser
def test_find_on_stale_element_after_tab_close_raises_page_closed(web):
    first_tab = web.goto(FIXTURE_URL)
    stale_page_element = first_tab.find(selector="body")
    first_tab.page.close()
    _open_tab(web)

    with pytest.raises(PageClosedError):
        stale_page_element.find(role="button", name="Search")


@requires_browser
def test_closed_tab_error_is_distinct_from_timeout(web):
    first_tab = web.goto(FIXTURE_URL)
    stale = first_tab.wait_for_element(role="button", name="Search")
    stale_title = "Search"
    first_tab.page.close()
    _open_tab(web)

    # Lifecycle errors must not be swallowed by `except TimeoutError` handlers
    # that callers already use around wait_for_element calls.
    assert not issubclass(PageClosedError, TimeoutError)
    with pytest.raises(PageClosedError):
        stale.find(role="button", name=stale_title)


# --------------------------------------------------------------------------
# 3. An element from a tab that is open but no longer active is refused.
# --------------------------------------------------------------------------
@requires_browser
def test_reusing_element_from_inactive_tab_raises_page_not_active(web):
    first_tab = web.goto(FIXTURE_URL)
    first_button = first_tab.wait_for_element(role="button", name="Search")
    second_tab = _open_tab(web)

    with pytest.raises(PageNotActiveError) as excinfo:
        first_button.click()
    message = str(excinfo.value)
    assert "not the active tab" in message
    assert "switch_to_page(0)" in message
    assert not isinstance(excinfo.value, PageClosedError)

    # Switching back restores normal behaviour for the same element instance.
    switched = web.switch_to_page(0)
    assert switched.page is first_tab.page
    first_button.focus()  # must not raise now

    # The second tab's elements are refused too while tab 0 is active.
    with pytest.raises(PageNotActiveError):
        second_tab.find(role="button", name="Search")

    # Re-activating the second tab makes its elements usable again.
    web.switch_to_page(1)
    second_tab.find(role="button", name="Search").click()


@requires_browser
def test_newly_opened_tab_element_is_usable(web):
    web.goto(FIXTURE_URL)
    second_tab = _open_tab(web)
    second_tab.wait_for_element(role="button", name="Search").click()
    assert web.get_current_page().page is second_tab.page


# --------------------------------------------------------------------------
# 4. Removing an element makes the semantic wait time out with a useful
#    message naming the role/name and the actual wait duration.
# --------------------------------------------------------------------------
@requires_browser
def test_wait_for_removed_element_times_out_with_context(web):
    page = web.goto(FIXTURE_URL)
    page.page.evaluate("document.querySelector('#removable').remove()")

    with pytest.raises(WebElementTimeoutError) as excinfo:
        page.wait_for_element(role="button", name="Temporary", timeout=1)

    message = str(excinfo.value)
    assert "role='button'" in message
    assert "name='Temporary'" in message
    assert "seconds" in message
    # Actual elapsed time is reported, not just the configured timeout.
    assert "Timed out after" in message
    assert "configured timeout was 1 second" in message
    assert isinstance(excinfo.value, TimeoutError)


@requires_browser
def test_wait_for_removed_element_with_selector_times_out(web):
    page = web.goto(FIXTURE_URL)
    page.page.evaluate("document.querySelector('#removable').remove()")

    with pytest.raises(WebElementTimeoutError) as excinfo:
        page.wait_for_element(selector="#temporary", timeout=1)
    assert "selector='#temporary'" in excinfo.value.__str__()


@requires_browser
def test_wait_for_all_invisible_matches_times_out(web):
    page = web.goto(FIXTURE_URL)
    with pytest.raises(WebElementTimeoutError) as excinfo:
        page.wait_for_element(role="link", name="Same", timeout=1)
    message = str(excinfo.value)
    assert "role='link'" in message
    assert "0 of them visible" in message


# --------------------------------------------------------------------------
# 5. Multiple role/name matches: the first visible one is used and the
#    caller is told about the ambiguity.
# --------------------------------------------------------------------------
@requires_browser
def test_ambiguous_role_name_uses_first_visible_match(web):
    page = web.goto(FIXTURE_URL)

    with pytest.warns(WebElementAmbiguityWarning) as record:
        duplicate = page.wait_for_element(role="button", name="Duplicate")

    assert any("Ambiguous" in str(item.message) for item in record)
    assert duplicate.match_count == 2
    assert duplicate.visible_match_count == 1
    assert duplicate.is_ambiguous is True

    duplicate.click()
    assert page.find(selector="#duplicate-hit").read() == "duplicate-visible"


@requires_browser
def test_unambiguous_lookup_does_not_warn(web):
    page = web.goto(FIXTURE_URL)
    with warnings.catch_warnings():
        warnings.simplefilter("error", WebElementAmbiguityWarning)
        button = page.wait_for_element(role="button", name="Search")
    assert button.match_count == 1
    assert button.is_ambiguous is False


@requires_browser
def test_element_relocated_after_render_is_fresh(web):
    page = web.goto(FIXTURE_URL)
    old_button = page.find(selector="#temporary")
    # Simulate a re-render that replaces the node behind the same selector.
    page.page.evaluate(
        "const old = document.querySelector('#temporary');"
        "old.replaceWith(old.cloneNode(true));"
    )
    # Locators are re-resolved on use, so the same WebElement keeps working.
    assert old_button.read() == "Temporary"
    assert web.get_current_page().find(selector="#temporary").read() == "Temporary"
