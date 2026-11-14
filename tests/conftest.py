"""Fakes for crawler tests.

Everything here is locally constructed; no real Google Maps network is used.
"""

from __future__ import annotations

import os
import sys

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from core.extractors.maps_extractor import MapsExtractor  # noqa: E402


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Avoid the artificial inter-request delays in the extractor."""
    monkeypatch.setattr("core.extractors.maps_extractor.time.sleep", lambda _seconds: None)


# ---------------------------------------------------------------------------
# Simple fake page for unit tests of MapsExtractor helpers
# ---------------------------------------------------------------------------


class FakeLocator:
    """Minimal Playwright locator stand-in backed by a FakePage."""

    def __init__(self, page: "FakePage", selector: str) -> None:
        self._page = page
        self._selector = selector
        self.first = self

    def count(self) -> int:
        if self._selector in self._page.reviews_tab_selectors:
            return 1 if self._page.has_reviews_tab else 0
        return self._page.locator_counts.get(self._selector, 0)

    def inner_text(self, timeout: int | None = None) -> str:
        if self._selector in self._page.texts:
            return self._page.texts[self._selector]
        raise RuntimeError(f"no text for selector {self._selector}")

    def get_attribute(self, attribute: str, timeout: int | None = None) -> str | None:
        return self._page.attributes.get((self._selector, attribute))

    def click(self, timeout: int | None = None) -> None:
        return None


class FakeElement:
    """Minimal Playwright element stand-in."""

    def query_selector_all(self, selector: str) -> list:
        return []


class FakePage:
    """A fake Playwright page that serves scripted Google Maps detail pages.

    Args:
        name_selector_ok: Whether the company-name selector can be found.
        reviews_ok: Whether the reviews selectors appear.
        goto_error: Optional exception raised by ``goto``.
        has_reviews_tab: Whether the reviews tab exists (places without
            reviews have none).
    """

    reviews_tab_selectors = (
        'button[role="tab"][aria-label*="Rezensionen"]',
        'button[role="tab"][aria-label*="Reviews"]',
    )

    def __init__(
        self,
        name_selector_ok: bool = True,
        reviews_ok: bool = True,
        goto_error: Exception | None = None,
        has_reviews_tab: bool = True,
    ) -> None:
        self.name_selector_ok = name_selector_ok
        self.reviews_ok = reviews_ok
        self.has_reviews_tab = has_reviews_tab
        self.goto_error = goto_error
        self.navigated_to: list[str] = []
        self.locator_counts: dict[str, int] = {}
        self.texts: dict[str, str] = {
            MapsExtractor.NAME_SELECTOR: "Test Company",
            MapsExtractor.RATING_SELECTOR: "4,5 (120)",
            MapsExtractor.ADDRESS_CONTAINER_SELECTOR: "Test Street 1",
            MapsExtractor.PHONE_CONTAINER_SELECTOR: "+49 123 456",
            MapsExtractor.PLUS_CODE_SELECTOR: "ABC123",
        }
        self.attributes: dict[tuple[str, str], str] = {
            (MapsExtractor.WEBSITE_SELECTOR, "href"): "https://example.com",
        }

    def goto(self, url: str, timeout: int | None = None, wait_until: str | None = None) -> None:
        self.navigated_to.append(url)
        if self.goto_error is not None:
            raise self.goto_error

    def wait_for_selector(self, selector: str, timeout: int | None = None, **kwargs):
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        if selector == MapsExtractor.NAME_SELECTOR:
            if not self.name_selector_ok:
                raise PlaywrightTimeoutError("name selector not found")
            return FakeElement()

        if selector in (
            MapsExtractor.REVIEWS_SCROLL_CONTAINER_SELECTOR,
            MapsExtractor.REVIEW_ITEM_SELECTOR,
        ):
            if not self.reviews_ok:
                raise PlaywrightTimeoutError("reviews not found")
            return FakeElement()

        return FakeElement()

    def wait_for_timeout(self, timeout: int | None = None) -> None:
        return None

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    def query_selector(self, selector: str):
        return None

    def query_selector_all(self, selector: str) -> list:
        return []

    def evaluate(self, script: str):
        return 0

    def click(self, selector: str, timeout: int | None = None) -> None:
        return None


# ---------------------------------------------------------------------------
# Fake Google Maps results page for end-to-end tests of crawl()
# ---------------------------------------------------------------------------


class FakeFeedLink:
    """A link element inside the fake results feed."""

    def __init__(self, href: str) -> None:
        self._href = href

    def get_attribute(self, name: str) -> str | None:
        return self._href if name == "href" else None


class FakeFeedElement:
    """The fake Google Maps results feed."""

    def __init__(self, links: list[str]) -> None:
        self._links = list(links)

    def query_selector_all(self, selector: str) -> list[FakeFeedLink]:
        return [FakeFeedLink(link) for link in self._links]


class FakeDetailLocator:
    """Locator for detail-page selectors of :class:`FakeGoogleMapsPage`."""

    reviews_tab_selectors = (
        'button[role="tab"][aria-label*="Rezensionen"]',
        'button[role="tab"][aria-label*="Reviews"]',
    )

    def __init__(self, page: "FakeGoogleMapsPage", selector: str) -> None:
        self._page = page
        self._selector = selector
        self.first = self

    def count(self) -> int:
        if self._selector in self.reviews_tab_selectors:
            return 1 if self._page.has_reviews_tab else 0
        return 0

    def inner_text(self, timeout: int | None = None) -> str:
        url = self._page.current_url
        if self._selector == MapsExtractor.NAME_SELECTOR:
            return self._page.company_names.get(url, "Company")
        if self._selector == MapsExtractor.RATING_SELECTOR:
            return "4,5 (10)"
        if self._selector == MapsExtractor.ADDRESS_CONTAINER_SELECTOR:
            return "Street 1"
        raise RuntimeError("no text")

    def get_attribute(self, attribute: str, timeout: int | None = None) -> str | None:
        if (
            self._selector == MapsExtractor.WEBSITE_SELECTOR
            and attribute == "href"
        ):
            return self._page.website
        return None

    def click(self, timeout: int | None = None) -> None:
        return None


class FakeGoogleMapsPage:
    """A fake page that serves a results feed followed by scripted detail pages.

    Args:
        links: Result links, in feed order.
        behavior: Per-link outcome: ``"ok"``, ``"reviews_timeout"``, or an
            exception instance raised on navigation to that detail page.
        company_names: Optional mapping from link to displayed company name.
        website: Website href exposed on every detail page.
    """

    def __init__(
        self,
        links: list[str],
        behavior: dict[str, object],
        company_names: dict[str, str] | None = None,
        website: str = "https://example.com",
        has_reviews_tab: bool = True,
    ) -> None:
        self.links = list(links)
        self.behavior = dict(behavior)
        self.company_names = dict(company_names or {})
        self.website = website
        self.has_reviews_tab = has_reviews_tab
        self.current_url: str | None = None
        self.navigated_to: list[str] = []
        self.feed = FakeFeedElement(links)

    def goto(self, url: str, timeout: int | None = None, wait_until: str | None = None) -> None:
        self.navigated_to.append(url)
        result = self.behavior.get(url)
        if isinstance(result, Exception):
            raise result
        self.current_url = url

    def wait_for_selector(self, selector: str, timeout: int | None = None, **kwargs):
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        if selector == MapsExtractor.FEED_SELECTOR:
            return self.feed

        if self.current_url is None:
            raise PlaywrightTimeoutError(f"{selector} not on the results page")

        result = self.behavior.get(self.current_url)
        if selector == MapsExtractor.NAME_SELECTOR:
            if isinstance(result, Exception):
                raise PlaywrightTimeoutError("name selector not found")
            return FakeFeedLink("")
        if selector in (
            MapsExtractor.REVIEWS_SCROLL_CONTAINER_SELECTOR,
            MapsExtractor.REVIEW_ITEM_SELECTOR,
        ):
            if result == "reviews_timeout" or isinstance(result, Exception):
                raise PlaywrightTimeoutError("reviews not found")
            return FakeFeedLink("")
        return FakeFeedLink("")

    def wait_for_timeout(self, timeout: int | None = None) -> None:
        return None

    def locator(self, selector: str) -> FakeDetailLocator:
        return FakeDetailLocator(self, selector)

    def query_selector(self, selector: str):
        return None

    def query_selector_all(self, selector: str) -> list:
        return []

    def evaluate(self, script: str):
        return 0


class FakeBrowserManager:
    """Fake BrowserManager that returns a fake page and never launches a browser."""

    def __init__(self, page: FakeGoogleMapsPage) -> None:
        self.page = page
        self.prompt: str | None = None
        self.closed = False

    def initialize(self) -> "FakeBrowserManager":
        return self

    def navigate_to_maps(self, prompt: str) -> FakeGoogleMapsPage:
        self.prompt = prompt
        return self.page

    def close(self) -> None:
        self.closed = True
