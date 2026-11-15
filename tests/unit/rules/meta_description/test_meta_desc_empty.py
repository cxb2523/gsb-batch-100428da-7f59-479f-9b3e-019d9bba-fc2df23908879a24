import pytest

from crawlforge.rules.meta_description.meta_desc_empty import MetaDescEmpty
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    rows = [
        (1, "https://a.com/null",       200, "text/html", None),
        (2, "https://a.com/empty",      200, "text/html", ""),
        (3, "https://a.com/whitespace", 200, "text/html", "   "),
        (4, "https://a.com/ok",         200, "text/html", "Real content."),
        (5, "https://a.com/404",        404, "text/html", ""),
    ]
    for url_id, url, status, ctype, md in rows:
        con.execute(
            "INSERT INTO urls (url_id, url, status_code, content_type, meta_description) "
            "VALUES (?, ?, ?, ?, ?)",
            [url_id, url, status, ctype, md],
        )
    return con


def test_flags_empty_and_whitespace(con):
    issues = list(MetaDescEmpty().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {2, 3}


def test_does_not_flag_null_or_ok(con):
    issues = list(MetaDescEmpty().check(con))
    flagged = {i.url_id for i in issues}
    assert 1 not in flagged
    assert 4 not in flagged
    assert 5 not in flagged
