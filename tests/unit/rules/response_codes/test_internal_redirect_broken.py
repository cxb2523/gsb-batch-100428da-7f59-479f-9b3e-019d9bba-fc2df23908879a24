import pytest

from crawlforge.rules.response_codes.internal_redirect_broken import (
    InternalRedirectBroken,
)
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    # Source URLs
    con.execute(
        "INSERT INTO urls (url_id, url, status_code) VALUES (?, ?, ?)",
        [1, "https://a.com/redir-to-404", 301],
    )
    con.execute(
        "INSERT INTO urls (url_id, url, status_code) VALUES (?, ?, ?)",
        [2, "https://a.com/redir-to-200", 301],
    )
    # Target URLs
    con.execute(
        "INSERT INTO urls (url_id, url, status_code) VALUES (?, ?, ?)",
        [3, "https://a.com/dead", 404],
    )
    con.execute(
        "INSERT INTO urls (url_id, url, status_code) VALUES (?, ?, ?)",
        [4, "https://a.com/alive", 200],
    )
    # Redirects
    con.execute(
        "INSERT INTO redirects (chain_id, from_url, to_url, status_code, hop, is_loop)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [10, "https://a.com/redir-to-404", "https://a.com/dead", 301, 0, False],
    )
    con.execute(
        "INSERT INTO redirects (chain_id, from_url, to_url, status_code, hop, is_loop)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [11, "https://a.com/redir-to-200", "https://a.com/alive", 301, 0, False],
    )
    return con


def test_flags_redirect_to_4xx(con):
    issues = list(InternalRedirectBroken().check(con))
    flagged_urls = {i.evidence["url"] for i in issues}
    assert "https://a.com/redir-to-404" in flagged_urls


def test_does_not_flag_redirect_to_200(con):
    issues = list(InternalRedirectBroken().check(con))
    flagged_urls = {i.evidence["url"] for i in issues}
    assert "https://a.com/redir-to-200" not in flagged_urls
