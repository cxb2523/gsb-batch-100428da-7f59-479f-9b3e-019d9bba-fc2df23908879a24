"""collect_discover_cards against scripted fakes: stop reasons, read quality, sorting, cache isolation.

Run from the repo root:  python -I -m pytest -q
The fakes never open a socket; every "read" is one line of a script. PARALLEL is pinned to 2 so every
batch and every read count in the assertions below is deterministic.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from google_discover_api_mcp.google_discover import client
from google_discover_api_mcp.google_discover.tools import collect_discover_cards as cc
from tests.fakes import FakeContext, make_card, rate_limited

HANDLER = cc.collect_discover_cards.handler


@pytest.fixture(autouse=True)
async def _small_parallel_batches(monkeypatch):
    # Without a configured setup, chrome.KEYS is empty and client.ask would refuse before reaching the fakes.
    monkeypatch.setattr(client, "KEYS", ["test-key"])
    # A known baseline for every test; individual tests may tighten these further.
    monkeypatch.setattr(cc, "PARALLEL", 2)
    monkeypatch.setattr(cc, "MAX_READS", 150)
    monkeypatch.setattr(cc, "STALL_READS", 15)
    monkeypatch.setattr(cc, "STALL_NEW", 2)
    monkeypatch.setattr(cc, "DEADLINE_S", 70)
    yield


def cards(n: int, *, prefix: str = "https://example-news.com/story", start: int = 0) -> list[dict[str, str]]:
    return [make_card(f"{prefix}-{start + i}") for i in range(n)]


async def run(script: list[Any], args: dict[str, Any] | None = None, cache: dict[str, Any] | None = None):
    ctx = FakeContext(script, cache)
    result = await HANDLER(ctx, args or {})
    return ctx, result


def assert_base_shape(result: dict[str, Any]) -> None:
    # The original fields are still there...
    assert set(result) >= {
        "country",
        "language",
        "collected_at",
        "reads",
        "complete",
        "cards",
        # ...next to the new termination and quality fields.
        "successful_reads",
        "failed_reads",
        "stop_reason",
    }
    assert result["reads"] == result["successful_reads"]
    assert result["stop_reason"] in {reason.value for reason in cc.StopReason}


async def test_target_reached_is_complete_and_counts_only_successful_reads():
    ctx, result = await run([cards(10), cards(10, start=10)], {"cards": 15})
    assert_base_shape(result)
    assert result["stop_reason"] == cc.StopReason.TARGET_REACHED.value
    assert result["complete"] is True
    assert result["successful_reads"] == 2
    assert result["failed_reads"] == 0
    assert len(result["cards"]) == 15
    # Each read left through its own client: no shared single-read connection.
    assert len(ctx.exits) == 2
    assert all(exits.requests == 1 for exits in ctx.exits)


async def test_partial_failures_keep_going_and_are_counted():
    script = [
        rate_limited(),
        cards(10),  # batch 1: one failed, ten new cards
        500,
        cards(10, start=10),  # batch 2: one failed again, ten more
    ]
    ctx, result = await run(script, {"cards": 15})
    assert_base_shape(result)
    assert result["stop_reason"] == cc.StopReason.TARGET_REACHED.value
    assert result["complete"] is True
    assert result["successful_reads"] == 2
    assert result["failed_reads"] == 2
    assert len(result["cards"]) == 15
    assert all(exits.requests == 1 for exits in ctx.exits)  # every read was a fresh exit


async def test_all_reads_failing_raises_target_error():
    with pytest.raises(Exception) as exc_info:
        await run([429, 500], {"cards": 5})
    assert exc_info.value.code.value == "target_error"


async def test_pool_dry_when_successive_reads_bring_almost_nothing_new(monkeypatch):
    monkeypatch.setattr(cc, "STALL_READS", 3)
    monkeypatch.setattr(cc, "STALL_NEW", 1)
    # Batch 1 fills 31 cards; every later read returns the same cards (zero growth), and after three
    # growth-free reads in a row the pool is declared dry at the end of batch 2.
    script = [cards(31), cards(31), cards(31), cards(31)]
    _ctx, result = await run(script, {"cards": 300})
    assert_base_shape(result)
    assert result["stop_reason"] == cc.StopReason.POOL_DRY.value
    assert result["complete"] is False
    assert result["failed_reads"] == 0
    assert result["successful_reads"] == 4  # 2 reads of the first batch + the 3-read stall window
    assert len(result["cards"]) == 31


async def test_stall_counts_failures_as_zero_growth_too(monkeypatch):
    monkeypatch.setattr(cc, "STALL_READS", 3)
    monkeypatch.setattr(cc, "STALL_NEW", 1)
    # The stall window only advances with successful reads; failures fill the batches but bring no growth.
    # Batch 1 fills the pool, batch 2 fails twice, batch 3's two duplicate reads complete a three-read
    # zero-growth window together with batch 1's second read, so the call stops before batch 4.
    script = [
        cards(31),
        cards(31),  # batch 1: pool filled; the second read is the window's first zero-growth read
        rate_limited(),
        rate_limited(),  # batch 2: two failed reads, no growth
        cards(31),
        cards(31),  # batch 3: zero-growth reads 2-3 -> stalls
        rate_limited(),
        cards(31),  # batch 4: never reached, kept only as an overshoot guard
    ]
    _, result = await run(script, {"cards": 300})
    assert result["stop_reason"] == cc.StopReason.POOL_DRY.value
    # Batch 1's second read plus batch 3's two duplicate reads make the three-read zero-growth window,
    # so the call stops before batch 4: four successful reads, the two batch-2 failures.
    assert result["successful_reads"] == 4
    assert result["failed_reads"] == 2


async def test_max_reads_ceiling_stops_the_collection(monkeypatch):
    monkeypatch.setattr(cc, "MAX_READS", 4)
    monkeypatch.setattr(cc, "STALL_READS", 100)  # never stall before the read ceiling
    _, result = await run([cards(2) for _ in range(4)], {"cards": 300})
    assert_base_shape(result)
    assert result["stop_reason"] == cc.StopReason.MAX_READS.value
    assert result["complete"] is False
    assert result["successful_reads"] == 4
    assert result["failed_reads"] == 0


async def test_time_limit_keeps_finished_reads_and_drops_hanging_ones(monkeypatch):
    monkeypatch.setattr(cc, "DEADLINE_S", 0.05)

    class HangingHttp:
        requests = 0

        async def request(self, *args, **kwargs):
            type(self).requests += 1
            await asyncio.sleep(60)

    class OneAnswerThenHanging(FakeContext):
        def __init__(self, script):
            super().__init__(script)
            self._answered = False

        def new_http(self):
            if not self._answered:
                self._answered = True
                return FakeHttpOnce(self._script, self._counter)
            return HangingHttp()

    from tests.fakes import FakeHttp as FakeHttpOnce

    ctx = OneAnswerThenHanging([cards(5), cards(5)])
    result = await HANDLER(ctx, {"cards": 300})
    assert_base_shape(result)
    assert result["stop_reason"] == cc.StopReason.TIME_LIMIT.value
    assert result["complete"] is False
    assert result["successful_reads"] >= 1
    assert result["failed_reads"] == 0  # hanging reads were cancelled, not counted as failed
    assert len(result["cards"]) == 5
    assert HangingHttp.requests > 0  # further reads really started and were cut off


async def test_collection_never_touches_the_single_read_cache():
    poisoned = {"feed:US:en-US": '{"country": "US", "language": "en-US", "cards": "STALE SNAPSHOT"}'}
    ctx, result = await run([cards(7), cards(7)], {"cards": 7}, cache=poisoned)
    # The poisoned single-read snapshot survived untouched and was never returned...
    assert "feed:US:en-US" in ctx.cache
    assert all(card["url"] != "STALE SNAPSHOT" for card in result["cards"])
    # ...and nothing the collection made was written into the cache.
    assert list(ctx.cache) == ["feed:US:en-US"]


async def test_cards_sort_by_seen_count_then_best_position(monkeypatch):
    monkeypatch.setattr(cc, "STALL_READS", 100)
    monkeypatch.setattr(cc, "MAX_READS", 4)
    url_a, url_b, url_c = "https://a.example/a", "https://b.example/b", "https://c.example/c"
    # C always rides in second position; A and B alternate at the front.
    script = [
        [make_card(url_a, title="A"), make_card(url_c, title="C")],
        [make_card(url_b, title="B"), make_card(url_c, title="C")],
        [make_card(url_a, title="A"), make_card(url_c, title="C")],
        [make_card(url_b, title="B"), make_card(url_c, title="C")],
    ]
    _, result = await run(script, {"cards": 300})
    by_url = {card["url"]: card for card in result["cards"]}
    # A in 2 reads, B in 2, C in all 4; between A and B the stable order keeps A first.
    assert by_url[url_c]["seen_in"] == 4 and by_url[url_a]["seen_in"] == 2 and by_url[url_b]["seen_in"] == 2
    assert [card["url"] for card in result["cards"]] == [url_c, url_a, url_b]
    # best_position is the highest place the card ever held.
    assert by_url[url_a]["best_position"] == 1
    assert by_url[url_b]["best_position"] == 1
    assert by_url[url_c]["best_position"] == 2


async def test_empty_answer_counts_as_a_failed_read():
    _, result = await run([b"", b"", cards(4), cards(4)], {"cards": 4})
    assert result["successful_reads"] == 2
    assert result["failed_reads"] == 2
    assert result["stop_reason"] == cc.StopReason.TARGET_REACHED.value


async def test_original_response_fields_keep_their_meaning(monkeypatch):
    monkeypatch.setattr(cc, "STALL_READS", 3)
    monkeypatch.setattr(cc, "STALL_NEW", 1)
    # Only three different cards exist; repeated reads return them and the pool runs dry.
    _, result = await run([cards(3), cards(3), cards(3), cards(3), cards(3)], {"cards": 10})
    assert result["country"] == "US"
    assert result["language"] == "en-US"
    assert result["complete"] is False  # only 3 different cards existed, 10 were asked
    assert result["stop_reason"] == cc.StopReason.POOL_DRY.value
    card = result["cards"][0]
    assert set(card) >= {"title", "publisher", "url", "domain", "type", "age", "image_url", "seen_in", "best_position"}
    assert "position" not in card  # the merged shape never carried the per-read position


async def test_single_read_tools_still_use_the_fifteen_minute_cache():
    # The single-read path must keep its exact behavior: client.feed reads and writes the shared cache.
    from google_discover_api_mcp.core.context import Profile, ToolContext
    from google_discover_api_mcp.google_discover import client
    from tests.fakes import FakeHttp

    settings = type("Settings", (), {"proxy_for": lambda self, country: None})()
    ctx = ToolContext(settings=settings, profile=Profile(country="US"))
    fake = FakeHttp([[make_card("https://one.example/1")]], [0])
    ctx._http = fake  # the cached single read uses ctx.http, not new_http()

    first = await client.feed(ctx, "US", "en-US")
    second = await client.feed(ctx, "US", "en-US")
    assert fake.requests == 1  # the second call was served from the cache
    assert first == second
    cached = await ctx.cache_get("feed:US:en-US")
    assert cached is not None and "one.example" in cached
    assert client.CACHE_S == 15 * 60  # the single-read cache duration is unchanged
