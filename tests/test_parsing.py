# -*- coding: utf-8 -*-
"""Parsing helper tests (prices, list page classification, phone numbers)."""

import pytest

from olx_scraper.parsing import (
    LIST_EMPTY,
    LIST_HAS_ADS,
    LIST_INVALID,
    detect_list_page_state,
    parse_price,
)
from olx_scraper.spiders.scrape_olx import normalize_phone

from fixtures import (
    BASE,
    LIST_PAGE_BLOCKED,
    LIST_PAGE_EMPTY,
    LIST_PAGE_INVALID,
    LIST_PAGE_WITH_ADS,
    make_response,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Rs 1,25,000", 125000),
        ("Rs. 25,000", 25000),
        ("25000", 25000),
        ("Rs 350 000", 350000),
        ("  Rs 1 500 ", 1500),
        ("PKR 99", 99),
        ("Rs 12.5", 12),
    ],
)
def test_parse_price_understands_common_formats(raw, expected):
    assert parse_price(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [None, "", "   ", "Negotiable", "Call for price", "Rs", "Price on request"],
)
def test_parse_price_returns_none_for_unparseable_values(raw):
    assert parse_price(raw) is None


def test_detect_list_page_with_ads():
    response = make_response(BASE, LIST_PAGE_WITH_ADS)
    assert detect_list_page_state(response) == LIST_HAS_ADS


def test_detect_list_page_empty_is_success_not_invalid():
    response = make_response(BASE, LIST_PAGE_EMPTY)
    assert detect_list_page_state(response) == LIST_EMPTY


def test_detect_list_page_invalid_structure():
    response = make_response(BASE, LIST_PAGE_INVALID)
    assert detect_list_page_state(response) == LIST_INVALID


def test_detect_blocked_page_is_not_reported_as_empty():
    response = make_response(BASE, LIST_PAGE_BLOCKED)
    assert detect_list_page_state(response) == LIST_INVALID


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("0300 1234567", "+923001234567"),
        ("0321-7654321", "+923217654321"),
        ("+92 300 1234567", "+923001234567"),
        ("3001234567", "+923001234567"),
        ("12345", None),
        ("Not available", None),
        (None, None),
    ],
)
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected
