"""Google Maps listing scraper (Selenium / Python).

The scrolling, parsing and CSV rules live in :mod:`maps_common` so this file
only contains the Selenium glue.  Defaults still target the live Google Maps
page; pass ``url`` / ``headless`` to run the same code against a local
fixture page in the test suite.
"""

from __future__ import annotations

import os
import shutil
import sys
import time

# Under isolated mode (python -I) some environments omit the script's own
# directory from sys.path, so make the sibling maps_common import resolvable.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

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

DEFAULT_URL = "https://www.google.com/maps"
DEFAULT_QUERY = "restaurants in New York"
DEFAULT_OUTPUT = "maps_data.csv"


def _resolve_chromedriver():
    """Locate a chromedriver without relying on Selenium Manager's network
    lookup, which hangs in restricted/offline environments.

    Order: CHROMEDRIVER_PATH override -> a driver on PATH -> a chromedriver
    installed alongside this checkout (e.g. the Node ``chromedriver``
    package).  Returning None lets Selenium fall back to its own discovery.
    """
    override = os.environ.get("CHROMEDRIVER_PATH")
    if override and os.path.isfile(override):
        return override

    on_path = shutil.which("chromedriver")
    if on_path:
        return on_path

    exe = "chromedriver.exe" if sys.platform.startswith("win") else "chromedriver"
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(repo_root, "NodeJS", "node_modules", "chromedriver", "lib", "chromedriver", exe),
        os.path.join(repo_root, "drivers", exe),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


def _profile_directory():
    """Return a fresh, writable per-run Chrome profile directory.

    On some sandboxes the default temp profile (under a redirected SystemTemp
    or LOCALAPPDATA) causes Chrome to drop its renderer when sessions are
    created back to back. A dedicated directory we control avoids that.
    """
    import tempfile

    base = os.environ.get("MAPS_PROFILE_ROOT") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "tmp", "chrome-profiles"
    )
    os.makedirs(base, exist_ok=True)
    return tempfile.mkdtemp(prefix="profile-", dir=base)


def _safe_call(action):
    """Run a Selenium lookup, turning any driver error into an empty field."""
    try:
        return action()
    except Exception:
        return ""


def parse_card(driver, card):
    """Extract one record. Every field is fault-tolerant and defaults to ''."""
    def first_text(by, selector):
        def action():
            elements = card.find_elements(by, selector)
            return elements[0].text if elements else ""
        return _safe_call(action)

    def first_attribute(by, selector, attribute):
        def action():
            elements = card.find_elements(by, selector)
            return elements[0].get_attribute(attribute) if elements else ""
        return _safe_call(action)

    name = first_text(By.CSS_SELECTOR, SELECTORS["name"])

    rating_aria = first_attribute(By.XPATH, './/span[contains(@aria-label, "stars")]', "aria-label")
    rating = extract_rating(rating_aria)

    reviews_text = first_text(By.CSS_SELECTOR, SELECTORS["reviews"])
    reviews = extract_reviews(reviews_text)

    category = first_text(By.XPATH, './/div[contains(@class, "W4Efsd")]/span[1]')

    def services_action():
        elements = card.find_elements(By.XPATH, './/div[contains(@class, "ah5Ghc")]/span')
        return ", ".join(element.text for element in elements)

    services = _safe_call(services_action)

    image_url = first_attribute(By.XPATH, './/img[contains(@src, "googleusercontent")]', "src")
    detail_url = first_attribute(By.CSS_SELECTOR, SELECTORS["detail_link"], "href")

    return {
        "Name": name,
        "Rating": rating,
        "Reviews": reviews,
        "Category": category,
        "Services": services,
        "Image": image_url,
        "Detail URL": detail_url,
    }


def _build_options(profile_dir, headless):
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    # Stability flags: needed inside restricted/containerized environments
    # and harmless on ordinary desktops.  Pipe-based DevTools is more
    # reliable than the default debugging port (which intermittently loses
    # the renderer when many short-lived sessions are created back to back).
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-crash-reporter")
    options.add_argument("--disable-background-networking")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--remote-debugging-pipe")
    options.add_argument("--log-level=3")
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument("--window-size=1280,900")
    return options


# Browser/session errors worth a fresh-session retry in flaky environments.
_RETRYABLE_MARKERS = (
    "invalid session id",
    "session not created",
    "unable to receive message from renderer",
    "devtoolsactiveport",
    "web view not found",
    "chrome not reachable",
)


def _is_retryable(exc):
    message = str(exc).lower()
    return any(marker in message for marker in _RETRYABLE_MARKERS)


def run_scraper(
    url=DEFAULT_URL,
    query=DEFAULT_QUERY,
    output_file=DEFAULT_OUTPUT,
    max_scrolls=10,
    scroll_pause=2,
    headless=False,
    retries=3,
):
    """Run the scraper and return ``(records, scrolls, stop_reason)``.

    A crashed renderer / dropped session is retried with a brand-new browser
    session up to ``retries`` times, which keeps runs stable on sandboxes
    where Chrome occasionally loses its DevTools connection.
    """
    normalize_browser_env()
    driver_path = _resolve_chromedriver()
    last_error = None

    for attempt in range(1, retries + 1):
        profile_dir = _profile_directory()
        options = _build_options(profile_dir, headless)
        if driver_path:
            driver = webdriver.Chrome(service=Service(executable_path=driver_path), options=options)
        else:
            # Fall back to Selenium Manager's automatic discovery.
            driver = webdriver.Chrome(options=options)
        try:
            driver.get(url)
            time.sleep(scroll_pause)

            # The search box only exists on the live page; fixture pages skip it.
            search_boxes = driver.find_elements(By.ID, "searchboxinput")
            if search_boxes:
                search_boxes[0].send_keys(query)
                search_boxes[0].send_keys(Keys.ENTER)
                time.sleep(scroll_pause)

            scrollable = driver.find_element(By.CSS_SELECTOR, SELECTORS["feed"])

            def scroll_once():
                driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight", scrollable)

            def get_height():
                return driver.execute_script("return arguments[0].scrollHeight", scrollable) or 0

            scrolls, stop_reason = scroll_feed(scroll_once, get_height, max_scrolls, scroll_pause)

            feed_container = driver.find_element(By.CSS_SELECTOR, SELECTORS["feed_container"])
            cards = feed_container.find_elements(By.CSS_SELECTOR, SELECTORS["card"])
            records = parse_cards(lambda: cards, lambda card: parse_card(driver, card))

            write_csv(output_file, records)
            report(records, output_file, scrolls, stop_reason)
            return records, scrolls, stop_reason
        except Exception as exc:
            last_error = exc
            if attempt < retries and _is_retryable(exc):
                print(f"Browser session failed ({exc.__class__.__name__}); retrying {attempt}/{retries - 1}...")
                continue
            raise
        finally:
            try:
                driver.quit()
            except Exception:
                pass
            shutil.rmtree(profile_dir, ignore_errors=True)

    # Unreachable in practice; keeps the contract explicit.
    raise last_error  # pragma: no cover


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
    )
