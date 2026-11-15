"""Pagination scaffolding tests."""

from __future__ import annotations

from typing import Any

import pytest

from tikhub import (
    CursorPaginator,
    OffsetPaginator,
    Page,
    PageFailure,
    PagePaginator,
    TikHubAuthError,
    TikHubConnectionError,
    TikHubRateLimitError,
)


def _make_sync_pages(*pages: list[int]):
    state = {"i": 0}

    def fetch(_cursor: Any) -> Page[int]:
        i = state["i"]
        state["i"] = i + 1
        items = pages[i]
        last = i == len(pages) - 1
        return Page(items, next_cursor=i + 1, has_more=not last)

    return fetch


def test_cursor_paginator_iter():
    pager = CursorPaginator(sync_fetch=_make_sync_pages([1, 2], [3, 4], [5]))
    assert list(pager) == [1, 2, 3, 4, 5]


def test_cursor_paginator_first():
    pager = CursorPaginator(sync_fetch=_make_sync_pages([1, 2], [3, 4], [5, 6]))
    assert pager.first(3) == [1, 2, 3]


def test_page_paginator_initial_cursor():
    pager = PagePaginator(sync_fetch=_make_sync_pages([10]), initial_page=5)
    assert pager.cursor == 5
    assert list(pager) == [10]


def test_offset_paginator_initial_offset():
    pager = OffsetPaginator(sync_fetch=_make_sync_pages([7, 8]), initial_offset=20)
    assert pager.cursor == 20
    assert list(pager) == [7, 8]


async def test_cursor_paginator_async_iter():
    state = {"i": 0}

    async def fetch(_cursor: Any) -> Page[int]:
        i = state["i"]
        state["i"] = i + 1
        pages = ([1, 2], [3, 4], [5])
        items = pages[i]
        last = i == len(pages) - 1
        return Page(items, next_cursor=i + 1, has_more=not last)

    pager = CursorPaginator[int](async_fetch=fetch)
    out: list[int] = []
    async for item in pager:
        out.append(item)
    assert out == [1, 2, 3, 4, 5]


# ---------------------------------------------------------------------------
# Fault-tolerant mode
# ---------------------------------------------------------------------------


def _rate_limit() -> TikHubRateLimitError:
    return TikHubRateLimitError(
        "slow down", status_code=429, method="GET", url="https://api.tikhub.io/x"
    )


def _flaky_sync_fetch(pages: list[list[int]], fail_at: dict[Any, list[BaseException]]):
    """Sync fetch that raises the queued errors for a cursor before serving it."""

    def fetch(cursor: Any) -> Page[int]:
        errors = fail_at.get(cursor)
        if errors:
            raise errors.pop(0)
        i = int(cursor)
        items = pages[i]
        last = i == len(pages) - 1
        return Page(items, next_cursor=i + 1, has_more=not last)

    return fetch


def _flaky_async_fetch(pages: list[list[int]], fail_at: dict[Any, list[BaseException]]):
    async def fetch(cursor: Any) -> Page[int]:
        errors = fail_at.get(cursor)
        if errors:
            raise errors.pop(0)
        i = int(cursor)
        items = pages[i]
        last = i == len(pages) - 1
        return Page(items, next_cursor=i + 1, has_more=not last)

    return fetch


def test_tolerant_normal_multi_page():
    pager = CursorPaginator(
        sync_fetch=_flaky_sync_fetch([[1, 2], [3, 4], [5]], {}),
        tolerate_page_errors=True,
    )
    assert list(pager) == [1, 2, 3, 4, 5]
    assert pager.failures == ()


def test_tolerant_middle_page_failure_then_continues():
    pager = CursorPaginator(
        sync_fetch=_flaky_sync_fetch([[1, 2], [3, 4], [5]], {1: [_rate_limit()]}),
        tolerate_page_errors=True,
    )
    assert list(pager) == [1, 2, 3, 4, 5]

    failures = pager.failures
    assert len(failures) == 1
    failure = failures[0]
    assert isinstance(failure, PageFailure)
    assert failure.cursor == 1
    assert failure.error_type == "TikHubRateLimitError"
    assert "slow down" in failure.message
    assert failure.retryable is True
    # Cursor was preserved at the failed page, then advanced after the retry.
    assert pager.cursor == 3


def test_tolerant_stops_after_max_consecutive_failures():
    fail_at = {1: [_rate_limit(), _rate_limit(), _rate_limit()]}
    pager = CursorPaginator(
        sync_fetch=_flaky_sync_fetch([[1, 2], [3, 4], [5]], fail_at),
        tolerate_page_errors=True,
        max_consecutive_failures=3,
    )
    # Items from the first page are still delivered before iteration stops.
    assert list(pager) == [1, 2]
    assert pager.has_more is False
    assert len(pager.failures) == 3
    assert all(f.cursor == 1 for f in pager.failures)
    # Cursor still points at the failed page.
    assert pager.cursor == 1

    # Caller fixes the issue and resumes from the preserved cursor.
    pager.resume()
    assert list(pager) == [3, 4, 5]
    assert len(pager.failures) == 3  # failure log is kept across resume


def test_tolerant_non_retryable_error_stops_immediately():
    auth_error = TikHubAuthError(
        "bad key", status_code=401, method="GET", url="https://api.tikhub.io/x"
    )
    pager = CursorPaginator(
        sync_fetch=_flaky_sync_fetch([[1, 2], [3, 4]], {1: [auth_error]}),
        tolerate_page_errors=True,
        max_consecutive_failures=5,
    )
    assert list(pager) == [1, 2]
    assert pager.has_more is False
    assert len(pager.failures) == 1
    assert pager.failures[0].retryable is False
    assert pager.failures[0].error_type == "TikHubAuthError"


def test_default_mode_still_raises():
    pager = CursorPaginator(
        sync_fetch=_flaky_sync_fetch([[1, 2], [3, 4]], {1: [_rate_limit()]}),
    )
    with pytest.raises(TikHubRateLimitError):
        list(pager)
    assert pager.failures == ()


def test_tolerant_mode_does_not_swallow_non_tikhub_errors():
    pager = CursorPaginator(
        sync_fetch=_flaky_sync_fetch([[1, 2], [3, 4]], {1: [ValueError("boom")]}),
        tolerate_page_errors=True,
    )
    with pytest.raises(ValueError, match="boom"):
        list(pager)
    assert pager.failures == ()


def test_failures_is_read_only():
    pager = CursorPaginator(
        sync_fetch=_flaky_sync_fetch([[1], [2]], {0: [TikHubConnectionError("reset")]}),
        tolerate_page_errors=True,
    )
    assert list(pager) == [1, 2]
    failures = pager.failures
    assert isinstance(failures, tuple)
    with pytest.raises(AttributeError):
        failures.append("nope")  # type: ignore[attr-defined]


def test_page_paginator_records_page_number():
    pager = PagePaginator(
        sync_fetch=_flaky_sync_fetch([[1], [2], [3]], {2: [_rate_limit()]}),
        initial_page=1,
        tolerate_page_errors=True,
    )
    assert list(pager) == [2, 3]
    assert pager.failures[0].cursor == 2


def test_invalid_max_consecutive_failures():
    with pytest.raises(ValueError):
        CursorPaginator(sync_fetch=_flaky_sync_fetch([[1]], {}), max_consecutive_failures=0)


async def test_tolerant_async_middle_page_failure_then_continues():
    pager = CursorPaginator[int](
        async_fetch=_flaky_async_fetch([[1, 2], [3, 4], [5]], {1: [_rate_limit()]}),
        tolerate_page_errors=True,
    )
    out: list[int] = []
    async for item in pager:
        out.append(item)
    assert out == [1, 2, 3, 4, 5]
    assert len(pager.failures) == 1
    assert pager.failures[0].cursor == 1


async def test_tolerant_async_stops_after_max_consecutive_failures():
    fail_at = {0: [TikHubConnectionError("down"), TikHubConnectionError("down")]}
    pager = CursorPaginator[int](
        async_fetch=_flaky_async_fetch([[1, 2], [3, 4]], fail_at),
        tolerate_page_errors=True,
        max_consecutive_failures=2,
    )
    out: list[int] = []
    async for item in pager:
        out.append(item)
    assert out == []
    assert pager.has_more is False
    assert len(pager.failures) == 2

    pager.resume()
    async for item in pager:
        out.append(item)
    assert out == [1, 2, 3, 4]


async def test_default_mode_async_still_raises():
    pager = CursorPaginator[int](
        async_fetch=_flaky_async_fetch([[1, 2]], {0: [_rate_limit()]}),
    )
    with pytest.raises(TikHubRateLimitError):
        async for _ in pager:
            pass
