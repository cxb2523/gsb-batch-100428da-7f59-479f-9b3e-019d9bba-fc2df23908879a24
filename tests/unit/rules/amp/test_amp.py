import pytest

import crawlforge.rules.amp  # noqa: F401
from crawlforge.rules._registry import REGISTRY
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    rows = [
        # url_id, url, status, ctype, indexable, robots, meta_robots
        (1, "https://a.com/article/amp/",      404, "text/html", False, False, None),
        (2, "https://a.com/post.amp.html",     503, "text/html", False, False, None),
        (3, "https://a.com/news?amp=1",        403, "text/html", False, False, None),
        (4, "https://a.com/x/amp/",            200, "text/html", False, True,  None),
        (5, "https://a.com/y/amp/",            200, "text/html", False, False, "noindex"),
        (6, "https://a.com/non-amp/",          200, "text/html", True,  False, None),
    ]
    for r in rows:
        con.execute(
            "INSERT INTO urls (url_id, url, status_code, content_type, "
            "is_indexable, from_robots, meta_robots) VALUES (?, ?, ?, ?, ?, ?, ?)",
            list(r),
        )
    return con


def test_amp_url_4xx_flags_404(con):
    issues = list(REGISTRY["amp_url_4xx"]().check(con))
    assert {i.url_id for i in issues} == {1}


def test_amp_url_5xx_flags_503(con):
    issues = list(REGISTRY["amp_url_5xx"]().check(con))
    assert {i.url_id for i in issues} == {2}


def test_amp_url_403_flags_403(con):
    issues = list(REGISTRY["amp_url_403"]().check(con))
    assert {i.url_id for i in issues} == {3}


def test_amp_url_disallowed_flags_robots_blocked(con):
    issues = list(REGISTRY["amp_url_disallowed"]().check(con))
    assert {i.url_id for i in issues} == {4}


def test_amp_url_noindex_flags_noindex(con):
    issues = list(REGISTRY["amp_url_noindex"]().check(con))
    assert {i.url_id for i in issues} == {5}


def test_non_amp_urls_not_flagged(con):
    """url 6 is non-AMP and should not appear in any AMP rule."""
    for rid in ["amp_url_4xx", "amp_url_5xx", "amp_url_403", "amp_url_disallowed", "amp_url_noindex"]:
        issues = list(REGISTRY[rid]().check(con))
        assert all(i.url_id != 6 for i in issues), f"{rid} flagged non-AMP url 6"
