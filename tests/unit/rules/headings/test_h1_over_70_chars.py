import pytest

from crawlforge.rules.headings.h1_over_70_chars import H1Over70Chars
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    rows = [
        (1, "https://a.com/short",   200, "text/html", ["Short heading"]),
        (2, "https://a.com/edge",    200, "text/html", ["x" * 70]),
        (3, "https://a.com/long",    200, "text/html", ["x" * 80]),
        (4, "https://a.com/mixed",   200, "text/html", ["short", "x" * 90]),
        (5, "https://a.com/empty",   200, "text/html", []),
        (6, "https://a.com/null",    200, "text/html", None),
    ]
    for url_id, url, status, ctype, h1 in rows:
        con.execute(
            "INSERT INTO urls (url_id, url, status_code, content_type, h1) "
            "VALUES (?, ?, ?, ?, ?)",
            [url_id, url, status, ctype, h1],
        )
    return con


def test_flags_h1_over_threshold(con):
    issues = list(H1Over70Chars().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {3, 4}


def test_does_not_flag_short_or_edge(con):
    issues = list(H1Over70Chars().check(con))
    flagged = {i.url_id for i in issues}
    for uid in (1, 2, 5, 6):
        assert uid not in flagged
