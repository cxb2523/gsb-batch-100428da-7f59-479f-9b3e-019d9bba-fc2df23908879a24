"""Nitter pagination behaviour — no real network.

Replaces ``NitterBackend._get_html`` (or the ``http`` layer) with fakes and
covers: first-page failure, mid-pagination failure, duplicate cursors,
limit reached, and the recorded ``last_pagination`` outcome.
"""
import pytest

from xtf.backends import nitter as nitter_mod
from xtf.backends.nitter import (
    STOP_DUPLICATE_CURSOR,
    STOP_LIMIT,
    STOP_NO_CURSOR,
    STOP_UPSTREAM_ERROR,
    NitterBackend,
)
from xtf.exceptions import RateLimited, UpstreamDown


def _page(tweets, cursor=None):
    """Build minimal Nitter HTML: [(username, tweet_id, text), ...]."""
    items = "".join(
        f'<div class="timeline-item" data-username="{u}">'
        f'<a class="tweet-link" href="/{u}/status/{tid}#m"></a>'
        f'<div class="tweet-content media-body" dir="auto">{text}</div>'
        "</div>"
        for u, tid, text in tweets
    )
    more = (
        f'<div class="show-more"><a href="?cursor={cursor}">Load more</a></div>'
        if cursor else ""
    )
    return f"<html><body>{items}{more}</body></html>"


def _make_pages(pages):
    """pages: {cursor_or_None: html or exception} — served in request order."""
    calls = []
    # list, not dict: cursor None keys plus explicit order
    for key, value in pages:
        calls.append((key, value))
    state = {"i": 0}

    def fake_get_html(path):
        assert state["i"] < len(calls), f"unexpected extra page requested: {path}"
        cursor, value = calls[state["i"]]
        state["i"] += 1
        if isinstance(value, Exception):
            raise value
        return value

    return fake_get_html, calls, state


def _backend(monkeypatch, pages, op_mode="search"):
    fake, calls, state = _make_pages(pages)
    backend = NitterBackend(instances=["http://fake"])
    monkeypatch.setattr(backend, "_get_html", fake)
    return backend, calls, state


def _tweets(page_no, count=1):
    """Numeric-ids tweets for one page: ids are page_no * 100 + n."""
    return [
        (f"u{page_no}_{n}", str(page_no * 100 + n), f"p{page_no}-{n}")
        for n in range(1, count + 1)
    ]


# ── first page failure ──────────────────────────────────────────────────
def test_first_page_failure_raises(monkeypatch):
    backend = NitterBackend(instances=["http://fake"])
    monkeypatch.setattr(backend, "_get_html", lambda path: (_ for _ in ()).throw(
        RateLimited("HTTP 429")))
    with pytest.raises(RateLimited):
        backend.search("timeout", limit=5)
    outcome = backend.last_pagination
    assert outcome is not None
    assert outcome.reason == STOP_UPSTREAM_ERROR
    assert outcome.page == 1
    assert outcome.count == 0
    assert outcome.error_code == "rate_limited"


# ── mid-pagination failure keeps earlier tweets ────────────────────────
def test_second_page_failure_returns_first_page(monkeypatch, capsys):
    backend, _, _ = _backend(monkeypatch, [
        (None, _page(_tweets(1), cursor="c1")),
        ("c1", UpstreamDown("HTTP 503")),
    ])
    tweets = backend.search("timeout", limit=5)

    assert [t.tweet_id for t in tweets] == ["101"]
    outcome = backend.last_pagination
    assert outcome.reason == STOP_UPSTREAM_ERROR
    assert outcome.error_code == "upstream_down"
    assert outcome.page == 2
    assert outcome.count == 1

    err = capsys.readouterr().err
    assert "page 2" in err
    assert "upstream_down" in err
    assert "stopped" in err


def test_third_page_failure_returns_two_pages(monkeypatch):
    backend, _, _ = _backend(monkeypatch, [
        (None, _page(_tweets(1), cursor="c1")),
        ("c1", _page(_tweets(2), cursor="c2")),
        ("c2", RateLimited("HTTP 429")),
    ])
    tweets = backend.search("timeout", limit=5)

    assert [t.tweet_id for t in tweets] == ["101", "201"]
    assert backend.last_pagination.reason == STOP_UPSTREAM_ERROR
    assert backend.last_pagination.page == 3
    assert backend.last_pagination.error_code == "rate_limited"


# ── duplicate cursor ────────────────────────────────────────────────────
def test_duplicate_cursor_stops_before_refetching(monkeypatch, capsys):
    backend, calls, state = _backend(monkeypatch, [
        (None, _page(_tweets(1), cursor="c1")),
        ("c1", _page(_tweets(2), cursor="c1")),  # repeats c1
    ])
    tweets = backend.search("timeout", limit=20)

    assert [t.tweet_id for t in tweets] == ["101", "201"]
    assert state["i"] == 2            # c1 was never requested a second time
    assert backend.last_pagination.reason == STOP_DUPLICATE_CURSOR
    assert backend.last_pagination.page == 2
    assert backend.last_pagination.count == 2

    err = capsys.readouterr().err
    assert "duplicate_cursor" in err
    assert "page 2" in err


def test_duplicate_cursor_across_three_pages(monkeypatch):
    backend, _, state = _backend(monkeypatch, [
        (None, _page(_tweets(1), cursor="c1")),
        ("c1", _page(_tweets(2), cursor="c2")),
        ("c2", _page(_tweets(3), cursor="c1")),  # back to a seen cursor
    ])
    tweets = backend.search("timeout", limit=20)

    assert [t.tweet_id for t in tweets] == ["101", "201", "301"]
    assert state["i"] == 3
    assert backend.last_pagination.reason == STOP_DUPLICATE_CURSOR
    assert backend.last_pagination.page == 3


# ── happy paths ────────────────────────────────────────────────────────
def test_limit_reached_across_pages(monkeypatch):
    backend, _, state = _backend(monkeypatch, [
        (None, _page(_tweets(1, 2), cursor="c1")),
        ("c1", _page(_tweets(2, 2), cursor="c2")),
    ])
    tweets = backend.search("timeout", limit=3)

    assert [t.tweet_id for t in tweets] == ["101", "102", "201"]
    assert state["i"] == 2
    outcome = backend.last_pagination
    assert outcome.reason == STOP_LIMIT
    assert outcome.count == 3
    assert outcome.page == 2


def test_no_more_cursor(monkeypatch):
    backend, _, _ = _backend(monkeypatch, [
        (None, _page(_tweets(1, 2), cursor=None)),
    ])
    tweets = backend.search("timeout", limit=10)

    assert [t.tweet_id for t in tweets] == ["101", "102"]
    assert backend.last_pagination.reason == STOP_NO_CURSOR
    assert backend.last_pagination.page == 1


def test_empty_page_stops(monkeypatch):
    backend, _, _ = _backend(monkeypatch, [
        (None, _page(_tweets(1), cursor="c1")),
        ("c1", _page([], cursor="c2")),
    ])
    tweets = backend.search("timeout", limit=10)

    assert [t.tweet_id for t in tweets] == ["101"]
    assert backend.last_pagination.reason == "empty_page"


# ── fetch_timeline reuses the same pagination path ─────────────────────
def test_fetch_timeline_uses_shared_pagination(monkeypatch):
    backend, calls, _ = _backend(monkeypatch, [
        (None, _page(_tweets(1), cursor="c1")),
        ("c1", _page(_tweets(2), cursor=None)),
    ])
    tweets = backend.fetch_timeline("alice", limit=5)

    assert [t.tweet_id for t in tweets] == ["101", "201"]
    assert backend.last_pagination.reason == STOP_NO_CURSOR
    # Timeline uses the search route, same as before the refactor.
    assert backend.last_pagination.limit == 5


def test_fetch_timeline_page_failure_also_partial(monkeypatch):
    backend, _, _ = _backend(monkeypatch, [
        (None, _page(_tweets(1), cursor="c1")),
        ("c1", UpstreamDown("HTTP 502")),
    ])
    tweets = backend.fetch_timeline("alice", limit=5)

    assert [t.tweet_id for t in tweets] == ["101"]
    assert backend.last_pagination.reason == STOP_UPSTREAM_ERROR
    assert backend.last_pagination.error_code == "upstream_down"


# ── fetch_list shares the same flow ────────────────────────────────────
def test_fetch_list_paginates_and_dedupes_cursor(monkeypatch):
    backend, _, state = _backend(monkeypatch, [
        (None, _page(_tweets(1), cursor="c1")),
        ("c1", _page(_tweets(2), cursor="c1")),
    ])
    tweets = backend.fetch_list("123", limit=10)

    assert [t.tweet_id for t in tweets] == ["101", "201"]
    assert state["i"] == 2
    assert backend.last_pagination.reason == STOP_DUPLICATE_CURSOR


def test_fetch_list_second_page_failure_partial(monkeypatch):
    backend, _, _ = _backend(monkeypatch, [
        (None, _page(_tweets(1), cursor="c1")),
        ("c1", RateLimited("HTTP 403")),
    ])
    tweets = backend.fetch_list("123", limit=10)

    assert [t.tweet_id for t in tweets] == ["101"]
    assert backend.last_pagination.reason == STOP_UPSTREAM_ERROR
    assert backend.last_pagination.error_code == "rate_limited"


# ── fake http layer (exercises _get_html failover path too) ────────────
def test_failure_via_fake_http_layer(monkeypatch, capsys):
    backend = NitterBackend(instances=["http://inst"])
    monkeypatch.setattr(nitter_mod.http, "probe", lambda url, timeout=3: True)

    def fake_get_text(url, headers=None, timeout=15, retries=0):
        if "cursor=bad" in url:
            raise UpstreamDown("HTTP 503 — " + url)
        return _page(_tweets(1), cursor="bad")

    monkeypatch.setattr(nitter_mod.http, "get_text", fake_get_text)
    tweets = backend.search("timeout", limit=5)

    assert [t.tweet_id for t in tweets] == ["101"]
    # _get_html wraps per-instance failures; a lone instance yields
    # backend_unavailable. Both are XtfError subclasses, so page 2 still
    # stops gracefully and the code is reported.
    outcome = backend.last_pagination
    assert outcome.reason == STOP_UPSTREAM_ERROR
    assert outcome.page == 2
    assert outcome.error_code == "backend_unavailable"
    err = capsys.readouterr().err
    assert "page 2" in err
    assert "backend_unavailable" in err


def test_second_instance_succeeds_after_first_fails(monkeypatch):
    """Failover happens inside _get_html; pagination still records the page."""
    backend = NitterBackend(instances=["http://bad", "http://good"])
    monkeypatch.setattr(nitter_mod.http, "probe", lambda url, timeout=3: True)

    def fake_get_text(url, headers=None, timeout=15, retries=0):
        if url.startswith("http://bad"):
            raise UpstreamDown("HTTP 503 — " + url)
        return _page(_tweets(1), cursor=None)

    monkeypatch.setattr(nitter_mod.http, "get_text", fake_get_text)
    tweets = backend.search("timeout", limit=5)

    assert [t.tweet_id for t in tweets] == ["101"]
    assert backend.last_pagination.reason == STOP_NO_CURSOR
    assert backend.last_pagination.error_code is None
