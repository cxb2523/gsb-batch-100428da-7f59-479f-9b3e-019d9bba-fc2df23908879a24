"""Tests for PlaywrightAdapter page reconfirmation.

After EVERY execute() action the adapter must re-confirm which page it drives:
switch to a tab opened during the action, fall back to the last open page when
the current one was closed, and raise a clear error when no page remains.
"""

from __future__ import annotations

import pytest

from qirabot.adapters.playwright_adapter import PlaywrightAdapter
from qirabot.exceptions import NoOpenPageError


class FakeMouse:
    def __init__(self) -> None:
        self.clicks: list[tuple] = []
        self.wheels: list[tuple] = []
        self.on_click = None
        self.on_wheel = None

    def click(self, x, y, **kwargs):
        self.clicks.append((x, y, kwargs))
        if self.on_click:
            self.on_click()

    def dblclick(self, x, y):
        self.clicks.append((x, y, {"double": True}))

    def move(self, x, y, **kwargs):
        pass

    def down(self):
        pass

    def up(self):
        pass

    def wheel(self, dx, dy):
        self.wheels.append((dx, dy))
        if self.on_wheel:
            self.on_wheel()


class FakeKeyboard:
    def __init__(self) -> None:
        self.pressed: list[str] = []
        self.typed: list[str] = []

    def press(self, key):
        self.pressed.append(key)

    def type(self, text):
        self.typed.append(text)


class FakePage:
    def __init__(self, context: FakeContext, url: str = "about:blank") -> None:
        self.context = context
        self.url = url
        self.closed = False
        self.mouse = FakeMouse()
        self.keyboard = FakeKeyboard()
        self.viewport_size = {"width": 1280, "height": 720}
        self.load_state_waits = 0
        self.screenshots = 0
        self.goto_urls: list[str] = []
        self.on_goto = None  # hook fired mid-goto (e.g. a script closing the tab)

    def is_closed(self):
        return self.closed

    def close(self):
        if not self.closed:
            self.closed = True
            self.context._pages.remove(self)

    def goto(self, url, wait_until=None):
        self.goto_urls.append(url)
        self.url = url
        if self.on_goto:
            self.on_goto()

    def go_back(self, wait_until=None):
        pass

    def reload(self):
        pass

    def wait_for_load_state(self, *args, **kwargs):
        self.load_state_waits += 1

    def wait_for_timeout(self, ms):
        pass

    def screenshot(self, **kwargs):
        self.screenshots += 1
        return b"shot"


class FakeContext:
    def __init__(self) -> None:
        self._pages: list[FakePage] = []
        self._handlers: list = []
        self.wait_for_event_calls = 0

    @property
    def pages(self):
        return list(self._pages)

    def on(self, event, handler):
        assert event == "page"
        self._handlers.append(handler)

    def remove_listener(self, event, handler):
        self._handlers.remove(handler)

    def wait_for_event(self, event, timeout=None):
        self.wait_for_event_calls += 1
        raise TimeoutError("no late popup in these tests")

    def new_page(self, url="about:blank"):
        page = FakePage(self, url=url)
        self._pages.append(page)
        return page

    def emit_page(self, page):
        """Simulate the browser firing the context's "page" event."""
        for handler in list(self._handlers):
            handler(page)


def make_adapter(start_url="https://list.example"):
    context = FakeContext()
    page = context.new_page(start_url)
    adapter = PlaywrightAdapter(page)
    return adapter, context, page


class TestReconfirmAfterEveryAction:
    def test_goto_closes_old_page_switches_to_last_open(self):
        """goto (or a script it triggers) closes the old tab: the adapter must
        follow to the remaining open page, and screenshot/click must land there."""
        adapter, context, page = make_adapter()
        other = context.new_page("https://other.example")
        page.on_goto = page.close  # navigation kills the tab it ran on

        adapter.execute("navigate", {"url": "https://detail.example"})

        assert adapter.current_target is other
        assert adapter.screenshot() == b"shot"
        assert other.screenshots == 1
        adapter.click(10, 20)
        assert other.mouse.clicks[-1][0:2] == (10, 20)

    def test_action_opens_new_tab(self):
        """A click that opens a popup tab: switch to it and wait for its load."""
        adapter, context, page = make_adapter()

        def open_popup():
            context.emit_page(context.new_page("https://popup.example"))

        page.mouse.on_click = open_popup
        adapter.execute("click", {"x": 5, "y": 5})

        new_page = context.pages[-1]
        assert adapter.current_target is new_page
        assert new_page.load_state_waits >= 1

    def test_non_whitelist_action_also_reconfirms(self):
        """The old code only checked after click/double_click/type_text/press_key;
        a scroll that opens a tab must trigger the same switch."""
        adapter, context, page = make_adapter()

        def open_popup():
            context.emit_page(context.new_page("https://popup.example"))

        page.mouse.on_wheel = open_popup
        adapter.execute("scroll", {"direction": "down", "amount": 300})

        assert adapter.current_target is context.pages[-1]

    def test_all_pages_closed_raises(self):
        """No page left in the context: raise a clear error instead of
        continuing to drive a closed page."""
        adapter, _, _ = make_adapter()

        with pytest.raises(NoOpenPageError, match="no page left"):
            adapter.close_tab()

    def test_ctrl_w_closing_last_tab_raises(self):
        adapter, _, _ = make_adapter()

        with pytest.raises(NoOpenPageError, match="no page left"):
            adapter.execute("press_key", {"key": "ctrl+w"})

    def test_close_tab_falls_back_to_remaining_page(self):
        adapter, context, _ = make_adapter()
        other = context.new_page("https://other.example")
        # current target is the first page; closing it must move to `other`
        adapter.close_tab()

        assert adapter.current_target is other

    def test_healthy_page_stays_current(self):
        adapter, _, page = make_adapter()
        adapter.execute("click", {"x": 1, "y": 1})
        assert adapter.current_target is page


class TestCloseListener:
    def test_close_removes_page_listener(self):
        adapter, context, _ = make_adapter()
        assert len(context._handlers) == 1

        adapter.close()
        assert context._handlers == []

    def test_close_is_idempotent(self):
        adapter, context, _ = make_adapter()
        adapter.close()
        adapter.close()  # second call must not raise
        assert context._handlers == []
