"""Smoke tests para reglas de URL."""

import pytest

from crawlforge.rules._registry import REGISTRY
from crawlforge.rules.url.broken_bookmark import BrokenBookmark
from crawlforge.rules.url.query_more_than_three_params import QueryMoreThanThreeParams
from crawlforge.rules.url.query_paginated_params import QueryPaginatedParams
from crawlforge.rules.url.query_search_filter_params import QuerySearchFilterParams
from crawlforge.rules.url.query_sort_params import QuerySortParams
from crawlforge.rules.url.url_contains_space import UrlContainsSpace
from crawlforge.rules.url.url_ga_tracking_params import UrlGaTrackingParams
from crawlforge.rules.url.url_has_parameters import UrlHasParameters
from crawlforge.rules.url.url_internal_search import UrlInternalSearch
from crawlforge.rules.url.url_multiple_ga import UrlMultipleGa
from crawlforge.rules.url.url_multiple_gtm import UrlMultipleGtm
from crawlforge.rules.url.url_multiple_slashes import UrlMultipleSlashes
from crawlforge.rules.url.url_no_ga import UrlNoGa
from crawlforge.rules.url.url_no_gtm import UrlNoGtm
from crawlforge.rules.url.url_non_ascii import UrlNonAscii
from crawlforge.rules.url.url_over_115_chars import UrlOver115Chars
from crawlforge.rules.url.url_repetitive_path import UrlRepetitivePath
from crawlforge.rules.url.url_repetitive_query_params import UrlRepetitiveQueryParams
from crawlforge.rules.url.url_session_id_params import UrlSessionIdParams
from crawlforge.rules.url.url_underscores import UrlUnderscores
from crawlforge.rules.url.url_uppercase import UrlUppercase
from crawlforge.storage.schema import init_database


LONG_URL = "https://example.com/" + "a" * 120  # 140 chars


@pytest.fixture
def con():
    con = init_database()

    rows = [
        # 1: URL limpia (no debería disparar nada)
        (1, "https://example.com/clean-page"),
        # 2: doble slash en path
        (2, "https://example.com/path//double"),
        # 3: espacio
        (3, "https://example.com/with space"),
        # 4: %20
        (4, "https://example.com/with%20space"),
        # 5: caracteres no-ASCII (ñ)
        (5, "https://example.com/españa"),
        # 6: mayúsculas en path
        (6, "https://example.com/Path/Capital"),
        # 7: segmentos repetidos
        (7, "https://example.com/blog/blog/post"),
        # 8: search interno
        (8, "https://example.com/?q=hello"),
        # 9: query simple (utm + ga)
        (9, "https://example.com/?utm_source=fb&utm_medium=cpc"),
        # 10: gclid
        (10, "https://example.com/page?gclid=abc"),
        # 11: PHPSESSID
        (11, "https://example.com/?PHPSESSID=xyz"),
        # 12: parámetros repetidos
        (12, "https://example.com/?color=red&color=blue"),
        # 13: underscore en path
        (13, "https://example.com/my_page"),
        # 14: > 115 chars
        (14, LONG_URL),
        # 15: > 3 query params
        (15, "https://example.com/?a=1&b=2&c=3&d=4&e=5"),
        # 16: paginación
        (16, "https://example.com/blog?page=3"),
        # 17: filter
        (17, "https://example.com/shop?filter_color=red"),
        # 18: sort
        (18, "https://example.com/shop?sort=price&order=asc"),
    ]
    for url_id, url in rows:
        con.execute(
            "INSERT INTO urls (url_id, url, status_code) VALUES (?, ?, 200)",
            [url_id, url],
        )
    return con


# --- url_multiple_slashes --------------------------------------------------

def test_multiple_slashes_detects(con):
    flagged = {i.url_id for i in UrlMultipleSlashes().check(con)}
    assert 2 in flagged
    assert 1 not in flagged


# --- url_contains_space ----------------------------------------------------

def test_contains_space_literal(con):
    flagged = {i.url_id for i in UrlContainsSpace().check(con)}
    assert 3 in flagged


def test_contains_space_pct20(con):
    flagged = {i.url_id for i in UrlContainsSpace().check(con)}
    assert 4 in flagged


def test_contains_space_clean(con):
    flagged = {i.url_id for i in UrlContainsSpace().check(con)}
    assert 1 not in flagged


# --- url_non_ascii ---------------------------------------------------------

def test_non_ascii_detects(con):
    flagged = {i.url_id for i in UrlNonAscii().check(con)}
    assert 5 in flagged
    assert 1 not in flagged


# --- url_uppercase ---------------------------------------------------------

def test_uppercase_in_path(con):
    flagged = {i.url_id for i in UrlUppercase().check(con)}
    assert 6 in flagged
    assert 1 not in flagged
    # URL 9 (`?utm_source=...`) sólo tiene minúsculas en path/query: NO disparar
    assert 9 not in flagged


# --- url_repetitive_path ---------------------------------------------------

def test_repetitive_path_detects(con):
    flagged = {i.url_id for i in UrlRepetitivePath().check(con)}
    assert 7 in flagged
    assert 1 not in flagged


# --- url_internal_search ---------------------------------------------------

def test_internal_search_detects(con):
    flagged = {i.url_id for i in UrlInternalSearch().check(con)}
    assert 8 in flagged
    assert 9 not in flagged  # utm_source no es búsqueda


# --- url_has_parameters ----------------------------------------------------

def test_has_parameters_flags_all_with_q(con):
    flagged = {i.url_id for i in UrlHasParameters().check(con)}
    # Cualquier URL con `?` debería caer (LONG_URL/14 no lo tiene)
    for uid in (8, 9, 10, 11, 12, 15, 16, 17, 18):
        assert uid in flagged, f"expected {uid} flagged, got {flagged}"
    assert 1 not in flagged
    assert 14 not in flagged


# --- url_ga_tracking_params ------------------------------------------------

def test_ga_tracking_detects_utm(con):
    flagged = {i.url_id for i in UrlGaTrackingParams().check(con)}
    assert 9 in flagged
    assert 10 in flagged  # gclid


def test_ga_tracking_ignores_clean(con):
    flagged = {i.url_id for i in UrlGaTrackingParams().check(con)}
    assert 1 not in flagged
    assert 11 not in flagged


# --- url_session_id_params -------------------------------------------------

def test_session_id_detects_phpsessid(con):
    flagged = {i.url_id for i in UrlSessionIdParams().check(con)}
    assert 11 in flagged
    assert 1 not in flagged
    assert 9 not in flagged


# --- url_repetitive_query_params -------------------------------------------

def test_repetitive_query_params_detects(con):
    issues = list(UrlRepetitiveQueryParams().check(con))
    flagged = {i.url_id for i in issues}
    assert 12 in flagged
    assert 9 not in flagged  # utm_source y utm_medium son keys distintas
    [issue] = [i for i in issues if i.url_id == 12]
    assert issue.evidence["duplicate_params"] == {"color": 2}


# --- url_underscores -------------------------------------------------------

def test_underscores_detects(con):
    flagged = {i.url_id for i in UrlUnderscores().check(con)}
    assert 13 in flagged
    assert 1 not in flagged
    # url 9 tiene utm_source: contiene `_` en query, debe disparar también
    assert 9 in flagged


# --- url_over_115_chars ----------------------------------------------------

def test_over_115_chars_detects(con):
    flagged = {i.url_id for i in UrlOver115Chars().check(con)}
    assert 14 in flagged
    assert 1 not in flagged


# --- query_more_than_three_params ------------------------------------------

def test_more_than_three_params(con):
    flagged = {i.url_id for i in QueryMoreThanThreeParams().check(con)}
    assert 15 in flagged
    assert 9 not in flagged  # solo 2
    assert 1 not in flagged


# --- query_paginated_params ------------------------------------------------

def test_paginated_params_detects(con):
    flagged = {i.url_id for i in QueryPaginatedParams().check(con)}
    assert 16 in flagged
    assert 1 not in flagged


# --- query_search_filter_params --------------------------------------------

def test_search_filter_params_detects(con):
    flagged = {i.url_id for i in QuerySearchFilterParams().check(con)}
    assert 17 in flagged
    assert 1 not in flagged


# --- query_sort_params -----------------------------------------------------

def test_sort_params_detects(con):
    flagged = {i.url_id for i in QuerySortParams().check(con)}
    assert 18 in flagged
    assert 1 not in flagged


# --- skipped (TODO schema) -------------------------------------------------

def test_broken_bookmark_returns_empty(con):
    assert list(BrokenBookmark().check(con)) == []


def test_url_multiple_gtm_returns_empty(con):
    assert list(UrlMultipleGtm().check(con)) == []


def test_url_multiple_ga_returns_empty(con):
    assert list(UrlMultipleGa().check(con)) == []


def test_url_no_gtm_returns_empty(con):
    assert list(UrlNoGtm().check(con)) == []


def test_url_no_ga_returns_empty(con):
    assert list(UrlNoGa().check(con)) == []


# --- registry --------------------------------------------------------------

def test_all_rules_registered():
    import crawlforge.rules.url  # noqa: F401

    expected = {
        "url_multiple_slashes",
        "url_contains_space",
        "broken_bookmark",
        "url_non_ascii",
        "url_uppercase",
        "url_repetitive_path",
        "url_internal_search",
        "url_has_parameters",
        "url_ga_tracking_params",
        "url_session_id_params",
        "url_repetitive_query_params",
        "url_underscores",
        "url_over_115_chars",
        "url_multiple_gtm",
        "url_multiple_ga",
        "url_no_gtm",
        "url_no_ga",
        "query_more_than_three_params",
        "query_paginated_params",
        "query_search_filter_params",
        "query_sort_params",
    }
    for rid in expected:
        assert rid in REGISTRY, f"{rid} not registered"
