"""Per-leg trajectory (`leg_trace`) of `FacebookScraper.user_timeline`.

A high-level `user_timeline` call can span several submitted tasks ("legs"):
a leg returning `cursor_reset` makes the scraper advance `end_date` backward
to its oldest collected post's day and submit another leg (up to
`MAX_CURSOR_RESET_RESUMES`). The merged `ScrapingResult` hides that loop; an
opt-in `leg_trace` list records one `TimelineLegTrace` per leg — its own
result, per-leg post count, cross-leg-deduped new count, the `end_date`
actually submitted, whether the reset caused another leg, and whether a
resume cursor was used.

Everything here runs against a hand-built fake worker pool and fake
ScrapingResults: no network, no browser.

Asserted outcomes:
- normal end (incl. max_posts_reached);
- cursor_reset followed by a leg that successfully advances the date;
- cursor_reset whose oldest post does not move the date (no_progress);
- cursor_reset on a leg that returned no posts (no_posts);
- cursor_reset with no upper bound (terminal, partial data preserved);
- retry cap exhausted (`cursor_reset_max_retries`);
- unreadable resume file → fresh-scrape fallback, flagged on leg 0's trace;
- trace-off behavior and per-target trace isolation in a concurrent batch.
"""


import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from fbscrape import scraper as scraper_mod
from fbscrape.models import Query, ScrapingResult, TimelineLegTrace
from fbscrape.scraper import FacebookScraper, MAX_CURSOR_RESET_RESUMES


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

def _make_post(post_id: str, created_unix: int | None) -> dict:
    """Minimal raw `{node: Story}` record. With a creation_time it flattens
    to a datable post (drives the cursor-reset end_date computation);
    without one it still carries a flattenable `post_id` for dedup."""
    if created_unix is None:
        return {"node": {"post_id": post_id}}
    return {"node": {
        "post_id": post_id,
        "comet_sections": {"context_layout": {"story": {"comet_sections": {
            "metadata": [{
                "__typename": "CometFeedStoryMinimizedTimestampStrategy",
                "story": {"creation_time": created_unix},
            }],
        }}}},
    }}


def _date_unix(date_str: str) -> int:
    """Noon UTC on `date_str` (YYYY-MM-DD) as a unix timestamp."""
    d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(d.timestamp()) + 12 * 3600


def _leg_result(query: Query, result: str, data: list[dict],
                last_cursor: str | None = None) -> ScrapingResult:
    return ScrapingResult(
        query=query,
        result=result,
        data=data,
        time_started=datetime.now(timezone.utc),
        time_taken=timedelta(seconds=1),
        last_cursor=last_cursor,
    )


class _FakeWorkerPool:
    """Stand-in worker pool. Each submitted Query is answered with the next
    scripted (result, data) pair; the last pair repeats if the scraper submits
    more legs than scripted. Records every submitted Query for assertions."""

    def __init__(self, scripted: list[tuple[str, list[dict]]]):
        self._scripted = list(scripted)
        self.submitted: list[Query] = []

    async def submit_task(self, query: Query) -> asyncio.Future:
        self.submitted.append(query)
        idx = min(len(self.submitted) - 1, len(self._scripted) - 1)
        result, data = self._scripted[idx]
        fut = asyncio.get_running_loop().create_future()
        fut.set_result(_leg_result(query, result, data))
        return fut


def _make_scraper(monkeypatch, scripted: list[tuple[str, list[dict]]]):
    # AccountsPool(db=...) only stores the path — no DB/file is opened until
    # a real WorkerPool initializes, which _ensure_initialized is stubbed to
    # prevent.
    scraper = FacebookScraper(db=":memory:")
    pool = _FakeWorkerPool(scripted)
    scraper.worker_pool = pool

    async def _noop():
        return None

    monkeypatch.setattr(scraper, "_ensure_initialized", _noop)
    return scraper, pool


# --------------------------------------------------------------------------- #
# 1. Normal end
# --------------------------------------------------------------------------- #

async def test_single_leg_normal_end(monkeypatch):
    posts = [_make_post("p1", _date_unix("2025-03-01")),
             _make_post("p2", _date_unix("2025-02-01"))]
    scraper, pool = _make_scraper(monkeypatch, [("success", posts)])

    trace: list[TimelineLegTrace] = []
    result = await scraper.user_timeline(
        "zuck", start_date="2025-01-01", end_date="2025-06-01",
        leg_trace=trace,
    )

    # The merged ScrapingResult is unchanged in shape and semantics.
    assert result.result == "success"
    assert len(result.data) == 2
    assert len(pool.submitted) == 1

    assert len(trace) == 1
    entry = trace[0]
    assert entry.leg_index == 0
    assert entry.result == "success"
    assert entry.leg_post_count == 2
    assert entry.new_post_count == 2
    assert entry.end_date == "2025-06-01"
    assert entry.cursor_reset_continued is False
    assert entry.resume_cursor_used is False
    assert entry.resume_read_failed is False


async def test_single_leg_max_posts_reached(monkeypatch):
    posts = [_make_post(f"p{i}", _date_unix("2025-03-01")) for i in range(3)]
    scraper, _pool = _make_scraper(monkeypatch, [("max_posts_reached", posts)])

    trace: list[TimelineLegTrace] = []
    result = await scraper.user_timeline(
        "zuck", start_date="2025-01-01", end_date="2025-06-01",
        max_posts=3, leg_trace=trace,
    )

    assert result.result == "max_posts_reached"
    assert len(trace) == 1
    assert trace[0].result == "max_posts_reached"
    assert trace[0].leg_post_count == 3
    assert trace[0].new_post_count == 3
    assert trace[0].cursor_reset_continued is False


# --------------------------------------------------------------------------- #
# 2. Cursor reset, then a leg that successfully advances the date
# --------------------------------------------------------------------------- #

async def test_cursor_reset_then_successful_progress(monkeypatch):
    leg0_posts = [_make_post("a1", _date_unix("2025-05-01")),
                  _make_post("a2", _date_unix("2025-04-15"))]
    # Second leg overlaps a2 (re-served at the bootstrap edge) and adds b1.
    leg1_posts = [_make_post("a2", _date_unix("2025-04-15")),
                  _make_post("b1", _date_unix("2025-03-10"))]
    scraper, pool = _make_scraper(monkeypatch, [
        ("cursor_reset", leg0_posts),
        ("success", leg1_posts),
    ])

    trace: list[TimelineLegTrace] = []
    result = await scraper.user_timeline(
        "zuck", start_date="2025-01-01", end_date="2025-06-01",
        leg_trace=trace,
    )

    assert result.result == "success"
    assert len(pool.submitted) == 2
    # The reset leg's end_date advanced to its oldest post's day.
    assert pool.submitted[1].query["end_date"] == "2025-04-15"

    assert len(trace) == 2

    e0, e1 = trace
    assert e0.leg_index == 0 and e1.leg_index == 1
    assert e0.result == "cursor_reset"
    assert e0.leg_post_count == 2
    assert e0.new_post_count == 2
    assert e0.end_date == "2025-06-01"
    assert e0.cursor_reset_continued is True

    assert e1.result == "success"
    assert e1.leg_post_count == 2          # a2 re-served + b1
    assert e1.new_post_count == 1          # a2 deduped across legs
    assert e1.end_date == "2025-04-15"
    assert e1.cursor_reset_continued is False

    # Merged data keeps all three unique posts.
    assert len(result.data) == 3


# --------------------------------------------------------------------------- #
# 3. Cursor reset but the date does not move forward
# --------------------------------------------------------------------------- #

async def test_cursor_reset_no_date_progress(monkeypatch):
    # Oldest post is ON the current end_date, so new_end_date >= end_date.
    posts = [_make_post("a1", _date_unix("2025-06-01"))]
    scraper, pool = _make_scraper(monkeypatch, [("cursor_reset", posts)])

    trace: list[TimelineLegTrace] = []
    result = await scraper.user_timeline(
        "zuck", start_date="2025-01-01", end_date="2025-06-01",
        leg_trace=trace,
    )

    assert result.result == "cursor_reset_no_progress"
    assert len(pool.submitted) == 1        # no second leg submitted
    assert len(trace) == 1
    entry = trace[0]
    assert entry.result == "cursor_reset"
    assert entry.cursor_reset_continued is False
    assert entry.end_date == "2025-06-01"
    # Partial data is preserved in the merged result.
    assert len(result.data) == 1


# --------------------------------------------------------------------------- #
# 4. Cursor reset with no posts to advance on
# --------------------------------------------------------------------------- #

async def test_cursor_reset_no_posts_to_advance(monkeypatch):
    scraper, pool = _make_scraper(monkeypatch, [("cursor_reset", [])])

    trace: list[TimelineLegTrace] = []
    result = await scraper.user_timeline(
        "zuck", start_date="2025-01-01", end_date="2025-06-01",
        leg_trace=trace,
    )

    assert result.result == "cursor_reset_no_posts"
    assert len(pool.submitted) == 1
    assert len(trace) == 1
    assert trace[0].result == "cursor_reset"
    assert trace[0].leg_post_count == 0
    assert trace[0].new_post_count == 0
    assert trace[0].end_date == "2025-06-01"
    assert trace[0].cursor_reset_continued is False
    assert result.data == []


# --------------------------------------------------------------------------- #
# 5. Cursor reset with no upper bound — terminal, partial data kept
# --------------------------------------------------------------------------- #

async def test_cursor_reset_without_end_date_is_terminal(monkeypatch):
    posts = [_make_post("a1", _date_unix("2025-05-01"))]
    scraper, pool = _make_scraper(monkeypatch, [("cursor_reset", posts)])

    trace: list[TimelineLegTrace] = []
    result = await scraper.user_timeline(
        "zuck", start_date="2025-01-01", end_date=None,
        leg_trace=trace,
    )

    assert result.result == "cursor_reset"
    assert len(pool.submitted) == 1        # nothing to advance → no resume leg
    assert len(trace) == 1
    entry = trace[0]
    assert entry.result == "cursor_reset"
    assert entry.end_date is None
    assert entry.cursor_reset_continued is False
    assert entry.leg_post_count == 1
    assert len(result.data) == 1           # partial data preserved


# --------------------------------------------------------------------------- #
# 6. Retry cap exhausted
# --------------------------------------------------------------------------- #

async def test_cursor_reset_retries_exhausted(monkeypatch):
    # Every leg resets; each leg's posts are a day older so the date keeps
    # advancing (each reset genuinely continues) until the cap.
    scripted = []
    for leg in range(MAX_CURSOR_RESET_RESUMES + 1):
        day = 20 - leg  # 2025-05-20, 2025-05-19, ...
        scripted.append((
            "cursor_reset",
            [_make_post(f"r{leg}", _date_unix(f"2025-05-{day:02d}"))],
        ))
    scraper, pool = _make_scraper(monkeypatch, scripted)

    trace: list[TimelineLegTrace] = []
    result = await scraper.user_timeline(
        "zuck", start_date="2025-01-01", end_date="2025-06-01",
        leg_trace=trace,
    )

    assert result.result == "cursor_reset_max_retries"
    # One initial leg + MAX_CURSOR_RESET_RESUMES resume legs.
    assert len(pool.submitted) == MAX_CURSOR_RESET_RESUMES + 1
    assert len(trace) == MAX_CURSOR_RESET_RESUMES + 1

    # Every leg but the last continued into another submission.
    assert [e.leg_index for e in trace] == list(
        range(MAX_CURSOR_RESET_RESUMES + 1)
    )
    assert all(e.result == "cursor_reset" for e in trace)
    assert [e.cursor_reset_continued for e in trace[:-1]] == [
        True
    ] * MAX_CURSOR_RESET_RESUMES
    assert trace[-1].cursor_reset_continued is False

    # end_date walked backward one day per submitted leg. The last leg still
    # ran with the date its predecessor computed; the next date is computed
    # afterward but never submitted (the for-else ends the call).
    assert trace[0].end_date == "2025-06-01"
    assert trace[1].end_date == "2025-05-20"
    assert trace[-1].end_date == f"2025-05-{21 - MAX_CURSOR_RESET_RESUMES:02d}"
    # Every leg contributed one unique new post.
    assert all(e.leg_post_count == 1 and e.new_post_count == 1 for e in trace)
    assert len(result.data) == MAX_CURSOR_RESET_RESUMES + 1


# --------------------------------------------------------------------------- #
# 7. Unreadable resume file — fresh-scrape fallback, flagged on the trace
# --------------------------------------------------------------------------- #

async def test_unreadable_resume_file_marks_trace(monkeypatch):
    def _boom(_path):
        raise EOFError("truncated gzip member")

    # Both resume readers route here for non-JSONL paths; force the failure.
    monkeypatch.setattr(scraper_mod, "_read_resume_state", _boom)

    posts = [_make_post("p1", _date_unix("2025-03-01"))]
    scraper, pool = _make_scraper(monkeypatch, [("success", posts)])

    trace: list[TimelineLegTrace] = []
    result = await scraper.user_timeline(
        "zuck", start_date="2025-01-01", end_date="2025-06-01",
        resume_from="/fake/unreadable.json.gz", leg_trace=trace,
    )

    # Fallback behavior preserved: no raise, one fresh-scrape leg.
    assert result.result == "success"
    assert len(pool.submitted) == 1
    assert pool.submitted[0].params.get("initial_cursor", "") == ""

    assert len(trace) == 1
    entry = trace[0]
    assert entry.resume_read_failed is True
    assert entry.resume_cursor_used is False

    # A call without resume_from must not carry the failure marker.
    scraper2, _pool2 = _make_scraper(
        monkeypatch, [("success", [_make_post("q1", _date_unix("2025-03-01"))])]
    )
    trace2: list[TimelineLegTrace] = []
    await scraper2.user_timeline(
        "zuck2", start_date="2025-01-01", end_date="2025-06-01",
        leg_trace=trace2,
    )
    assert trace2[0].resume_read_failed is False
    assert trace2[0].resume_cursor_used is False


async def test_readable_resume_cursor_is_marked_used(monkeypatch):
    # A readable resume file that yields a cursor: leg 0 must record that the
    # resume cursor was actually used (companion to the unreadable case).
    monkeypatch.setattr(
        scraper_mod, "_read_resume_state",
        lambda _path: ("CURSOR_X", ["seen1"]),
    )
    posts = [
        _make_post("seen1", _date_unix("2025-05-01")),  # seeded dedup
        _make_post("new1", _date_unix("2025-04-01")),
    ]
    scraper, pool = _make_scraper(monkeypatch, [("success", posts)])

    trace: list[TimelineLegTrace] = []
    result = await scraper.user_timeline(
        "zuck", start_date="2025-01-01", end_date="2025-06-01",
        resume_from="/fake/good.json.gz", leg_trace=trace,
    )

    assert pool.submitted[0].params["initial_cursor"] == "CURSOR_X"
    assert trace[0].resume_cursor_used is True
    assert trace[0].resume_read_failed is False
    # The seeded id is deduped: only new1 counts as new.
    assert trace[0].leg_post_count == 2
    assert trace[0].new_post_count == 1
    assert len(result.data) == 1


# --------------------------------------------------------------------------- #
# 8. Tracing off by default; per-target trace isolation in a batch
# --------------------------------------------------------------------------- #

async def test_trace_off_keeps_behavior(monkeypatch):
    posts = [_make_post("p1", _date_unix("2025-03-01"))]
    scraper, pool = _make_scraper(
        monkeypatch, [("cursor_reset", posts), ("success", [])]
    )

    # No leg_trace argument at all: identical call contract, nothing raised.
    result = await scraper.user_timeline(
        "zuck", start_date="2025-01-01", end_date="2025-06-01",
    )
    assert result.result == "success"
    assert len(pool.submitted) == 2
    assert len(result.data) == 1


async def test_concurrent_targets_keep_independent_traces(monkeypatch):
    # Two targets in one gather()-style batch, each with its own trace list:
    # neither target's legs may land in the other's trace.
    async def _build(handle, scripted):
        scraper, _pool = _make_scraper(monkeypatch, scripted)
        return scraper

    s1 = await _build("h1", [("success", [_make_post("x1", _date_unix("2025-03-01"))])])
    s2 = await _build("h2", [
        ("cursor_reset", [_make_post("y1", _date_unix("2025-05-01"))]),
        ("success", [_make_post("y2", _date_unix("2025-04-01"))]),
    ])

    trace1: list[TimelineLegTrace] = []
    trace2: list[TimelineLegTrace] = []
    r1, r2 = await asyncio.gather(
        s1.user_timeline("h1", start_date="2025-01-01",
                         end_date="2025-06-01", leg_trace=trace1),
        s2.user_timeline("h2", start_date="2025-01-01",
                         end_date="2025-06-01", leg_trace=trace2),
    )

    assert r1.result == "success" and r2.result == "success"
    assert [e.leg_index for e in trace1] == [0]
    assert [e.leg_index for e in trace2] == [0, 1]
    assert all(isinstance(e, TimelineLegTrace) for e in trace1 + trace2)
    # The multi-leg target's reset flag is isolated to its own trace.
    assert trace1[0].cursor_reset_continued is False
    assert trace2[0].cursor_reset_continued is True
    assert trace2[1].cursor_reset_continued is False
