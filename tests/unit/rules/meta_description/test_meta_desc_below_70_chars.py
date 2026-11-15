import pytest

from crawlforge.rules.meta_description.meta_desc_below_70_chars import (
    MetaDescBelow70Chars,
)
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    rows = [
        (1, "https://a.com/short",   200, "text/html", "x" * 30),
        (2, "https://a.com/edge",    200, "text/html", "x" * 70),
        (3, "https://a.com/long",    200, "text/html", "x" * 120),
        (4, "https://a.com/empty",   200, "text/html", ""),
        (5, "https://a.com/null",    200, "text/html", None),
    ]
    for url_id, url, status, ctype, md in rows:
        con.execute(
            "INSERT INTO urls (url_id, url, status_code, content_type, meta_description) "
            "VALUES (?, ?, ?, ?, ?)",
            [url_id, url, status, ctype, md],
        )
    return con


def test_flags_below_70(con):
    issues = list(MetaDescBelow70Chars().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {1}


def test_does_not_flag_edge_long_empty_or_null(con):
    issues = list(MetaDescBelow70Chars().check(con))
    flagged = {i.url_id for i in issues}
    assert 2 not in flagged  # exactly threshold
    assert 3 not in flagged
    assert 4 not in flagged  # empty handled by another rule
    assert 5 not in flagged
