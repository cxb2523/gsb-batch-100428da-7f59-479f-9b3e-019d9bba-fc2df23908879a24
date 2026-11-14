"""Google Maps listing scraper (Playwright / Python).

Mirrors :mod:`selenium_scraper` with the shared rules from :mod:`maps_common`,
so both drivers handle scroll stalls, missing fields and empty results the
same way.  Defaults target the live Google Maps page; tests pass a local
``file://`` URL instead.
"""

from __future__ import annotations

import os
import sys
import time

# Under isolated mode (python -I) some environments omit the script's own
# directory from sys.path, so make the sibling maps_common import resolvable.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from playwright.sync_api import sync_playwright

from maps_common import (
    SELECTORS,
    extract_rating,
    extract_reviews,
    normalize_browser_env,
    parse_cards,
    report,
    scroll_feed,
    write_csv,
)

try:  # Stealth is optional; fixture pages and minimal installs don't need it.
    from playwright_stealth import Stealth
except Exception:  # pragma: no cover - depends on the local environment
    Stealth = None

DEFAULT_URL = "https://www.google.com/maps"
DEFAULT_QUERY = "restaurants in New York"
DEFAULT_OUTPUT = "maps_data_playwright.csv"


def _safe_call(action):
    """Turn any Playwright lookup error into an empty field."""
    try:
        return action()
    except Exception:
        return ""


def parse_card(card):
    """Extract one record. Every field is fault-tolerant and defaults to ''."""
    def first_text(selector):
        def action():
            locator = card.locator(selector)
            return locator.nth(0).inner_text() if locator.count() > 0 else ""
        return _safe_call(action)

    def first_attribute(selector, attribute):
        def action():
            locator = card.locator(selector)
            return locator.nth(0).get_attribute(attribute) if locator.count() > 0 else ""
        return _safe_call(action)

    name = first_text(SELECTORS["name"])

    rating_aria = first_attribute(SELECTORS["rating"], "aria-label")
    rating = extract_rating(rating_aria)

    reviews_text = first_text(SELECTORS["reviews"])
    reviews = extract_reviews(reviews_text)

    category = first_text(SELECTORS["category"])

    def services_action():
        locator = card.locator(SELECTORS["services"])
        return ", ".join(locator.nth(i).inner_text() for i in range(locator.count()))

    services = _safe_call(services_action)

    image_url = first_attribute(SELECTORS["image"], "src")
    detail_url = first_attribute(SELECTORS["detail_link"], "href")

    return {
        "Name": name,
        "Rating": rating,
        "Reviews": reviews,
        "Category": category,
        "Services": services,
        "Image": image_url,
        "Detail URL": detail_url,
    }


def run_scraper(
    url=DEFAULT_URL,
    query=DEFAULT_QUERY,
    output_file=DEFAULT_OUTPUT,
    max_scrolls=10,
    scroll_pause=2,
    headless=False,
    channel=None,
):
    """Run the scraper and return ``(records, scrolls, stop_reason)``."""
    normalize_browser_env()
    with sync_playwright() as playwright:
        launch_kwargs = {
            "headless": headless,
            "args": [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        }
        if channel:
            launch_kwargs["channel"] = channel
        browser = playwright.chromium.launch(**launch_kwargs)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()

        if Stealth is not None:
            try:
                Stealth().apply_stealth_sync(page)
            except Exception:  # pragma: no cover - stealth best effort
                pass

        try:
            page.goto(url, wait_until="domcontentloaded")
            time.sleep(scroll_pause)

            search_box = page.locator("#searchboxinput")
            if search_box.count() > 0:
                search_box.fill(query)
                search_box.press("Enter")
                time.sleep(scroll_pause)

            scrollable = page.locator(SELECTORS["feed"])

            def scroll_once():
                # Locator.evaluate resolves the element and passes it as the
                # first argument; page.evaluate cannot take a Locator directly.
                scrollable.evaluate("el => el.scrollTop = el.scrollHeight")

            def get_height():
                return scrollable.evaluate("el => el.scrollHeight") or 0

            scrolls, stop_reason = scroll_feed(
                scroll_once, get_height, max_scrolls, scroll_pause
            )

            feed_container = page.locator(SELECTORS["feed_container"])
            card_locator = feed_container.locator(SELECTORS["card"])
            cards = [card_locator.nth(i) for i in range(card_locator.count())]
            records = parse_cards(lambda: cards, parse_card)

            write_csv(output_file, records)
            report(records, output_file, scrolls, stop_reason)
            return records, scrolls, stop_reason
        finally:
            context.close()
            browser.close()


def _env_bool(name, default):
    value = os.environ.get(name)
    return default if value is None else value.lower() in ("1", "true", "yes")


if __name__ == "__main__":
    run_scraper(
        url=os.environ.get("MAPS_URL", DEFAULT_URL),
        query=os.environ.get("MAPS_QUERY", DEFAULT_QUERY),
        output_file=os.environ.get("MAPS_OUTPUT", DEFAULT_OUTPUT),
        max_scrolls=int(os.environ.get("MAPS_MAX_SCROLLS", "10")),
        scroll_pause=float(os.environ.get("MAPS_SCROLL_PAUSE", "2")),
        headless=_env_bool("MAPS_HEADLESS", False),
        channel=os.environ.get("MAPS_BROWSER_CHANNEL") or None,
    )
