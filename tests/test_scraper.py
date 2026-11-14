"""Coverage/truncation contract for the twscrape-backed scraping layer.

No network and no real X: every scenario is a local fake async generator
(normal end, slow-over-budget, rate-limit raise, silent rate-limit stop) plus
an in-memory fake of the slice of twscrape's AccountsPool the Scraper reads.

Run: python -m pytest tests/test_scraper.py -q
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from spectre.errors import XError  # noqa: E402
from spectre.scraper import SearchResults, Scraper  # noqa: E402
from twscrape.accounts_pool import NoAccountError  # noqa: E402


# ── fakes ───────────────────────────────────────────────────────────


class FakePool:
    """Mimics the AccountsPool surface Scraper touches: get_all() + lock rows."""

    def __init__(self, locks: dict | None = None, accounts: int = 1):
        self._locks = locks or {}
        self._accounts = [
            SimpleNamespace(
                username=f"acc{i}",
                active=True,
                error_msg=None,
                last_used=None,
                locks=dict(self._locks),
            )
            for i in range(accounts)
        ]

    async def get_all(self):
        return list(self._accounts)


def tweet_item(idx: int) -> SimpleNamespace:
    # Only the attributes tweet_dict() reads are needed.
    return SimpleNamespace(
        id=1000 + idx,
        url=f"https://x.com/u/status/{1000 + idx}",
        rawContent=f"tweet {idx}",
        user=SimpleNamespace(id=7, username="u", displayname="U"),
        date=None,
        lang="en",
        replyCount=0,
        retweetCount=0,
        likeCount=0,
        quoteCount=0,
        viewCount=0,
        bookmarkedCount=0,
        media=None,
        quotedTweet=None,
        retweetedTweet=None,
        links=[],
        hashtags=[],
        mentionedUsers=[],
        inReplyToTweetId=None,
        conversationId=1000 + idx,
    )


def user_item(idx: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=2000 + idx,
        username=f"u{idx}",
        displayname=f"User {idx}",
        rawDescription="bio",
        location=None,
        url=None,
        descriptionLinks=None,
        followersCount=100,
        friendsCount=50,
        statusesCount=10,
        favouritesCount=3,
        mediaCount=0,
        verified=False,
        blue=False,
        profileImageUrl=None,
        profileBannerUrl=None,
        created=None,
        protected=False,
    )


async def finite_gen(n: int, item=tweet_item):
    """A feed that ends naturally after `n` items."""
    for i in range(n):
        yield item(i)


async def limit_overflow_gen(n: int, item=tweet_item):
    """A feed that holds more items than the caller's limit."""
    for i in range(n):
        yield item(i)


async def slow_gen(delay: float, n: int = 10, item=tweet_item):
    """Each item takes `delay`; a budget smaller than delay stops it early."""
    for i in range(n):
        await asyncio.sleep(delay)
        yield item(i)


async def raising_gen(exc: BaseException, lead_items: int = 0, item=tweet_item):
    """Yields `lead_items` items, then raises (twscrape failure mid-walk)."""
    for i in range(lead_items):
        yield item(i)
    raise exc


async def empty_gen():
    """A feed that stops immediately — twscrape's silent rate-limit shape."""
    if False:  # make this an async generator
        yield  # pragma: no cover


class FakeAPI:
    """Records calls and serves generator factories chosen per test."""

    def __init__(self, factory, *, user=None):
        self._factory = factory
        self._user = user or SimpleNamespace(id=42, username="u", followersCount=500, friendsCount=250)
        self.calls: list[tuple] = []

    def _gen(self, name, *args, **kwargs):
        self.calls.append((name, kwargs))
        return self._factory()

    def search(self, q, limit=-1, kv=None):
        return self._gen("search", query=q, limit=limit, kv=kv)

    def search_user(self, q, limit=-1, kv=None):
        return self._gen("search_user", query=q, limit=limit)

    def user_tweets(self, uid, limit=-1):
        return self._gen("user_tweets", uid=uid, limit=limit)

    def user_tweets_and_replies(self, uid, limit=-1):
        return self._gen("user_tweets_and_replies", uid=uid, limit=limit)

    def user_media(self, uid, limit=-1):
        return self._gen("user_media", uid=uid, limit=limit)

    def followers(self, uid, limit=-1):
        return self._gen("followers", uid=uid, limit=limit)

    def following(self, uid, limit=-1):
        return self._gen("following", uid=uid, limit=limit)

    def verified_followers(self, uid, limit=-1):
        return self._gen("verified_followers", uid=uid, limit=limit)

    def tweet_replies(self, tid, limit=-1):
        return self._gen("tweet_replies", tid=tid, limit=limit)

    def tweet_thread(self, tid, limit=-1):
        return self._gen("tweet_thread", tid=tid, limit=limit)

    def retweeters(self, tid, limit=-1):
        return self._gen("retweeters", tid=tid, limit=limit)

    def list_timeline(self, lid, limit=-1):
        return self._gen("list_timeline", lid=lid, limit=limit)

    def list_members(self, lid, limit=-1):
        return self._gen("list_members", lid=lid, limit=limit)

    def community_tweets(self, cid, limit=-1):
        return self._gen("community_tweets", cid=cid, limit=limit)

    def bookmarks(self, limit=-1):
        return self._gen("bookmarks", limit=limit)

    async def user_by_login(self, username):
        return self._user


def make_scraper(factory, *, locks=None, budget=90.0, accounts=1) -> Scraper:
    scraper = Scraper(FakePool(locks=locks, accounts=accounts), default_budget=budget)
    scraper._api = FakeAPI(factory)
    return scraper


def locked(queue: str, seconds: int = 300) -> dict:
    return {queue: (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()}


# ── normal completion ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_normal_end_below_limit_not_truncated():
    scraper = make_scraper(lambda: finite_gen(3))
    results = await scraper.search("hello", limit=20)

    assert isinstance(results, SearchResults)
    assert isinstance(results, list)  # backwards-compatible container
    assert results.count == 3
    assert len(results) == 3
    assert results.truncated is False
    # item shape unchanged
    assert set(results[0]) >= {"id", "text", "author_username", "created_at"}
    assert results[0]["id"] == "1000"


@pytest.mark.asyncio
async def test_search_limit_is_honoured_and_clean_limit_is_not_truncated():
    scraper = make_scraper(lambda: limit_overflow_gen(50))
    results = await scraper.search("hello", limit=10)

    assert len(results) == 10
    assert results.count == 10
    assert results.truncated is False  # hitting the requested limit is completion


@pytest.mark.asyncio
async def test_empty_feed_is_complete_not_an_error():
    scraper = make_scraper(lambda: empty_gen())
    results = await scraper.search("nothing", limit=20)

    assert results == []
    assert results.count == 0
    assert results.truncated is False


@pytest.mark.asyncio
async def test_dict_reads_carry_count_and_truncated():
    scraper = make_scraper(lambda: finite_gen(5))
    out = await scraper.user_tweets("u", limit=20)
    assert out["count"] == 5
    assert out["truncated"] is False
    assert len(out["tweets"]) == 5


@pytest.mark.asyncio
async def test_search_users_coverage_and_item_shape():
    scraper = make_scraper(lambda: finite_gen(4, user_item))
    results = await scraper.search_users("someone", limit=10)

    assert isinstance(results, SearchResults)
    assert results.item_kind == "users"
    assert results.count == 4
    assert results.truncated is False
    assert set(results[0]) >= {"id", "username", "display_name", "followers"}
    assert results.envelope() == {"users": list(results), "count": 4, "truncated": False}


# ── slow / budget exhaustion ────────────────────────────────────────


@pytest.mark.asyncio
async def test_slow_walk_is_truncated_with_partial_data():
    # budget 0.2s, each item takes 0.05s -> a few items then the deadline hits.
    scraper = make_scraper(lambda: slow_gen(0.05), budget=0.2)
    out = await scraper.user_tweets("u", limit=20)

    assert 0 < out["count"] < 20
    assert out["truncated"] is True


@pytest.mark.asyncio
async def test_slow_search_marked_truncated_not_fake_complete():
    scraper = make_scraper(lambda: slow_gen(0.05), budget=0.12)
    results = await scraper.search("hello", limit=20)
    assert results.truncated is True
    assert results.count >= 1  # partial data preserved


@pytest.mark.asyncio
async def test_budget_exhausted_with_zero_items_and_locks_raises_rate_limit():
    # First item never arrives in time; lock table proves a pool-wide limit.
    scraper = make_scraper(
        lambda: slow_gen(1.0),
        locks=locked("SearchTimeline", 42),
        budget=0.02,
    )
    with pytest.raises(XError) as ei:
        await scraper.search("hello", limit=5)
    err = ei.value
    assert err.kind == "rate_limit"
    assert err.retry_after is not None and err.retry_after <= 42
    assert err.hint and ("retry" in err.hint.lower() or "wait" in err.hint.lower())


# ── rate limit chain ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generator_raising_rate_limit_with_no_items_raises():
    exc = RuntimeError("Rate limit exceeded for queue SearchTimeline")
    scraper = make_scraper(lambda: raising_gen(exc))
    with pytest.raises(XError) as ei:
        await scraper.search("hello", limit=20)
    err = ei.value
    assert err.kind == "rate_limit"
    assert err.hint  # readable retry hint present


@pytest.mark.asyncio
async def test_no_account_error_with_no_items_raises_rate_limit():
    scraper = make_scraper(
        lambda: raising_gen(NoAccountError("No account available for queue Followers")),
        locks=locked("Followers", 120),
    )
    with pytest.raises(XError) as ei:
        await scraper.followers("u", limit=20)
    err = ei.value
    assert err.kind == "rate_limit"
    assert err.retry_after is not None and err.retry_after <= 120
    assert err.hint


@pytest.mark.asyncio
async def test_rate_limit_after_partial_data_keeps_data_and_truncates():
    exc = RuntimeError("Rate limit exceeded")
    scraper = make_scraper(lambda: raising_gen(exc, lead_items=7))
    results = await scraper.search("hello", limit=20)

    assert results.count == 7
    assert results.truncated is True


@pytest.mark.asyncio
async def test_rate_limit_after_partial_data_on_dict_read():
    exc = NoAccountError("No account available for queue TweetDetail")
    scraper = make_scraper(lambda: raising_gen(exc, lead_items=4))
    out = await scraper.thread("123", limit=20)
    assert out["count"] == 4
    assert out["truncated"] is True


@pytest.mark.asyncio
async def test_silent_generator_stop_with_locked_pool_raises_rate_limit():
    # twscrape's real shape: queue client returns None and the generator simply
    # ends. With every account locked, that must not look like a clean empty.
    scraper = make_scraper(lambda: empty_gen(), locks=locked("Bookmarks", 90))
    with pytest.raises(XError) as ei:
        await scraper.bookmarks(limit=20)
    assert ei.value.kind == "rate_limit"
    assert ei.value.retry_after is not None and ei.value.retry_after <= 90


@pytest.mark.asyncio
async def test_ensure_available_fast_path_still_fires_before_walk():
    scraper = make_scraper(lambda: finite_gen(10), locks=locked("SearchTimeline", 60))
    with pytest.raises(XError) as ei:
        await scraper.search("hello", limit=20)
    assert ei.value.kind == "rate_limit"


@pytest.mark.asyncio
async def test_non_rate_limit_failure_with_no_items_is_network_error():
    scraper = make_scraper(lambda: raising_gen(RuntimeError("connection reset")))
    with pytest.raises(XError) as ei:
        await scraper.search("hello", limit=20)
    assert ei.value.kind == "network"


@pytest.mark.asyncio
async def test_non_rate_limit_failure_after_items_truncates():
    scraper = make_scraper(lambda: raising_gen(RuntimeError("connection reset"), lead_items=3))
    results = await scraper.search("hello", limit=20)
    assert results.count == 3
    assert results.truncated is True


# ── budget relaxation for long social-graph walks ───────────────────


@pytest.mark.asyncio
async def test_followers_keep_relaxed_budget():
    scraper = make_scraper(lambda: finite_gen(50), budget=10.0)
    # limit=1000 -> relaxed budget = min(300, 250) = 250s, far above default 10s;
    # a feed of 50 quick items completes well inside either budget.
    out = await scraper.followers("u", limit=1000)
    assert out["count"] == 50
    assert out["truncated"] is False
    assert scraper._budget(1000, long_walk=True) == 250.0
    assert scraper._budget(100, long_walk=True) == 25.0  # max(default 10, limit/4=25)
    assert scraper._budget(100, long_walk=False) == 10.0
    assert scraper._budget(10, long_walk=True) == 10.0  # small walks stay on default


@pytest.mark.asyncio
async def test_followers_relaxed_budget_survives_a_slow_walk():
    # default budget would kill this at ~10s; the long-walk budget (250s)
    # lets the whole feed finish, proving the paths are not flattened together.
    scraper = make_scraper(lambda: slow_gen(0.02, n=6), budget=10.0)
    out = await scraper.followers("u", limit=1000)
    assert out["count"] == 6
    assert out["truncated"] is False


# ── backwards compatibility of SearchResults ────────────────────────


@pytest.mark.asyncio
async def test_search_results_behave_as_a_plain_list():
    scraper = make_scraper(lambda: finite_gen(3))
    results = await scraper.search("hello", limit=20)

    assert [r["id"] for r in results] == ["1000", "1001", "1002"]
    assert results[0]["text"] == "tweet 0"
    assert bool(results) is True
    assert list(results) == results[:]
    # envelope keeps the same single-item dicts, only wraps the collection
    wrapped = results.envelope()
    assert wrapped["tweets"][0] is results[0]
