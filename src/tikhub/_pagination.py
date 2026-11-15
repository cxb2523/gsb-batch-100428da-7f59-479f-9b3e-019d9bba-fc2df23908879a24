"""Pagination helpers.

The TikHub API uses three pagination patterns:

* Cursor / max_cursor + has_more — most Douyin & TikTok user-post listings.
* Page + count — TikTok web search, Bilibili comments.
* Offset + limit — Instagram, Reddit feeds.

This module ships one paginator class per pattern, each implementing both
sync (``__iter__``) and async (``__aiter__``) protocols. Resource methods
that return paginated data return one of these objects instead of a single
model — callers can iterate, call ``first(n)``, or drive page-by-page.

Phase 0 ships the public surface; resource methods will plug their request
factories in during Phase 2.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Iterator
from dataclasses import dataclass
from typing import (
    Any,
    Callable,
    Generic,
    TypeVar,
)

from tikhub._errors import TikHubError
from tikhub._retries import is_retryable_error

T = TypeVar("T")


@dataclass(frozen=True)
class PageFailure:
    """Record of a single failed page fetch in fault-tolerant mode.

    ``cursor`` is the page number / cursor / offset that was being fetched
    when the error was raised (depending on the paginator type).
    """

    cursor: Any
    error_type: str
    message: str
    retryable: bool


class _PaginatorBase(Generic[T]):
    """Common surface for sync + async paginators.

    Subclasses implement ``_advance`` (sync) or ``_aadvance`` (async),
    which returns one page worth of items and updates internal state.

    When ``tolerate_page_errors`` is enabled, a :class:`~tikhub.TikHubError`
    raised while fetching a single page no longer terminates iteration:
    the failure is recorded (see :attr:`failures`), the cursor is left
    pointing at the failed page, and the page is retried on the next
    iteration step. Iteration stops once ``max_consecutive_failures``
    consecutive failures are hit or a non-retryable error occurs — items
    already yielded remain consumed, and the caller can fix the underlying
    issue and call :meth:`resume` to continue from the preserved cursor.
    Non-TikHub exceptions always propagate.
    """

    def __init__(
        self,
        *,
        tolerate_page_errors: bool = False,
        max_consecutive_failures: int = 3,
    ) -> None:
        if max_consecutive_failures < 1:
            raise ValueError("max_consecutive_failures must be >= 1")
        self.has_more: bool = True
        self.tolerate_page_errors: bool = tolerate_page_errors
        self.max_consecutive_failures: int = max_consecutive_failures
        self._failures: list[PageFailure] = []
        self._consecutive_failures: int = 0

    @property
    def failures(self) -> tuple[PageFailure, ...]:
        """Read-only record of every page that failed to fetch, in order."""
        return tuple(self._failures)

    def resume(self) -> None:
        """Re-arm iteration after fault-tolerant mode stopped on page failures.

        The cursor still points at the first failed page, so iterating again
        picks up exactly where the previous run stopped. Resets the
        consecutive-failure counter; the :attr:`failures` log is kept.
        """
        self.has_more = True
        self._consecutive_failures = 0

    def _record_page_failure(self, cursor: Any, exc: TikHubError) -> None:
        """Log a failed page and decide whether iteration may continue."""
        self._failures.append(
            PageFailure(
                cursor=cursor,
                error_type=type(exc).__name__,
                message=str(exc),
                retryable=is_retryable_error(exc),
            )
        )
        self._consecutive_failures += 1
        if (
            not is_retryable_error(exc)
            or self._consecutive_failures >= self.max_consecutive_failures
        ):
            self.has_more = False

    # ------------------------------------------------------------------
    # Sync iteration
    # ------------------------------------------------------------------

    def _advance(self) -> list[T]:  # pragma: no cover - subclass impl
        raise NotImplementedError

    def __iter__(self) -> Iterator[T]:
        while self.has_more:
            yield from self._advance()

    def first(self, n: int) -> list[T]:
        """Eagerly collect at most ``n`` items."""
        out: list[T] = []
        for item in self:
            out.append(item)
            if len(out) >= n:
                break
        return out

    # ------------------------------------------------------------------
    # Async iteration
    # ------------------------------------------------------------------

    async def _aadvance(self) -> list[T]:  # pragma: no cover - subclass impl
        raise NotImplementedError

    def __aiter__(self) -> AsyncIterator[T]:
        return self._aiter()

    async def _aiter(self) -> AsyncIterator[T]:
        while self.has_more:
            page = await self._aadvance()
            for item in page:
                yield item

    async def afirst(self, n: int) -> list[T]:
        """Async equivalent of :meth:`first`."""
        out: list[T] = []
        async for item in self:
            out.append(item)
            if len(out) >= n:
                break
        return out


# ---------------------------------------------------------------------------
# Cursor paginator
# ---------------------------------------------------------------------------

PageFn = Callable[[Any], "Page[T]"]
AsyncPageFn = Callable[[Any], Awaitable["Page[T]"]]


class Page(Generic[T]):
    """Result of one page fetch — items plus the cursor for the next call."""

    __slots__ = ("has_more", "items", "next_cursor")

    def __init__(
        self,
        items: list[T],
        *,
        next_cursor: Any = None,
        has_more: bool = False,
    ) -> None:
        self.items = items
        self.next_cursor = next_cursor
        self.has_more = has_more


class CursorPaginator(_PaginatorBase[T]):
    """Generic cursor pager. Resources supply a sync or async page-fetch callable."""

    def __init__(
        self,
        *,
        sync_fetch: PageFn[T] | None = None,
        async_fetch: AsyncPageFn[T] | None = None,
        initial_cursor: Any = 0,
        tolerate_page_errors: bool = False,
        max_consecutive_failures: int = 3,
    ) -> None:
        super().__init__(
            tolerate_page_errors=tolerate_page_errors,
            max_consecutive_failures=max_consecutive_failures,
        )
        if sync_fetch is None and async_fetch is None:
            raise ValueError("CursorPaginator needs at least one of sync_fetch / async_fetch")
        self._sync_fetch = sync_fetch
        self._async_fetch = async_fetch
        self.cursor: Any = initial_cursor

    def _advance(self) -> list[T]:
        if self._sync_fetch is None:
            raise RuntimeError("This paginator is async-only.")
        try:
            page = self._sync_fetch(self.cursor)
        except TikHubError as exc:
            if not self.tolerate_page_errors:
                raise
            self._record_page_failure(self.cursor, exc)
            return []
        self.cursor = page.next_cursor
        self.has_more = page.has_more
        self._consecutive_failures = 0
        return page.items

    async def _aadvance(self) -> list[T]:
        if self._async_fetch is None:
            raise RuntimeError("This paginator is sync-only.")
        try:
            page = await self._async_fetch(self.cursor)
        except TikHubError as exc:
            if not self.tolerate_page_errors:
                raise
            self._record_page_failure(self.cursor, exc)
            return []
        self.cursor = page.next_cursor
        self.has_more = page.has_more
        self._consecutive_failures = 0
        return page.items


class PagePaginator(CursorPaginator[T]):
    """Page-number pager. Same surface as :class:`CursorPaginator`, just named for clarity."""

    def __init__(
        self,
        *,
        sync_fetch: PageFn[T] | None = None,
        async_fetch: AsyncPageFn[T] | None = None,
        initial_page: int = 1,
        tolerate_page_errors: bool = False,
        max_consecutive_failures: int = 3,
    ) -> None:
        super().__init__(
            sync_fetch=sync_fetch,
            async_fetch=async_fetch,
            initial_cursor=initial_page,
            tolerate_page_errors=tolerate_page_errors,
            max_consecutive_failures=max_consecutive_failures,
        )


class OffsetPaginator(CursorPaginator[T]):
    """Offset/limit pager. Identical to :class:`CursorPaginator`, but cursor is the offset."""

    def __init__(
        self,
        *,
        sync_fetch: PageFn[T] | None = None,
        async_fetch: AsyncPageFn[T] | None = None,
        initial_offset: int = 0,
        tolerate_page_errors: bool = False,
        max_consecutive_failures: int = 3,
    ) -> None:
        super().__init__(
            sync_fetch=sync_fetch,
            async_fetch=async_fetch,
            initial_cursor=initial_offset,
            tolerate_page_errors=tolerate_page_errors,
            max_consecutive_failures=max_consecutive_failures,
        )
