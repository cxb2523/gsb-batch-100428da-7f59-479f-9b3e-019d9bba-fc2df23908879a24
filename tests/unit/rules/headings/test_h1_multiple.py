import pytest

from crawlforge.rules.headings.h1_multiple import H1Multiple
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    rows = [
        (1, "https://a.com/single", 200, "text/html", ["Only one"]),
        (2, "https://a.com/two",    200, "text/html", ["First", "Second"]),
        (3, "https://a.com/three",  200, "text/html", ["A", "B", "C"]),
        (4, "https://a.com/empty",  200, "text/html", []),
        (5, "https://a.com/null",   200, "text/html", None),
    ]
    for url_id, url, status, ctype, h1 in rows:
        con.execute(
            "INSERT INTO urls (url_id, url, status_code, content_type, h1) "
            "VALUES (?, ?, ?, ?, ?)",
            [url_id, url, status, ctype, h1],
        )
    return con


def test_flags_pages_with_multiple_h1(con):
    issues = list(H1Multiple().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {2, 3}
    counts = {i.url_id: i.evidence["h1_count"] for i in issues}
    assert counts == {2: 2, 3: 3}


def test_does_not_flag_single_or_zero_h1(con):
    issues = list(H1Multiple().check(con))
    flagged = {i.url_id for i in issues}
    for uid in (1, 4, 5):
        assert uid not in flagged
