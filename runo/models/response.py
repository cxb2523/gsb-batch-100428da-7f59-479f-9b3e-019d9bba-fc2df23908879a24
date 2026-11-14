from typing import Any, Literal

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool


class ExtractResult(BaseModel):
    url: str
    status: Literal["success", "error"]
    render_mode: Literal["plain", "headless"] | None = None
    data: dict[str, Any] | None = None
    error: ErrorDetail | None = None
    # Optional coercion warnings — populated when a raw value was present on
    # the page but could not be coerced to the declared type (the field is
    # still nulled in ``data``, consistent with the public contract).
    # Happy-path responses serialize ``"warnings": null``; only populated
    # when there is something to report, so no extra serialization work per
    # successful call.
    warnings: list[str] | None = None
    images_processed: int | None = None  # vision pass image count; omitted when not used


class BatchResult(BaseModel):
    results: list[ExtractResult]
    total: int
    succeeded: int
    failed: int
    cancelled: bool = False


class CrawlMeta(BaseModel):
    pages_visited: int
    pages_skipped: int
    pages_failed: int
    # True when the crawl exited early because the caller cancelled it via
    # DELETE /v1/jobs/{id}. Unused reservation has already been refunded.
    cancelled: bool = False
    # Links that matched ``follow_pattern`` but were never enqueued because
    # the ``max_pages`` ceiling had already been reached. This includes
    # sitemap candidates truncated by the same ceiling (also broken out in
    # the sitemap_* counters below when use_sitemap is enabled).
    links_dropped_max_pages: int = 0
    # Links skipped because the normalized URL had already been discovered
    # or visited earlier in the same crawl. Reported separately from
    # ``pages_skipped``, which remains the robots.txt/other-policy bucket.
    pages_skipped_duplicate: int = 0
    # Sitemap coverage. Populated only when ``crawl.use_sitemap`` is enabled;
    # the counters stay at zero otherwise — and also when the sitemap cannot
    # be fetched or parsed, since a failed sitemap fetch reports zero
    # candidates rather than raising.
    #   candidates_total:      pattern-matching URLs the sitemap exposed
    #   candidates_enqueued:   of those, actually added to the crawl queue
    #   candidates_truncated:  of those, refused because max_pages was hit
    # Invariant: candidates_total = candidates_enqueued + candidates_truncated
    #            (+ sitemap duplicates, which are counted in
    #            pages_skipped_duplicate rather than any sitemap_* field).
    sitemap_candidates_total: int = 0
    sitemap_candidates_enqueued: int = 0
    sitemap_candidates_truncated: int = 0


class CrawlResult(BaseModel):
    results: list[ExtractResult]
    crawl_meta: CrawlMeta
