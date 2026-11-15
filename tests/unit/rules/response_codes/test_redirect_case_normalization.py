import pytest

from crawlforge.rules.response_codes.redirect_case_normalization import (
    RedirectCaseNormalization,
)
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    con.execute(
        "INSERT INTO urls (url_id, url, status_code) VALUES (?, ?, ?)",
        [1, "https://a.com/Foo", 301],
    )
    con.execute(
        "INSERT INTO urls (url_id, url, status_code) VALUES (?, ?, ?)",
        [2, "https://a.com/baz", 301],
    )
    # Solo difiere en case
    con.execute(
        "INSERT INTO redirects (chain_id, from_url, to_url, status_code, hop, is_loop)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [10, "https://a.com/Foo", "https://a.com/foo", 301, 0, False],
    )
    # Diferencia distinta a case
    con.execute(
        "INSERT INTO redirects (chain_id, from_url, to_url, status_code, hop, is_loop)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [11, "https://a.com/baz", "https://a.com/qux", 301, 0, False],
    )
    return con


def test_flags_case_redirect(con):
    issues = list(RedirectCaseNormalization().check(con))
    flagged_urls = {i.evidence["url"] for i in issues}
    assert "https://a.com/Foo" in flagged_urls


def test_does_not_flag_path_change(con):
    issues = list(RedirectCaseNormalization().check(con))
    flagged_urls = {i.evidence["url"] for i in issues}
    assert "https://a.com/baz" not in flagged_urls
