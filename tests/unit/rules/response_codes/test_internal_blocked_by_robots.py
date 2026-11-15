import pytest

from crawlforge.rules.response_codes.internal_blocked_by_robots import (
    InternalBlockedByRobots,
)
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    rows = [
        # url_id, url, from_robots
        (1, "https://a.com/blocked", True),
        (2, "https://a.com/open", False),
    ]
    for url_id, url, blocked in rows:
        con.execute(
            "INSERT INTO urls (url_id, url, from_robots) VALUES (?, ?, ?)",
            [url_id, url, blocked],
        )
    return con


def test_flags_blocked_url(con):
    issues = list(InternalBlockedByRobots().check(con))
    assert {i.url_id for i in issues} == {1}


def test_does_not_flag_open_url(con):
    issues = list(InternalBlockedByRobots().check(con))
    assert all(i.url_id != 2 for i in issues)
