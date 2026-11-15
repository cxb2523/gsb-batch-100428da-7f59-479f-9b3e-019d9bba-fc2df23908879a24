"""Shared helpers and test doubles for the Ofek-bot tests.

The module under test lives in app/app.py, which is not a package, so it is
loaded from its file path once and shared by all tests.
"""

import importlib.util
import sys
from pathlib import Path

APP_PATH = Path(__file__).resolve().parents[1] / "app" / "app.py"


def load_app_module():
    """Load app/app.py as a module under the name ``ofek_app``."""
    spec = importlib.util.spec_from_file_location("ofek_app", APP_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["ofek_app"] = module
    spec.loader.exec_module(module)
    return module


APP = load_app_module()

TITLE_PREFIX = "מצב משימות אופק של "


def title_for(name):
    return TITLE_PREFIX + name


class FakeBrowser:
    def __init__(self):
        self.quit_called = False

    def quit(self):
        self.quit_called = True


class FakeCrawler:
    """Stand-in for Crawler with a scripted scrape result per username."""

    def __init__(self, results):
        # results maps username -> dict of raw counters, or an exception to raise.
        self.results = results
        self.browser = None
        self.opened = []
        self.closed = []
        self.crawled = []

    def init_browser(self):
        self.browser = FakeBrowser()
        self.opened.append(self.browser)
        return self.browser

    def close_browser(self, browser=None):
        driver = browser if browser is not None else self.browser
        self.closed.append(driver)
        driver.quit()
        if driver is self.browser:
            self.browser = None

    def crowl(self, username, password):
        self.crawled.append((username, password))
        result = self.results.get(username)
        if isinstance(result, BaseException):
            raise result
        if result is None:
            raise AssertionError("unexpected username: " + username)
        return dict(result)


class FakeNotifier:
    def __init__(self, fail_for=()):
        self.sent = []
        self.fail_for = set(fail_for)

    def send_notification(self, title, message):
        if title in self.fail_for:
            raise RuntimeError("notification channel down")
        self.sent.append((title, message))


def counters(todo="1", tofix="0", checked="5", waiting="2"):
    return {
        "todo": todo,
        "tofix": tofix,
        "checked": checked,
        "waiting": waiting,
    }


def kid(name, username=None, password="pw"):
    return {
        "name": name,
        "username": username if username is not None else name.lower(),
        "password": password,
    }
