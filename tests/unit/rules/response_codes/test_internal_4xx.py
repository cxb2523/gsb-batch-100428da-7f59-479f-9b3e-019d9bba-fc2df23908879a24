import pytest

from crawlforge.rules.response_codes.internal_4xx import Internal4xx
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    rows = [
        (1, "https://a.com/ok", 200),
        (2, "https://a.com/404", 404),
        (3, "https://a.com/410", 410),
        (4, "https://a.com/500", 500),
        (5, "https://a.com/dead", 0),
    ]
    for url_id, url, status in rows:
        con.execute(
            "INSERT INTO urls (url_id, url, status_code) VALUES (?, ?, ?)",
            [url_id, url, status],
        )
    return con


def test_flags_4xx_only(con):
    issues = list(Internal4xx().check(con))
    assert {i.url_id for i in issues} == {2, 3}


def test_does_not_flag_5xx_or_2xx(con):
    issues = list(Internal4xx().check(con))
    flagged = {i.url_id for i in issues}
    assert 1 not in flagged
    assert 4 not in flagged
    assert 5 not in flagged
