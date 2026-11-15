import pytest

from crawlforge.rules.response_codes.internal_redirect_chain import InternalRedirectChain
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    con.execute(
        "INSERT INTO urls (url_id, url, status_code) VALUES (?, ?, ?)",
        [1, "https://a.com/A", 301],
    )
    con.execute(
        "INSERT INTO urls (url_id, url, status_code) VALUES (?, ?, ?)",
        [2, "https://a.com/single", 301],
    )
    # Cadena larga: A -> B -> C
    con.execute(
        "INSERT INTO redirects (chain_id, from_url, to_url, status_code, hop, is_loop)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [100, "https://a.com/A", "https://a.com/B", 301, 0, False],
    )
    con.execute(
        "INSERT INTO redirects (chain_id, from_url, to_url, status_code, hop, is_loop)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [100, "https://a.com/B", "https://a.com/C", 301, 1, False],
    )
    # Single hop: single -> final (hop=0 only)
    con.execute(
        "INSERT INTO redirects (chain_id, from_url, to_url, status_code, hop, is_loop)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [200, "https://a.com/single", "https://a.com/final", 301, 0, False],
    )
    return con


def test_flags_chain(con):
    issues = list(InternalRedirectChain().check(con))
    flagged_urls = {i.evidence["url"] for i in issues}
    assert "https://a.com/A" in flagged_urls
    # confirma que reporta los hops totales
    a_issue = next(i for i in issues if i.evidence["url"] == "https://a.com/A")
    assert a_issue.evidence["hops"] == 2


def test_does_not_flag_single_hop(con):
    issues = list(InternalRedirectChain().check(con))
    flagged_urls = {i.evidence["url"] for i in issues}
    assert "https://a.com/single" not in flagged_urls
