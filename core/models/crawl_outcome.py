"""Result outcome tracking for crawl runs.

While a crawl is running, every position in the Google Maps result list can
end in a different way: it can be extracted successfully, its detail page can
fail to load, its reviews can time out, it can be a duplicate of an earlier
position, or it can be filtered out because it has no usable website.

This module defines the small data model used to report those per-position
outcomes to the caller.  The regular company list returned by
``GoogleMapsCrawler.crawl()`` is a :class:`CrawlResults` instance, which is a
plain ``list`` subclass with an additional ``outcomes`` attribute.  That keeps
the existing list semantics (length, indexing, iteration and order) intact
while making the outcome information available alongside the results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from .company import CompanyData


class ResultStatus(str, Enum):
    """The possible outcomes for a single result-list position.

    Attributes:
        SUCCESS: The company was extracted successfully.
        DETAIL_PAGE_FAILED: The detail page could not be loaded, or the
            company name could not be found on it.
        REVIEWS_TIMEOUT: The detail page loaded, but the reviews did not
            appear/load within the configured timeout.  The company itself is
            still returned, just without reviews.
        DUPLICATE: Another position with the same normalized place link was
            already processed successfully; this position was not fetched
            again.  ``ResultOutcome.duplicate_of`` points at the covering
            position.
        NO_VALID_WEBSITE: The company was extracted but filtered out because
            it has no valid HTTP(S) website.
        DUPLICATE_COMPANY: The company was removed by the post-processing
            de-duplication (same name and website as an earlier company).
    """

    SUCCESS = "success"
    DETAIL_PAGE_FAILED = "detail_page_failed"
    REVIEWS_TIMEOUT = "reviews_timeout"
    DUPLICATE = "duplicate"
    NO_VALID_WEBSITE = "no_valid_website"
    DUPLICATE_COMPANY = "duplicate_company"

    def __str__(self) -> str:
        """Return the plain string value (useful in logs)."""
        return self.value


@dataclass
class ResultOutcome:
    """The outcome of processing one position in the result list.

    Attributes:
        position: Zero-based position of the result in the Google Maps feed.
        status: What happened at this position.
        url: The place link of this position (kept for locating failures).
        name: The company name when known, otherwise a name derived from the
            place link (may be empty).
        reason: Human-readable detail about why this outcome was recorded.
        duplicate_of: For duplicates, the position that was processed and
            covers this entry.
        company: The extracted company object, when one was produced.
    """

    position: int
    status: ResultStatus
    url: str = ""
    name: str = ""
    reason: str = ""
    duplicate_of: int | None = None
    company: "CompanyData | None" = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict:
        """Convert the outcome to a JSON-friendly dictionary.

        The company object itself is not serialized; its position in the
        returned company list can be derived from ``position`` when needed.

        Returns:
            A dictionary representation of the outcome.
        """
        return {
            "position": self.position,
            "status": self.status.value,
            "url": self.url,
            "name": self.name,
            "reason": self.reason,
            "duplicate_of": self.duplicate_of,
        }


class CrawlResults(list["CompanyData"]):
    """A list of companies that also carries per-position crawl outcomes.

    This is a normal ``list`` of :class:`CompanyData` objects (same meaning
    and order as before), with an extra ``outcomes`` attribute.  Callers that
    only care about the companies can keep using it exactly like a list.

    Usage:
        results = crawler.crawl()
        len(results)                  # number of accepted companies
        results[0].name               # first accepted company
        for outcome in results.outcomes:
            print(outcome.position, outcome.status, outcome.url)
    """

    def __init__(
        self,
        companies: Iterable["CompanyData"] = (),
        outcomes: Iterable[ResultOutcome] | None = None,
    ) -> None:
        """Initialize the results list.

        Args:
            companies: The accepted companies, in output order.
            outcomes: Per-position outcomes, ordered by position.
        """
        super().__init__(companies)
        self.outcomes: list[ResultOutcome] = list(outcomes or [])

    def outcomes_by_position(self) -> dict[int, ResultOutcome]:
        """Map result positions to their outcomes.

        Returns:
            A dictionary keyed by the zero-based result position.
        """
        return {outcome.position: outcome for outcome in self.outcomes}

    def counts_by_status(self) -> dict[str, int]:
        """Count outcomes per status.

        Returns:
            A dictionary mapping status values to their occurrence counts.
        """
        counts: dict[str, int] = {}
        for outcome in self.outcomes:
            key = outcome.status.value
            counts[key] = counts.get(key, 0) + 1
        return counts

    def to_dict(self) -> dict:
        """Convert companies and outcomes to a JSON-friendly dictionary.

        Returns:
            A dictionary with ``companies`` and ``outcomes`` lists.
        """
        return {
            "companies": [company.to_dict() for company in self],
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
        }
