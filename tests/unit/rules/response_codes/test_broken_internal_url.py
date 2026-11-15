import pytest

from crawlforge.rules.response_codes.broken_internal_url import BrokenInternalUrl
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    rows = [
        # url_id, url, status, is_indexable
        (1, "https://a.com/ok", 200, True),
        (2, "https://a.com/404-no-idx", 404, False),
        (3, "https://a.com/500-no-idx", 500, False),
        (4, "https://a.com/dead-no-idx", 0, False),
        # 404 pero marcada como indexable por error: no debería pasar pero si pasa, no la flageamos
        (5, "https://a.com/404-idx", 404, True),
    ]
    for url_id, url, status, idx in rows:
        con.execute(
            "INSERT INTO urls (url_id, url, status_code, is_indexable) VALUES (?, ?, ?, ?)",
            [url_id, url, status, idx],
        )
    return con


def test_flags_broken_non_indexable(con):
    issues = list(BrokenInternalUrl().check(con))
    assert {i.url_id for i in issues} == {2, 3, 4}


def test_does_not_flag_200(con):
    issues = list(BrokenInternalUrl().check(con))
    assert all(i.url_id != 1 for i in issues)


def test_does_not_flag_404_indexable(con):
    """Si está marcada como indexable, no encaja con la regla."""
    issues = list(BrokenInternalUrl().check(con))
    assert all(i.url_id != 5 for i in issues)
