import pytest

from crawlforge.rules.response_codes.internal_redirect_3xx import InternalRedirect3xx
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    rows = [
        # url_id, url, status, redirect_count
        (1, "https://a.com/direct", 200, 0),
        (2, "https://a.com/redir1", 200, 1),
        (3, "https://a.com/redir3", 200, 3),
    ]
    for url_id, url, status, rc in rows:
        con.execute(
            "INSERT INTO urls (url_id, url, status_code, redirect_count) "
            "VALUES (?, ?, ?, ?)",
            [url_id, url, status, rc],
        )
    return con


def test_flags_redirected_urls(con):
    issues = list(InternalRedirect3xx().check(con))
    assert {i.url_id for i in issues} == {2, 3}


def test_does_not_flag_direct(con):
    issues = list(InternalRedirect3xx().check(con))
    assert all(i.url_id != 1 for i in issues)
