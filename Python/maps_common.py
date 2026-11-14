"""Shared, fault-tolerant Google Maps listing logic for the Python scrapers.

Both ``selenium_scraper.py`` and ``playwright_scraper.py`` delegate the
scroll loop, card parsing and CSV writing to this module so the two browser
drivers behave identically.  Nothing in here talks to a real Google Maps
page on purpose: every selector and the scroll/parse contract can be
exercised against a local fixture page (see ``tests/``).
"""

from __future__ import annotations

import csv
import os
import re
import time
from typing import Any, Callable, Dict, List, Tuple

# Fixed output contract.  These seven columns are always written, even when
# every card is missing a field or when no card was found at all.
CSV_HEADER = [
    "Name",
    "Rating",
    "Reviews",
    "Category",
    "Services",
    "Image",
    "Detail URL",
]

# Selectors shared with the original examples.  Kept in one place so the
# Selenium and Playwright paths cannot drift apart.
SELECTORS = {
    "feed": 'div[role="feed"]',
    "feed_container": 'div.m6QErb.DxyBCb.kA9KIf.dS8AEf.XiKgde.ecceSd[role="feed"]',
    "card": "div.Nv2PK.THOPZb.CpccDe",
    "name": ".qBF1Pd",
    "rating": 'span[aria-label*="stars"]',
    "reviews": ".UY7F9",
    "category": "div.W4Efsd > span",
    "services": "div.ah5Ghc > span",
    "image": 'img[src*="googleusercontent"]',
    "detail_link": "a.hfpxzc",
}

# Two consecutive scroll rounds without any panel growth means the feed has
# reached its bottom (or never had any results).
STALL_LIMIT = 2


def normalize_browser_env() -> None:
    """Point LOCALAPPDATA/APPDATA back at the real per-user folders when they
    have been redirected to a non-standard location.

    Chrome/Edge reject remote debugging ("DevToolsActivePort file doesn't
    exist") when these variables point outside the normal profile tree,
    because every user-data-dir then looks like the browser default.  This is
    a no-op on ordinary machines and harmless when the fallback folders are
    absent.  Mirrored by ``normalizeBrowserEnv`` in the Node.js shared module.
    """
    candidates = (
        ("LOCALAPPDATA", r"C:\Users\Administrator\AppData\Local"),
        ("APPDATA", r"C:\Users\Administrator\AppData\Roaming"),
    )
    marker = os.path.join("users", "administrator", "appdata").lower()
    for name, fallback in candidates:
        current = os.environ.get(name)
        if current and marker not in current.lower() and os.path.isdir(fallback):
            os.environ[name] = fallback


def scroll_feed(
    scroll_once: Callable[[], None],
    get_height: Callable[[], int],
    max_scrolls: int,
    scroll_pause: float,
    sleep: Callable[[float], None] = time.sleep,
) -> Tuple[int, str]:
    """Scroll a results panel until it stops growing.

    Parameters
    ----------
    scroll_once:
        Zero-argument callable that pushes the panel to its current bottom.
    get_height:
        Zero-argument callable returning the panel's current ``scrollHeight``.
    max_scrolls / scroll_pause:
        Keep the original semantics: at most ``max_scrolls`` rounds with a
        ``scroll_pause`` second wait after each one.

    Returns
    -------
    (scrolls_performed, stop_reason)
        ``stop_reason`` is ``"reached_bottom"`` when the panel height failed
        to grow for two consecutive rounds, or ``"max_scrolls_reached"``
        when every configured round was used.
    """
    last_height = get_height()
    stalls = 0
    scrolls = 0

    for scrolls in range(1, max_scrolls + 1):
        scroll_once()
        sleep(scroll_pause)
        new_height = get_height()

        if new_height > last_height:
            stalls = 0
            last_height = new_height
        else:
            stalls += 1
            if stalls >= STALL_LIMIT:
                return scrolls, "reached_bottom"

    return scrolls, "max_scrolls_reached"


def parse_cards(find_cards: Callable[[], List[Any]], parse_card: Callable[[Any], Dict[str, str]]) -> List[Dict[str, str]]:
    """Parse every card through a per-field fault-tolerant parser.

    A card that somehow raises while being parsed is skipped after being
    reported instead of taking the whole run down with it.
    """
    records: List[Dict[str, str]] = []
    for index, card in enumerate(find_cards()):
        try:
            records.append(parse_card(card))
        except Exception as exc:  # pragma: no cover - defensive guard
            print(f"Warning: could not parse card #{index}: {exc}")
    return records


def extract_rating(aria_label: Any) -> str:
    """Pull the first number (digits/dot) out of a ``... stars`` aria-label."""
    if not aria_label:
        return ""
    match = re.search(r"([\d.]+)", aria_label)
    return match.group(1) if match else ""


def extract_reviews(text: Any) -> str:
    """Pull a digit/comma number out of the review text and drop separators."""
    if not text:
        return ""
    match = re.search(r"([\d,]+)", text)
    return match.group(1).replace(",", "") if match else ""


def write_csv(path: str, records: List[Dict[str, str]]) -> None:
    """Write records with the fixed seven-column header.

    An empty ``records`` list still produces a header-only CSV so downstream
    consumers never see a missing or shuffled column.
    """
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_HEADER, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow({column: record.get(column, "") or "" for column in CSV_HEADER})


def report(records: List[Dict[str, str]], output_path: str, scrolls: int, stop_reason: str) -> None:
    """Print a truthful summary of the run, including the zero-result case."""
    count = len(records)
    if stop_reason == "reached_bottom":
        reason_text = "panel stopped growing (reached bottom)"
    else:
        reason_text = "max_scrolls limit reached"

    if count == 0:
        print(
            "No results collected: 0 records found. "
            f"Wrote header-only CSV to {output_path}."
        )
    else:
        print(f"Saved {count} records to {output_path}.")
    print(
        f"Scrolling stopped after {scrolls} scroll round(s): {reason_text}. "
        f"Records actually written: {count}."
    )
