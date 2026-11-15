import pytest

from crawlforge.rules.headings.h1_missing import H1Missing
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    rows = [
        # url_id, url, status, ctype, h1
        (1, "https://a.com/null",    200, "text/html", None),
        (2, "https://a.com/empty",   200, "text/html", []),
        (3, "https://a.com/blank",   200, "text/html", ["", "   "]),
        (4, "https://a.com/ok",      200, "text/html", ["Real heading"]),
        (5, "https://a.com/multi",   200, "text/html", ["", "Real second"]),
        (6, "https://a.com/404",     404, "text/html", None),
        (7, "https://a.com/img",     200, "image/png", None),
    ]
    for url_id, url, status, ctype, h1 in rows:
        con.execute(
            "INSERT INTO urls (url_id, url, status_code, content_type, h1) "
            "VALUES (?, ?, ?, ?, ?)",
            [url_id, url, status, ctype, h1],
        )
    return con


def test_flags_null_empty_and_all_blank(con):
    issues = list(H1Missing().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {1, 2, 3}


def test_does_not_flag_pages_with_real_h1(con):
    issues = list(H1Missing().check(con))
    flagged = {i.url_id for i in issues}
    assert 4 not in flagged
    assert 5 not in flagged  # has at least one non-empty entry


def test_ignores_404_and_non_html(con):
    issues = list(H1Missing().check(con))
    flagged = {i.url_id for i in issues}
    assert 6 not in flagged
    assert 7 not in flagged
