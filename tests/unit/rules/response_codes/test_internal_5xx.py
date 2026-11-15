import pytest

from crawlforge.rules.response_codes.internal_5xx import Internal5xx
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    rows = [
        (1, "https://a.com/ok", 200),
        (2, "https://a.com/500", 500),
        (3, "https://a.com/503", 503),
        (4, "https://a.com/404", 404),
    ]
    for url_id, url, status in rows:
        con.execute(
            "INSERT INTO urls (url_id, url, status_code) VALUES (?, ?, ?)",
            [url_id, url, status],
        )
    return con


def test_flags_5xx_only(con):
    issues = list(Internal5xx().check(con))
    assert {i.url_id for i in issues} == {2, 3}


def test_does_not_flag_others(con):
    issues = list(Internal5xx().check(con))
    flagged = {i.url_id for i in issues}
    assert 1 not in flagged
    assert 4 not in flagged
