"""Offline tests for the stale-quote REST fallback (no real network).

WebSocket 断流通过把 ``_last_update`` 回拨到阈值之前来模拟；REST /book
响应通过替换 ``ClobFeed.fetch_book`` 的本地样例来模拟。
"""

import asyncio
import time

from pmanalysis.feeds.clob import (
    FALLBACK_FAILED,
    FALLBACK_RECOVERED,
    FALLBACK_UNCHANGED,
    ClobFeed,
)

STALE_SEC = 8.0


def _feed_with_rest(books: dict, errors: set[str] = ()):
    """A ClobFeed whose REST fetch_book serves local sample books."""
    feed = ClobFeed()
    calls: list[str] = []

    async def fake_fetch(token_id: str) -> dict:
        calls.append(token_id)
        if token_id in errors:
            raise RuntimeError(f"REST boom for {token_id}")
        return books[token_id]

    feed.fetch_book = fake_fetch
    return feed, calls


def _make_stale(feed: ClobFeed, token_id: str) -> None:
    """Simulate a dead WS feed: last confirmed quote is beyond the threshold."""
    feed._last_update[token_id] = time.monotonic() - (STALE_SEC + 5.0)


def test_stale_token_with_changed_price_recovers():
    feed, calls = _feed_with_rest({
        "tok": {"best_ask": 55.0, "best_bid": 45.0, "raw": {}},
    })
    # WS pushed a quote once, then went silent past CLOB_STALE_SEC.
    feed.apply_quote("tok", 52.0, 48.0)
    _make_stale(feed, "tok")

    outcomes = asyncio.run(feed.refresh_stale_quotes(["tok"], STALE_SEC))

    assert calls == ["tok"]
    assert outcomes == {"tok": FALLBACK_RECOVERED}
    assert feed.fallback_outcomes["tok"] == FALLBACK_RECOVERED
    assert feed.get_quote("tok") == (55.0, 45.0)
    # Recovered quote is fresh again, so it is no longer stale.
    assert feed.stale_tokens(["tok"], STALE_SEC) == []


def test_stale_token_with_identical_price_is_not_recovered():
    feed, calls = _feed_with_rest({
        "tok": {"best_ask": 52.0, "best_bid": 48.0, "raw": {}},
    })
    feed.apply_quote("tok", 52.0, 48.0)
    _make_stale(feed, "tok")

    outcomes = asyncio.run(feed.refresh_stale_quotes(["tok"], STALE_SEC))

    assert calls == ["tok"]
    assert outcomes == {"tok": FALLBACK_UNCHANGED}
    assert feed.fallback_outcomes["tok"] == FALLBACK_UNCHANGED
    assert feed.get_quote("tok") == (52.0, 48.0)
    # A successful pull still confirms the quote, pausing further fallback.
    assert feed.stale_tokens(["tok"], STALE_SEC) == []


def test_rest_exception_marks_failure_and_batch_continues():
    feed, calls = _feed_with_rest(
        {
            "ok": {"best_ask": 61.0, "best_bid": 59.0, "raw": {}},
            "same": {"best_ask": 30.0, "best_bid": 20.0, "raw": {}},
        },
        errors={"bad"},
    )
    feed.apply_quote("ok", 50.0, 40.0)
    feed.apply_quote("same", 30.0, 20.0)
    feed.apply_quote("bad", 10.0, 5.0)
    for tok in ("ok", "same", "bad"):
        _make_stale(feed, tok)

    outcomes = asyncio.run(feed.refresh_stale_quotes(["ok", "bad", "same"], STALE_SEC))

    assert sorted(calls) == ["bad", "ok", "same"]
    assert outcomes["bad"] == FALLBACK_FAILED
    assert outcomes["ok"] == FALLBACK_RECOVERED
    assert outcomes["same"] == FALLBACK_UNCHANGED
    # Failure is not a silent success: cache untouched, still stale for retry.
    assert feed.get_quote("bad") == (10.0, 5.0)
    assert feed.stale_tokens(["bad"], STALE_SEC) == ["bad"]
    # The other tokens were still processed despite the exception.
    assert feed.get_quote("ok") == (61.0, 59.0)


def test_fresh_ws_tokens_are_not_refetched():
    feed, calls = _feed_with_rest({
        "fresh": {"best_ask": 99.0, "best_bid": 1.0, "raw": {}},
        "stale": {"best_ask": 60.0, "best_bid": 40.0, "raw": {}},
    })
    feed.apply_quote("fresh", 52.0, 48.0)  # healthy WS flow, just now
    feed.apply_quote("stale", 52.0, 48.0)
    _make_stale(feed, "stale")

    outcomes = asyncio.run(feed.refresh_stale_quotes(["fresh", "stale"], STALE_SEC))

    assert calls == ["stale"]
    assert outcomes == {"stale": FALLBACK_RECOVERED}
    assert feed.get_quote("fresh") == (52.0, 48.0)


def test_fallback_keeps_partial_merge_semantics():
    # REST book with only an ask side: bid must survive from the cache.
    feed, _ = _feed_with_rest({
        "tok": {"best_ask": 55.0, "best_bid": 0.0, "raw": {}},
    })
    feed.apply_quote("tok", 52.0, 48.0)
    _make_stale(feed, "tok")

    outcomes = asyncio.run(feed.refresh_stale_quotes(["tok"], STALE_SEC))

    assert outcomes == {"tok": FALLBACK_RECOVERED}
    assert feed.get_quote("tok") == (55.0, 48.0)


def test_prune_quotes_stops_fallback_for_unsubscribed_tokens():
    feed, calls = _feed_with_rest({
        "gone": {"best_ask": 70.0, "best_bid": 60.0, "raw": {}},
    })
    feed.apply_quote("gone", 52.0, 48.0)
    _make_stale(feed, "gone")
    outcomes = asyncio.run(feed.refresh_stale_quotes(["gone"], STALE_SEC))
    assert outcomes == {"gone": FALLBACK_RECOVERED}

    # Unsubscribe + prune: no cached state may survive for the token.
    feed.prune_quotes([])
    assert "gone" not in feed.fallback_outcomes

    # The poll loop only ever passes subscribed token ids; a pruned token is
    # never among them, so no REST call is issued for it.
    calls.clear()
    outcomes = asyncio.run(feed.refresh_stale_quotes([], STALE_SEC))
    assert calls == []
    assert outcomes == {}
