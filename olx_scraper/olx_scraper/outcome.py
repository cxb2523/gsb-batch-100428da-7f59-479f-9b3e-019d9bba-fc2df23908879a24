# -*- coding: utf-8 -*-
"""Per-crawl outcome accounting.

Every crawl is expected to leave a machine readable record so that a caller can
tell the difference between these three situations:

* ads were found and processed (``status == "success"``),
* the list page was valid but contained no ads (``status == "success"`` with
  ``list_page_empty == True``),
* the list page could not be understood (``status == "failed"`` with
  ``failure_reason`` set).

The same object also records how many individual ads were skipped or degraded
and how many documents the MongoDB pipeline wrote, inserted, updated or failed.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Mapping

STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"

# Reasons recorded in ``CrawlOutcome.skip_reasons``.
SKIP_PRICE_UNPARSEABLE = "price_unparseable"
SKIP_MISSING_DETAIL_URL = "missing_detail_url"
SKIP_DETAIL_REQUEST_FAILED = "detail_request_failed"


@dataclass
class CrawlOutcome:
    """Audit trail for a single spider run."""

    ads_seen: int = 0
    ads_yielded: int = 0
    ads_skipped: int = 0
    ads_filtered_below_min_price: int = 0
    skip_reasons: Counter[str] = field(default_factory=Counter)

    detail_missing_phone: int = 0
    detail_missing_description: int = 0

    items_written: int = 0
    items_inserted: int = 0
    items_updated: int = 0
    items_write_failed: int = 0
    pipeline_active: bool = False
    pipeline_disabled: bool = False

    list_page_empty: bool = False
    failure_reason: str | None = None
    failure_message: str | None = None
    finish_reason: str | None = None

    # ------------------------------------------------------------------ ads
    def record_ad_seen(self) -> None:
        self.ads_seen += 1

    def record_skip(self, reason: str) -> None:
        self.ads_skipped += 1
        self.skip_reasons[reason] += 1

    def record_filtered(self) -> None:
        self.ads_filtered_below_min_price += 1

    def record_ad_yielded(self) -> None:
        self.ads_yielded += 1

    def record_missing_phone(self) -> None:
        self.detail_missing_phone += 1

    def record_missing_description(self) -> None:
        self.detail_missing_description += 1

    def record_empty_list_page(self) -> None:
        self.list_page_empty = True

    def record_finish_reason(self, reason: str | None) -> None:
        self.finish_reason = reason

    def fail(self, reason: str, message: str | None = None) -> None:
        """Mark the crawl as failed (the first failure wins)."""
        if self.failure_reason is None:
            self.failure_reason = reason
            self.failure_message = message

    # ---------------------------------------------------------------- writes
    def record_item_written(self) -> None:
        self.items_written += 1

    def record_item_inserted(self) -> None:
        self.items_written += 1
        self.items_inserted += 1

    def record_item_updated(self) -> None:
        self.items_written += 1
        self.items_updated += 1

    def record_write_failed(self) -> None:
        self.items_write_failed += 1

    def record_pipeline_active(self) -> None:
        self.pipeline_active = True

    def record_pipeline_disabled(self) -> None:
        self.pipeline_disabled = True

    # --------------------------------------------------------------- summary
    @property
    def failed(self) -> bool:
        return self.failure_reason is not None

    @property
    def status(self) -> str:
        return STATUS_FAILED if self.failed else STATUS_SUCCESS

    def snapshot(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "failure_reason": self.failure_reason,
            "failure_message": self.failure_message,
            "finish_reason": self.finish_reason,
            "list_page_empty": self.list_page_empty,
            "ads_seen": self.ads_seen,
            "ads_yielded": self.ads_yielded,
            "ads_skipped": self.ads_skipped,
            "ads_filtered_below_min_price": self.ads_filtered_below_min_price,
            "skip_reasons": dict(sorted(self.skip_reasons.items())),
            "detail_missing_phone": self.detail_missing_phone,
            "detail_missing_description": self.detail_missing_description,
            "items_written": self.items_written,
            "items_inserted": self.items_inserted,
            "items_updated": self.items_updated,
            "items_write_failed": self.items_write_failed,
            "pipeline_active": self.pipeline_active,
            "pipeline_disabled": self.pipeline_disabled,
        }

    def summary_json(self) -> str:
        return json.dumps(self.snapshot(), sort_keys=True)

    def as_stats(self) -> dict[str, Any]:
        """Flatten the outcome into Scrapy stats friendly keys."""
        snap = self.snapshot()
        stats: dict[str, Any] = {
            "olx/crawl/status": snap["status"],
            "olx/crawl/failure_reason": snap["failure_reason"],
            "olx/crawl/failure_message": snap["failure_message"],
            "olx/list_page/empty": snap["list_page_empty"],
            "olx/ads/seen": snap["ads_seen"],
            "olx/ads/yielded": snap["ads_yielded"],
            "olx/ads/skipped": snap["ads_skipped"],
            "olx/ads/filtered_below_min_price": snap[
                "ads_filtered_below_min_price"
            ],
            "olx/detail/missing_phone": snap["detail_missing_phone"],
            "olx/detail/missing_description": snap["detail_missing_description"],
            "olx/items/written": snap["items_written"],
            "olx/items/inserted": snap["items_inserted"],
            "olx/items/updated": snap["items_updated"],
            "olx/items/write_failed": snap["items_write_failed"],
            "olx/pipeline/active": snap["pipeline_active"],
            "olx/pipeline/disabled": snap["pipeline_disabled"],
            "olx/crawl/finish_reason": snap["finish_reason"],
        }
        for reason, count in snap["skip_reasons"].items():
            stats["olx/ads/skip/%s" % reason] = count
        return stats

    def to_dict(self) -> Mapping[str, Any]:
        return self.snapshot()


__all__ = [
    "CrawlOutcome",
    "STATUS_FAILED",
    "STATUS_SUCCESS",
    "SKIP_DETAIL_REQUEST_FAILED",
    "SKIP_MISSING_DETAIL_URL",
    "SKIP_PRICE_UNPARSEABLE",
]
