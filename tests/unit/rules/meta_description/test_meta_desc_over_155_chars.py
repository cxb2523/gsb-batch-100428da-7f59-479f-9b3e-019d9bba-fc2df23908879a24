import pytest

from crawlforge.rules.meta_description.meta_desc_over_155_chars import (
    MetaDescOver155Chars,
)
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    rows = [
        (1, "https://a.com/short",   200, "text/html", "x" * 50),
        (2, "https://a.com/edge",    200, "text/html", "x" * 155),
        (3, "https://a.com/over",    200, "text/html", "x" * 200),
        (4, "https://a.com/null",    200, "text/html", None),
    ]
    for url_id, url, status, ctype, md in rows:
        con.execute(
            "INSERT INTO urls (url_id, url, status_code, content_type, meta_description) "
            "VALUES (?, ?, ?, ?, ?)",
            [url_id, url, status, ctype, md],
        )
    return con


def test_flags_over_155(con):
    issues = list(MetaDescOver155Chars().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {3}


def test_does_not_flag_short_or_exactly_155(con):
    issues = list(MetaDescOver155Chars().check(con))
    flagged = {i.url_id for i in issues}
    assert 1 not in flagged
    assert 2 not in flagged  # boundary
    assert 4 not in flagged
