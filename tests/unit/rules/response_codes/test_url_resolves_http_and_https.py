import pytest

from crawlforge.rules.response_codes.url_resolves_http_and_https import (
    UrlResolvesHttpAndHttps,
)
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    rows = [
        # Pareja: ambas 200 — debería disparar
        (1, "https://a.com/foo", 200),
        (2, "http://a.com/foo",  200),
        # HTTPS sin gemela HTTP — no dispara
        (3, "https://a.com/only-https", 200),
        # HTTPS con gemela HTTP que devuelve 301 — no dispara (HTTP no resuelve 200)
        (4, "https://a.com/redir", 200),
        (5, "http://a.com/redir",  301),
        # Pareja con HTTPS no 200 — no dispara
        (6, "https://a.com/down",  500),
        (7, "http://a.com/down",   200),
    ]
    for url_id, url, status in rows:
        con.execute(
            "INSERT INTO urls (url_id, url, status_code) VALUES (?, ?, ?)",
            [url_id, url, status],
        )
    return con


def test_flags_dual_resolve(con):
    issues = list(UrlResolvesHttpAndHttps().check(con))
    flagged = {i.url_id for i in issues}
    # Tanto la HTTPS (1) como la HTTP (2) deberían reportarse
    assert 1 in flagged
    assert 2 in flagged


def test_does_not_flag_https_only(con):
    issues = list(UrlResolvesHttpAndHttps().check(con))
    flagged = {i.url_id for i in issues}
    assert 3 not in flagged


def test_does_not_flag_when_http_redirects(con):
    issues = list(UrlResolvesHttpAndHttps().check(con))
    flagged = {i.url_id for i in issues}
    assert 4 not in flagged
    assert 5 not in flagged


def test_does_not_flag_when_https_not_200(con):
    issues = list(UrlResolvesHttpAndHttps().check(con))
    flagged = {i.url_id for i in issues}
    assert 6 not in flagged
    assert 7 not in flagged
