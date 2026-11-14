#!/usr/bin/env python

"""
Batch collection result models for crawl4weibo
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Generic, TypeVar

T = TypeVar("T")


class PageFailureReason(str, Enum):
    """Classification of why a single page failed during batch collection"""

    BLOCKED_432 = "blocked_432"
    """Request was blocked by Weibo's 432 anti-crawler protection"""

    TIMEOUT = "timeout"
    """Request timed out"""

    EMPTY_PARSE = "empty_parse"
    """Response parsed to empty or malformed data"""

    NETWORK_ERROR = "network_error"
    """Other network-level failure"""

    UNKNOWN = "unknown"
    """Failure could not be classified"""


@dataclass
class PageFailure:
    """Record of a single failed page during batch collection"""

    page: int
    """1-based page number that failed"""

    reason: PageFailureReason
    """Classified failure reason"""

    message: str = ""
    """Original error message for debugging"""


@dataclass
class BatchResult(Generic[T]):
    """
    Structured result of a batch (multi-page) collection.

    Returned by the ``*_with_result`` variants of the batch collection
    methods. Successfully collected items are always preserved, even when
    some pages failed.
    """

    items: list[T] = field(default_factory=list)
    """All successfully collected items, in page order"""

    success_pages: int = 0
    """Number of pages fetched successfully"""

    failed_pages: list[PageFailure] = field(default_factory=list)
    """Per-page failure records (page number, reason, message)"""

    stop_reason: str = "exhausted"
    """Why pagination stopped: 'count_reached', 'max_pages', 'exhausted'
    or 'max_consecutive_failures'"""

    @property
    def failed_page_count(self) -> int:
        """Number of pages that failed"""
        return len(self.failed_pages)

    @property
    def total_pages_attempted(self) -> int:
        """Total pages attempted (successful + failed)"""
        return self.success_pages + len(self.failed_pages)

    @property
    def has_failures(self) -> bool:
        """Whether any page failed during collection"""
        return len(self.failed_pages) > 0

    @property
    def reached_max_pages(self) -> bool:
        """Whether collection stopped because max_pages was reached"""
        return self.stop_reason == "max_pages"

    @property
    def exhausted(self) -> bool:
        """Whether collection stopped because data was exhausted
        (no more pages or target count reached)"""
        return self.stop_reason in ("exhausted", "count_reached")

    @property
    def finished_normally(self) -> bool:
        """Whether collection ended normally (target count reached,
        max_pages reached, or data exhausted) rather than being aborted
        by consecutive page failures"""
        return self.stop_reason in ("count_reached", "max_pages", "exhausted")

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Iterator[T]:
        return iter(self.items)
