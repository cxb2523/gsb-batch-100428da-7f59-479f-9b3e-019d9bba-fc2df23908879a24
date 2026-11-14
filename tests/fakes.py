# SPDX-License-Identifier: MIT

"""Minimal, fully in-memory Playwright stand-ins.

These fakes implement only the surface that ``get_page_source`` touches, so
the fetch layer can be exercised without launching a browser or touching the
network.
"""


class FakeKeyboard:
    def __init__(self):
        self.presses = []

    async def press(self, key):
        self.presses.append(key)


class FakePage:
    def __init__(self, content="", goto_error=None):
        self._content = content
        self._goto_error = goto_error
        self.keyboard = FakeKeyboard()
        self.navigations = []
        self.routes = []

    async def route(self, pattern, handler):
        self.routes.append((pattern, handler))

    async def goto(self, url, wait_until=None, timeout=None):
        self.navigations.append({"url": url, "wait_until": wait_until, "timeout": timeout})
        if self._goto_error is not None:
            raise self._goto_error

    async def wait_for_timeout(self, milliseconds):
        return None

    async def content(self):
        return self._content


class FakeContext:
    def __init__(self, page):
        self._page = page

    async def new_page(self):
        return self._page


class FakeBrowser:
    def __init__(self, page):
        self._page = page
        self.closed = False
        self.launch_kwargs = None

    async def new_context(self, **kwargs):
        return FakeContext(self._page)

    async def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, page):
        self._page = page
        self.browser = None

    async def launch(self, **kwargs):
        self.browser = FakeBrowser(self._page)
        self.browser.launch_kwargs = kwargs
        return self.browser


class FakePlaywright:
    def __init__(self, page):
        self.chromium = FakeChromium(page)
        self.stopped = False

    async def stop(self):
        self.stopped = True


class FakeAsyncPlaywrightContext:
    """Object returned by ``async_playwright()``; ``.start()`` gives a FakePlaywright.

    Mirrors the real API shape used by ``get_page_source``::

        playwright = await async_playwright().start()
    """

    def __init__(self, page):
        self._page = page
        self._playwright = FakePlaywright(page)

    async def start(self):
        return self._playwright


def build_fake_async_playwright(page):
    """Return an ``async_playwright`` replacement bound to one fake page.

    The returned callable exposes ``last_playwright`` so tests can inspect the
    browser launch arguments after ``get_page_source`` has run.
    """

    state = {"last": None}

    def _async_playwright():
        # Real playwright: calling async_playwright() returns a context manager
        # object; the browser is obtained via ``await ... .start()``.
        context = FakeAsyncPlaywrightContext(page)
        state["last"] = context._playwright
        return context

    _async_playwright.last_playwright = lambda: state["last"]
    return _async_playwright


class FakeTimeoutError(Exception):
    """Stands in for playwright's TimeoutError when the real one is absent."""


class FakeProxyError(Exception):
    """Stands in for a proxy-related launch/navigation error."""
