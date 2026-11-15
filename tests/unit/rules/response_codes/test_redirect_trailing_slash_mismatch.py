import pytest

from crawlforge.rules.response_codes.redirect_trailing_slash_mismatch import (
    RedirectTrailingSlashMismatch,
)
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    con.execute(
        "INSERT INTO urls (url_id, url, status_code) VALUES (?, ?, ?)",
        [1, "https://a.com/foo", 301],
    )
    con.execute(
        "INSERT INTO urls (url_id, url, status_code) VALUES (?, ?, ?)",
        [2, "https://a.com/bar", 301],
    )
    # Solo difiere en trailing slash
    con.execute(
        "INSERT INTO redirects (chain_id, from_url, to_url, status_code, hop, is_loop)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [10, "https://a.com/foo", "https://a.com/foo/", 301, 0, False],
    )
    # Diferencia distinta
    con.execute(
        "INSERT INTO redirects (chain_id, from_url, to_url, status_code, hop, is_loop)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [11, "https://a.com/bar", "https://a.com/baz", 301, 0, False],
    )
    return con


def test_flags_trailing_slash_redirect(con):
    issues = list(RedirectTrailingSlashMismatch().check(con))
    flagged_urls = {i.evidence["url"] for i in issues}
    assert "https://a.com/foo" in flagged_urls


def test_does_not_flag_other_redirects(con):
    issues = list(RedirectTrailingSlashMismatch().check(con))
    flagged_urls = {i.evidence["url"] for i in issues}
    assert "https://a.com/bar" not in flagged_urls
