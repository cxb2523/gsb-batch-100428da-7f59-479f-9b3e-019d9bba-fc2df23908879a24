"""Collect many Discover cards of a country."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from google_discover_api_mcp.core.errors import ErrorCode, ToolError
from google_discover_api_mcp.core.tools import tool
from google_discover_api_mcp.google_discover import protowire as pw
from google_discover_api_mcp.google_discover.client import cards, language_of
from google_discover_api_mcp.google_discover.exits import ExitContext, ask_from_new_exit
from google_discover_api_mcp.google_discover.tools._feed import COUNTRY, LANGUAGE

if TYPE_CHECKING:
    from google_discover_api_mcp.core.context import ToolContext

# One read holds about 31 cards, drawn from a larger pool that changes with each read and each home IP.
# Measured 25.10.2026 in the US: 10 reads 126 unique cards, 40 reads 212, 90 reads 265.
MAX_CARDS = 300
DEFAULT_CARDS = 300
MAX_READS = 150
PARALLEL = 8
# Stop when the pool runs dry: this many reads in a row brought at most STALL_NEW new cards.
STALL_READS = 15
STALL_NEW = 2
# The agent gets its answer within 85 s (api ANSWER_WITHIN_S; Cloudflare cuts at 100 s): room to wait and reply.
DEADLINE_S = 70


class StopReason(StrEnum):
    """Why a collection stopped, and therefore whether retrying can help."""

    # The requested number of different cards was reached; the result is complete.
    TARGET_REACHED = "target_reached"
    # The last STALL_READS reads brought almost no new cards: the country's pool of cards ran dry.
    POOL_DRY = "pool_dry"
    # The call used its whole time budget (DEADLINE_S); the same call later may find a changed pool.
    TIME_LIMIT = "time_limit"
    # MAX_READS were already made; fewer reads would leave more of the pool untouched.
    MAX_READS = "max_reads"


INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "cards": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_CARDS,
            "description": f"How many different cards to collect, at most {MAX_CARDS}. Default: {DEFAULT_CARDS}.",
        },
        "country": COUNTRY,
        "language": LANGUAGE,
    },
    "required": [],
    "additionalProperties": False,
}

EXAMPLE_RESPONSE: dict[str, Any] = {
    "country": "US",
    "language": "en-US",
    "collected_at": "2026-10-25T17:40:12+00:00",
    "reads": 74,
    "successful_reads": 74,
    "failed_reads": 0,
    "stop_reason": "target_reached",
    "complete": True,
    "cards": [
        {
            "title": "Ten herbs that survive a cold windowsill",
            "publisher": "Example News",
            "url": "https://example-news.com/guides/windowsill-herbs",
            "domain": "example-news.com",
            "type": "article",
            "age": "3h",
            "image_url": "https://encrypted-tbn0.gstatic.com/images?q=tbn:example",
            "seen_in": 41,
            "best_position": 1,
        },
        {
            "title": "Playful sea otters and mini pumpkins",
            "publisher": "YouTube",
            "url": "https://www.youtube.com/watch?v=example",
            "domain": "youtube.com",
            "type": "video",
            "age": "1d",
            "image_url": "https://encrypted-tbn1.gstatic.com/images?q=tbn:example",
            "seen_in": 3,
            "best_position": 12,
        },
    ],
}


class _Pool:
    """Cards of every read, by URL: how many reads showed each and its best position."""

    def __init__(self) -> None:
        self.cards: dict[str, dict[str, Any]] = {}
        self.reads = 0
        self.growth: list[int] = []

    def add(self, found: list[dict[str, Any]]) -> None:
        before = len(self.cards)
        for card in found:
            kept = self.cards.get(card["url"])
            if kept is None:
                kept = {k: v for k, v in card.items() if k != "position"} | {"seen_in": 0, "best_position": 999}
                self.cards[card["url"]] = kept
            kept["seen_in"] += 1
            kept["best_position"] = min(kept["best_position"], card["position"])
        self.reads += 1
        self.growth.append(len(self.cards) - before)

    def stalled(self) -> bool:
        return len(self.growth) >= STALL_READS and sum(self.growth[-STALL_READS:]) <= STALL_NEW


class _CollectionContext:
    """The narrow context a collection reads through: a fresh exit per read and the log, never the cache.

    Wrapping the real context means the collection can only reach ``new_http`` and ``log``. The single-read
    tools keep their 15-minute per-country/language cache; this path has no access to it and cannot pollute it.
    """

    def __init__(self, ctx: ToolContext) -> None:
        self._ctx = ctx

    def new_http(self) -> Any:
        return self._ctx.new_http()

    @property
    def log(self) -> Any:
        return self._ctx.log


async def _read(ctx: ExitContext, language: str) -> list[dict[str, Any]] | None:
    """One read through a fresh exit in the call's country. None when that exit or read failed, including an
    empty answer, so one bad read counts as a failed read but never aborts the collection."""
    try:
        found = cards(pw.strings(await ask_from_new_exit(ctx, language)))
    except Exception as error:  # noqa: BLE001 - the collection survives any single broken exit or read
        ctx.log.info("Discover read failed, the collection goes on", error=f"{type(error).__name__}: {error}")
        return None
    if not found:
        ctx.log.info("Discover read returned no cards, the collection goes on")
        return None
    return found


@tool(
    name="collect_discover_cards",
    title="Collect Discover cards",
    access="read",
    confirm=False,
    description="Up to 300 different cards Discover shows in a country, from many reads, with how often each appeared.",
    details=(
        "One read of the Discover feed holds about 31 cards, and each read draws them from a larger pool. This "
        "tool reads the feed of the country many times in parallel, each time on a new connection, until it "
        "has the number of different cards asked for or the pool runs dry, and returns them at once. seen_in says "
        "in how many reads a card appeared (a card in most reads is the core of the feed, one seen once is rare) and "
        "best_position its highest place. Cards come most often seen first, ties by best position. "
        "stop_reason says why it stopped: target_reached means complete is true and the asked count was collected; "
        "pool_dry means the last reads brought almost no new cards, so the feed held no more different ones; "
        "time_limit means the call's about 70 seconds were up; max_reads means the 150-read ceiling was hit. "
        "successful_reads and failed_reads count the reads: a failed read is retried by the next reads and never "
        "fails the call, only a call whose every read failed errors. reads equals successful_reads and counts the "
        "reads whose cards were merged; complete is true exactly when the asked number of different cards was "
        "reached. These reads are never the cached single-read snapshot of get_discover_feed and are not cached "
        "themselves, so every call starts from fresh reads. A call takes up to about 70 seconds and costs more "
        "credits than one read, because it makes many."
    ),
    input_schema=INPUT_SCHEMA,
    route="GET /feed/cards",
    example_response=EXAMPLE_RESPONSE,
    timeout_s=120,
)
async def collect_discover_cards(ctx: ToolContext, args: dict[str, Any]) -> Any:
    wanted = args.get("cards") or DEFAULT_CARDS
    country = ctx.profile.country
    language = language_of(country, args.get("language"))
    collection = _CollectionContext(ctx)
    pool = _Pool()
    failed_reads = 0
    deadline = time.monotonic() + DEADLINE_S
    started_reads = 0
    stop_reason: StopReason | None = None
    while len(pool.cards) < wanted and started_reads < MAX_READS and not pool.stalled():
        if time.monotonic() >= deadline:
            stop_reason = StopReason.TIME_LIMIT
            break
        # Size the batch against the remaining read ceiling so a parallel batch never overshoots MAX_READS.
        batch = min(PARALLEL, MAX_READS - started_reads)
        started_reads += batch
        tasks = [asyncio.create_task(_read(collection, language)) for _ in range(batch)]
        remaining = deadline - time.monotonic()
        try:
            async with asyncio.timeout(max(remaining, 1)):
                results = await asyncio.gather(*tasks)
        except TimeoutError:
            # The time budget is spent: keep the reads that already answered, let the rest go with the call.
            for task in tasks:
                task.cancel()
            settled = await asyncio.gather(*tasks, return_exceptions=True)
            stop_reason = StopReason.TIME_LIMIT
            results = [outcome for outcome in settled if not isinstance(outcome, BaseException)]
        for found in results:
            if found is None:
                failed_reads += 1
            else:
                pool.add(found)
        # The batch may have reached a target; name its reason instead of taking another loop turn.
        if len(pool.cards) >= wanted:
            stop_reason = StopReason.TARGET_REACHED
            break
        if pool.stalled():
            stop_reason = StopReason.POOL_DRY
            break
        if started_reads >= MAX_READS:
            stop_reason = StopReason.MAX_READS
            break
        if time.monotonic() >= deadline:
            stop_reason = StopReason.TIME_LIMIT
            break
    else:
        # The while condition itself was already false on entry (no batch was started).
        if len(pool.cards) >= wanted:
            stop_reason = StopReason.TARGET_REACHED
        elif pool.stalled():
            stop_reason = StopReason.POOL_DRY
        else:
            stop_reason = StopReason.MAX_READS
    if pool.reads == 0:
        raise ToolError("Google did not serve the feed. Try again in a minute.", ErrorCode.TARGET_ERROR)
    assert stop_reason is not None  # every loop exit sets a reason above
    ranked = sorted(pool.cards.values(), key=lambda card: (-card["seen_in"], card["best_position"]))
    return {
        "country": country,
        "language": language,
        "collected_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "reads": pool.reads,
        "successful_reads": pool.reads,
        "failed_reads": failed_reads,
        "stop_reason": stop_reason.value,
        "complete": len(pool.cards) >= wanted,
        "cards": ranked[:wanted],
    }
