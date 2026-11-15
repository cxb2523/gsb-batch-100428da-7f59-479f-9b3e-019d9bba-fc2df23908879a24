"""Smoke tests para reglas de Page Titles."""

import duckdb
import pytest

from crawlforge.rules._registry import REGISTRY
from crawlforge.rules.page_titles.title_duplicate import TitleDuplicate
from crawlforge.rules.page_titles.title_missing import TitleMissing
from crawlforge.rules.page_titles.title_too_long import TitleTooLong
from crawlforge.rules.page_titles.title_too_short import TitleTooShort
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    rows = [
        # url_id, url, status, ctype, title, indexable
        (1, "https://a.com/",       200, "text/html", None,                                                            True),
        (2, "https://a.com/empty",  200, "text/html", "",                                                              True),
        (3, "https://a.com/ok",     200, "text/html", "A reasonable length title for SEO purposes",                    True),
        (4, "https://a.com/dup-1",  200, "text/html", "Same Title Duplicated Across All Of These Pages",               True),
        (5, "https://a.com/dup-2",  200, "text/html", "Same Title Duplicated Across All Of These Pages",               True),
        (6, "https://a.com/long",   200, "text/html", "x" * 80,                                                        True),
        (7, "https://a.com/short",  200, "text/html", "Tiny",                                                          True),
        (8, "https://a.com/404",    404, "text/html", None,                                                            False),
    ]
    for url_id, url, status, ctype, title, idx in rows:
        con.execute(
            "INSERT INTO urls (url_id, url, status_code, content_type, title, is_indexable) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [url_id, url, status, ctype, title, idx],
        )
    return con


def test_title_missing_flags_null_and_empty(con):
    issues = list(TitleMissing().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {1, 2}


def test_title_missing_ignores_404(con):
    issues = list(TitleMissing().check(con))
    assert all(i.url_id != 8 for i in issues)


def test_title_duplicate_finds_pair(con):
    issues = list(TitleDuplicate().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {4, 5}
    for issue in issues:
        assert issue.evidence["duplicate_count"] == 2


def test_title_too_long_flags_over_60(con):
    issues = list(TitleTooLong().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {6}


def test_title_too_short_flags_under_30(con):
    issues = list(TitleTooShort().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {7}


def test_all_four_rules_registered():
    # Forces module import via the package __init__
    import crawlforge.rules.page_titles  # noqa: F401

    for rid in ["title_missing", "title_duplicate", "title_too_long", "title_too_short"]:
        assert rid in REGISTRY, f"{rid} not registered"
