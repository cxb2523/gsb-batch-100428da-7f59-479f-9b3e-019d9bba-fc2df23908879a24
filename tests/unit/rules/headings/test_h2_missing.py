import pytest

from crawlforge.rules.headings.h2_missing import H2Missing
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    rows = [
        # url_id, url, status, ctype, h2, indexable
        (1, "https://a.com/null",   200, "text/html", None,   True),
        (2, "https://a.com/empty",  200, "text/html", [],     True),
        (3, "https://a.com/blank",  200, "text/html", ["", "   "], True),
        (4, "https://a.com/ok",     200, "text/html", ["A section"], True),
        (5, "https://a.com/noidx",  200, "text/html", None,   False),  # ignore non-indexable
        (6, "https://a.com/404",    404, "text/html", None,   True),
        (7, "https://a.com/img",    200, "image/png", None,   True),
    ]
    for url_id, url, status, ctype, h2, idx in rows:
        con.execute(
            "INSERT INTO urls (url_id, url, status_code, content_type, h2, is_indexable) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [url_id, url, status, ctype, h2, idx],
        )
    return con


def test_flags_missing_h2_on_indexable_html(con):
    issues = list(H2Missing().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {1, 2, 3}


def test_does_not_flag_pages_with_h2_or_non_indexable(con):
    issues = list(H2Missing().check(con))
    flagged = {i.url_id for i in issues}
    for uid in (4, 5, 6, 7):
        assert uid not in flagged
