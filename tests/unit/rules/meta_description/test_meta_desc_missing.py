import pytest

from crawlforge.rules.meta_description.meta_desc_missing import MetaDescMissing
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    rows = [
        # url_id, url, status, ctype, meta_description
        (1, "https://a.com/null",  200, "text/html", None),                 # missing
        (2, "https://a.com/empty", 200, "text/html", ""),                   # empty (other rule)
        (3, "https://a.com/ok",    200, "text/html", "A nice meta desc."),  # ok
        (4, "https://a.com/404",   404, "text/html", None),                 # ignore (not 200)
        (5, "https://a.com/img",   200, "image/png", None),                 # ignore (not html)
    ]
    for url_id, url, status, ctype, md in rows:
        con.execute(
            "INSERT INTO urls (url_id, url, status_code, content_type, meta_description) "
            "VALUES (?, ?, ?, ?, ?)",
            [url_id, url, status, ctype, md],
        )
    return con


def test_flags_null_meta_description(con):
    issues = list(MetaDescMissing().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {1}


def test_does_not_flag_empty_404_or_non_html(con):
    issues = list(MetaDescMissing().check(con))
    flagged = {i.url_id for i in issues}
    assert 2 not in flagged
    assert 3 not in flagged
    assert 4 not in flagged
    assert 5 not in flagged
