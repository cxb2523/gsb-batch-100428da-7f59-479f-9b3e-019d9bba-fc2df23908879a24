import pytest

from crawlforge.rules.headings.h2_multiple import H2Multiple
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    rows = [
        (1, "https://a.com/zero",  200, "text/html", []),
        (2, "https://a.com/one",   200, "text/html", ["Only one"]),
        (3, "https://a.com/many",  200, "text/html", ["A", "B", "C"]),
        (4, "https://a.com/null",  200, "text/html", None),
    ]
    for url_id, url, status, ctype, h2 in rows:
        con.execute(
            "INSERT INTO urls (url_id, url, status_code, content_type, h2) "
            "VALUES (?, ?, ?, ?, ?)",
            [url_id, url, status, ctype, h2],
        )
    return con


def test_flags_pages_with_multiple_h2(con):
    issues = list(H2Multiple().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {3}


def test_does_not_flag_zero_or_one(con):
    issues = list(H2Multiple().check(con))
    flagged = {i.url_id for i in issues}
    for uid in (1, 2, 4):
        assert uid not in flagged
