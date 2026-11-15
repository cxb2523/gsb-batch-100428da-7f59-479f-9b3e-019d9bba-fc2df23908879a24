import pytest

from crawlforge.rules.response_codes.internal_redirect_loop import InternalRedirectLoop
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    # URL fila para hacer JOIN
    con.execute(
        "INSERT INTO urls (url_id, url, status_code) VALUES (?, ?, ?)",
        [1, "https://a.com/loop", 301],
    )
    con.execute(
        "INSERT INTO urls (url_id, url, status_code) VALUES (?, ?, ?)",
        [2, "https://a.com/normal", 301],
    )
    # Redirección normal sin loop
    con.execute(
        "INSERT INTO redirects (chain_id, from_url, to_url, status_code, hop, is_loop)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [100, "https://a.com/normal", "https://a.com/final", 301, 0, False],
    )
    # Redirección con loop
    con.execute(
        "INSERT INTO redirects (chain_id, from_url, to_url, status_code, hop, is_loop)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [200, "https://a.com/loop", "https://a.com/loop2", 301, 0, True],
    )
    con.execute(
        "INSERT INTO redirects (chain_id, from_url, to_url, status_code, hop, is_loop)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [200, "https://a.com/loop2", "https://a.com/loop", 301, 1, True],
    )
    return con


def test_flags_loops(con):
    issues = list(InternalRedirectLoop().check(con))
    flagged_urls = {i.evidence["url"] for i in issues}
    assert "https://a.com/loop" in flagged_urls
    assert "https://a.com/loop2" in flagged_urls


def test_does_not_flag_non_loop(con):
    issues = list(InternalRedirectLoop().check(con))
    flagged_urls = {i.evidence["url"] for i in issues}
    assert "https://a.com/normal" not in flagged_urls
