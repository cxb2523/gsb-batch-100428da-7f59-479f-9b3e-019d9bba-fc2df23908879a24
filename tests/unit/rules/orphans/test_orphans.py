import pytest

import crawlforge.rules.orphans  # noqa: F401
from crawlforge.rules._registry import REGISTRY
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    # url 1 = root (depth 0), url 2 = linked, url 3 = orphan (depth>0, no inlinks),
    # url 4 = isolated via canonical, url 5 = isolated via redirect target
    urls = [
        (1, "https://a.com/",          200, 0, True,  None),
        (2, "https://a.com/about",     200, 1, True,  None),
        (3, "https://a.com/orphan",    200, 2, True,  None),
        (4, "https://a.com/canon",     200, 1, True,  None),
        (5, "https://a.com/redir-to",  200, 1, True,  None),
    ]
    for url_id, url, status, depth, indexable, canonical in urls:
        con.execute(
            "INSERT INTO urls (url_id, url, status_code, depth, is_indexable, canonical) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [url_id, url, status, depth, indexable, canonical],
        )

    # links: 1→2 only. url 3, 4, 5 don't receive inlinks.
    con.execute(
        "INSERT INTO links (source_url_id, target_url, target_url_id) VALUES (1, 'https://a.com/about', 2)"
    )
    # url 4 is referenced via canonical from another url
    con.execute(
        "UPDATE urls SET canonical='https://a.com/canon' WHERE url_id=2"
    )
    # url 5 is target of a redirect
    con.execute(
        "INSERT INTO redirects (chain_id, from_url, to_url, hop) VALUES (1, 'https://a.com/old', 'https://a.com/redir-to', 0)"
    )
    return con


def test_orphan_url_flags_isolated(con):
    issues = list(REGISTRY["orphan_url"]().check(con))
    flagged = {i.url_id for i in issues}
    # url 3 (orphan), url 4 (only via canonical), url 5 (only via redirect)
    assert 3 in flagged


def test_isolated_via_canonical_flags_url_4(con):
    issues = list(REGISTRY["isolated_via_canonical"]().check(con))
    flagged = {i.url_id for i in issues}
    assert 4 in flagged


def test_isolated_via_redirect_flags_url_5(con):
    issues = list(REGISTRY["isolated_via_redirect"]().check(con))
    flagged = {i.url_id for i in issues}
    assert 5 in flagged


def test_root_url_not_flagged_as_orphan(con):
    issues = list(REGISTRY["orphan_url"]().check(con))
    flagged = {i.url_id for i in issues}
    assert 1 not in flagged
