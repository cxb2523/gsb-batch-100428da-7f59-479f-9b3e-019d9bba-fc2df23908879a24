# -*- coding: utf-8 -*-
"""Small, dependency free parsing helpers used by the OLX spider.

Keeping these functions separate makes them easy to exercise with local HTML
samples, without starting Scrapy or talking to the network.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Final

# ----------------------------------------------------------------------------
# List page classification
# ----------------------------------------------------------------------------
LIST_HAS_ADS: Final = "has_ads"
LIST_EMPTY: Final = "empty"
LIST_INVALID: Final = "invalid"

# Container used by the OLX version this project targets.
LIST_AD_XPATH: Final = "//td[contains(@class,'offer')]"

# Positive evidence that the page is a working search result page.
_EMPTY_MARKER_XPATHS: Final = (
    "//*[contains(@class,'empty')]",
    "//*[contains(@class,'noresult')]",
    "//*[contains(@class,'noResult')]",
    "//*[contains(@class,'no-result')]",
    "//*[contains(@class,'errormsg')]",
    "//*[contains(@class,'error-msg')]",
)

_EMPTY_TEXT_RE: Final = re.compile(
    r"no\s+(?:ads|advertisements|results|matches|items|listings)"
    r"|nothing\s+(?:found|to\s+show)"
    r"|0\s+(?:ads|results|matches|listings)",
    re.IGNORECASE,
)
_BLOCKED_TEXT_RE: Final = re.compile(
    r"captcha|access\s+denied|forbidden|service\s+unavailable"
    r"|maintenance|cloudflare|just\s+a\s+moment",
    re.IGNORECASE,
)


def _page_text(response: Any) -> str:
    try:
        parts = response.xpath("//text()").getall()
    except AttributeError:  # allow plain strings in unit tests
        return str(response)
    return " ".join(part.strip() for part in parts if part and part.strip())


def _has_xpath(response: Any, xpath: str) -> bool:
    try:
        return bool(response.xpath(xpath))
    except AttributeError:
        return False


def detect_list_page_state(response: Any) -> str:
    """Classify a list page response.

    Returns one of ``LIST_HAS_ADS``, ``LIST_EMPTY`` or ``LIST_INVALID``.

    ``LIST_INVALID`` means there is no ad container *and* no evidence that the
    page is a legitimate empty search result page.  Callers must treat that as
    a crawl failure instead of a successful zero-item run.
    """

    if _has_xpath(response, LIST_AD_XPATH):
        return LIST_HAS_ADS

    text = _page_text(response)

    # A blocked / error page must never be reported as "no ads".
    if _BLOCKED_TEXT_RE.search(text):
        return LIST_INVALID

    if _EMPTY_TEXT_RE.search(text):
        return LIST_EMPTY
    for xpath in _EMPTY_MARKER_XPATHS:
        if _has_xpath(response, xpath):
            return LIST_EMPTY

    return LIST_INVALID


# ----------------------------------------------------------------------------
# Price parsing
# ----------------------------------------------------------------------------
_CURRENCY_RE: Final = re.compile(r"(?:rs\.?|pkr|rupees?)", re.IGNORECASE)
_NUMBER_RE: Final = re.compile(r"\d+(?:\.\d+)?")
_SEPARATORS: Final = (" ", " ", ",", "_", "'")


def parse_price(value: Any) -> int | None:
    """Parse an OLX price string into a whole number.

    Returns ``None`` when the text cannot be understood (``"Negotiable"``,
    ``"Call for price"``, empty values, ...).  The leading ``Rs`` marker,
    thousands separators (``1,25,000``) and non breaking spaces are tolerated.
    """

    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    # Drop the currency marker before looking for the number.
    text = _CURRENCY_RE.sub(" ", text)
    for separator in _SEPARATORS:
        text = text.replace(separator, "")
    # Remaining spaces may be digit grouping ("25 000"); remove them too.
    text = text.replace(" ", "")

    match = _NUMBER_RE.search(text)
    if not match:
        return None

    try:
        amount = Decimal(match.group(0))
    except InvalidOperation:  # pragma: no cover - regex already guarantees this
        return None
    if amount < 0:  # pragma: no cover - regex only matches unsigned numbers
        return None
    return int(amount)


def normalize_price_text(value: Any) -> str:
    """Return a display friendly price string (``""`` for missing values)."""
    if value is None:
        return ""
    return " ".join(str(value).split())


__all__ = [
    "LIST_AD_XPATH",
    "LIST_EMPTY",
    "LIST_HAS_ADS",
    "LIST_INVALID",
    "detect_list_page_state",
    "normalize_price_text",
    "parse_price",
]
