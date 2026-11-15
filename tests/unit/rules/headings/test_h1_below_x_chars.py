import pytest

from crawlforge.rules.headings.h1_below_x_chars import H1BelowXChars
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    rows = [
        (1, "https://a.com/tiny",    200, "text/html", ["Tiny"]),                      # below 20
        (2, "https://a.com/edge",    200, "text/html", ["x" * 20]),                    # exactly 20
        (3, "https://a.com/long",    200, "text/html", ["A reasonable length heading"]),
        (4, "https://a.com/empty",   200, "text/html", []),                            # no h1
        (5, "https://a.com/blank",   200, "text/html", ["   "]),                       # blank first
        (6, "https://a.com/null",    200, "text/html", None),
    ]
    for url_id, url, status, ctype, h1 in rows:
        con.execute(
            "INSERT INTO urls (url_id, url, status_code, content_type, h1) "
            "VALUES (?, ?, ?, ?, ?)",
            [url_id, url, status, ctype, h1],
        )
    return con


def test_flags_short_first_h1(con):
    issues = list(H1BelowXChars().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {1}


def test_does_not_flag_edge_long_blank_or_missing(con):
    issues = list(H1BelowXChars().check(con))
    flagged = {i.url_id for i in issues}
    for uid in (2, 3, 4, 5, 6):
        assert uid not in flagged
