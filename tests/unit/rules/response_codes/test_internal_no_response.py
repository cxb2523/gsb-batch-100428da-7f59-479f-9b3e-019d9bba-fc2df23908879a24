import pytest

from crawlforge.rules.response_codes.internal_no_response import InternalNoResponse
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    rows = [
        # url_id, url, status_code
        (1, "https://a.com/dead", 0),
        (2, "https://a.com/null", None),
        (3, "https://a.com/ok", 200),
        (4, "https://a.com/notfound", 404),
    ]
    for url_id, url, status in rows:
        con.execute(
            "INSERT INTO urls (url_id, url, status_code) VALUES (?, ?, ?)",
            [url_id, url, status],
        )
    return con


def test_flags_zero_and_null(con):
    issues = list(InternalNoResponse().check(con))
    assert {i.url_id for i in issues} == {1, 2}


def test_does_not_flag_2xx_or_4xx(con):
    issues = list(InternalNoResponse().check(con))
    flagged = {i.url_id for i in issues}
    assert 3 not in flagged
    assert 4 not in flagged
