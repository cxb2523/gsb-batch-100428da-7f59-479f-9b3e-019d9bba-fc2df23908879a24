"""Fast, browser-free unit tests for WebElement's page-ownership guard.

These use lightweight stand-ins for Playwright's Page/Locator so the lifecycle
logic itself is covered even on machines without a Chromium download.
"""
import warnings

import pytest

from tarsier.core.web import (
    PageClosedError,
    PageNotActiveError,
    WebElement,
    WebElementAmbiguityWarning,
    _register_active_page,
)


class FakeLocator:
    """Minimal Locator stand-in: fixed candidate list, all assumed visible."""

    def __init__(self, nodes=None, visible=None):
        self.nodes = list(nodes or [])
        self._visible = visible

    @property
    def first(self):
        return self

    def nth(self, index):
        visible = self._visible if self._visible is not None else [True] * len(self.nodes)
        return FakeLocator([self.nodes[index]], visible=[visible[index]])

    def filter(self, visible=None):
        visible_flags = self._visible if self._visible is not None else [True] * len(self.nodes)
        keep = [
            (node, flag)
            for node, flag in zip(self.nodes, visible_flags)
            if visible is None or flag == visible
        ]
        return FakeLocator([n for n, _ in keep], visible=[f for _, f in keep])

    def count(self):
        return len(self.nodes)

    def is_visible(self):
        if self._visible is None:
            return bool(self.nodes)
        return bool(self._visible[0])

    def inner_text(self):
        return "text"

    # Action methods just record the call.
    def click(self):
        self._called = "click"

    def fill(self, text):
        self._called = ("fill", text)

    def focus(self):
        self._called = "focus"


class FakePage:
    def __init__(self, context, title="tab", closed=False):
        self._context = context
        self._closed = closed
        self._calls = []

    @property
    def context(self):
        return self._context

    @property
    def url(self):
        return f"file:///{self._title}"

    @property
    def _title(self):
        return self._title_value

    @property
    def is_closed(self):
        return lambda: self._closed

    def close(self):
        self._closed = True
        self._context._pages.remove(self)

    def locator(self, selector):
        self._calls.append(("locator", selector))
        return FakeLocator([self._title])

    def get_by_role(self, role, name=None, exact=False):
        self._calls.append(("get_by_role", role, name))
        return FakeLocator([self._title])

    def get_by_text(self, text, exact=False):
        return FakeLocator([self._title])


class FakeContext:
    def __init__(self):
        self._pages = []

    @property
    def pages(self):
        return list(self._pages)

    def add(self, page):
        self._pages.append(page)
        return page


def make_page(context, title):
    page = FakePage(context, title)
    page._title_value = title
    context.add(page)
    return page


def test_click_on_active_page_succeeds():
    ctx = FakeContext()
    page = make_page(ctx, "active")
    _register_active_page(page)

    locator = FakeLocator(["node"])
    element = WebElement(page, locator, role="button", name="Go")
    element.click()

    assert getattr(locator, "_called", None) == "click"


def test_click_on_closed_page_raises_page_closed():
    ctx = FakeContext()
    page = make_page(ctx, "closed-tab")
    page._closed = True

    element = WebElement(page, FakeLocator(["node"]), role="button", name="Go")
    with pytest.raises(PageClosedError):
        element.click()


def test_click_on_inactive_page_raises_page_not_active():
    ctx = FakeContext()
    first = make_page(ctx, "first")
    second = make_page(ctx, "second")
    _register_active_page(second)

    element = WebElement(first, FakeLocator(["node"]), role="button", name="Go")
    with pytest.raises(PageNotActiveError) as excinfo:
        element.click()
    assert "switch_to_page(0)" in str(excinfo.value)
    assert not isinstance(excinfo.value, PageClosedError)


def test_errors_are_distinguishable_types():
    assert issubclass(PageClosedError, Exception)
    assert issubclass(PageNotActiveError, Exception)
    assert not issubclass(PageClosedError, PageNotActiveError)
    assert not issubclass(PageNotActiveError, PageClosedError)


def test_read_on_inactive_page_does_not_touch_locator():
    ctx = FakeContext()
    first = make_page(ctx, "first")
    second = make_page(ctx, "second")
    _register_active_page(second)

    element = WebElement(first, FakeLocator(["node"]), role="button", name="Go")
    with pytest.raises(PageNotActiveError):
        element.read()


def test_ambiguity_warning_lists_first_visible_match():
    ctx = FakeContext()
    page = make_page(ctx, "tab")
    _register_active_page(page)
    locator = FakeLocator(["hidden", "visible"], visible=[False, True])

    element = WebElement(page, locator, role="button", name="Save")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", WebElementAmbiguityWarning)
        total, visible, chosen = element._resolve_semantic_match()

    assert (total, visible, chosen) == (2, 1, 1)
    assert element.is_ambiguous
    assert element._action_locator.nodes == ["visible"]


def test_no_ambiguity_warning_for_single_match():
    ctx = FakeContext()
    page = make_page(ctx, "tab")
    _register_active_page(page)

    with warnings.catch_warnings():
        warnings.simplefilter("error", WebElementAmbiguityWarning)
        element = WebElement(page, FakeLocator(["only"]), role="button", name="Save")
        element._resolve_semantic_match()
    assert element.match_count == 1
    assert not element.is_ambiguous
