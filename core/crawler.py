"""Main crawler orchestrator for the GoogleMapsCrawler.

This module provides the main GoogleMapsCrawler class that orchestrates
the entire crawling process: browser management, data extraction,
processing, and output.

Usage:
    from core.models import CrawlerConfig
    from core.crawler import GoogleMapsCrawler

    config = CrawlerConfig(search_prompt="restaurants berlin")

    with GoogleMapsCrawler(config) as crawler:
        results = crawler.crawl()
"""

from __future__ import annotations

import logging
import os
import sys
from typing import TYPE_CHECKING

# Add the parent directory to sys.path so that `from core.*` imports work
# This allows running from both the root directory and from within the core directory
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from core.browser import BrowserManager
from core.extractors import MapsExtractor
from core.exceptions import CrawlerBaseException
from core.models import (
    CompanyData,
    CrawlResults,
    CrawlerConfig,
    ResultOutcome,
    ResultStatus,
)
from core.output import OutputHandler
from core.processors import DeduplicationProcessor, URLValidator

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class GoogleMapsCrawler:
    """Main crawler class that orchestrates the entire scraping process.

    This class coordinates all the components:
    1. BrowserManager - Manages the Playwright browser
    2. MapsExtractor - Extracts company data from Google Maps
    3. DeduplicationProcessor - Removes duplicate companies
    4. URLValidator - Validates website URLs
    5. OutputHandler - Formats and outputs results

    The results returned by :meth:`crawl` are a :class:`CrawlResults` list:
    a normal list of companies (same meaning and order as before) that also
    exposes per-position :attr:`CrawlResults.outcomes`.

    Usage:
        config = CrawlerConfig(search_prompt="restaurants berlin")
        crawler = GoogleMapsCrawler(config)
        crawler.start()
        results = crawler.get_results()
        crawler.close()

    Or with context manager:
        with GoogleMapsCrawler(config) as crawler:
            results = crawler.crawl()
            for outcome in results.outcomes:
                print(outcome.position, outcome.status, outcome.url)
    """

    def __init__(self, config: CrawlerConfig) -> None:
        """Initialize the GoogleMapsCrawler.

        Args:
            config: The crawler configuration.
        """
        self._config = config
        self._browser_manager: BrowserManager | None = None
        self._results: CrawlResults = CrawlResults([])
        self._outcomes: list[ResultOutcome] = []
        self._is_started = False

    @property
    def config(self) -> CrawlerConfig:
        """Get the crawler configuration.

        Returns:
            The CrawlerConfig instance.
        """
        return self._config

    @property
    def results(self) -> list[CompanyData]:
        """Get the crawl results.

        Returns:
            A list of CompanyData objects.
        """
        return self._results

    @property
    def outcomes(self) -> list[ResultOutcome]:
        """Get the per-position outcomes from the last crawl.

        Returns:
            A list of ResultOutcome objects ordered by result position.
        """
        return self._outcomes

    @property
    def is_started(self) -> bool:
        """Check if the crawler has been started.

        Returns:
            True if started, False otherwise.
        """
        return self._is_started

    def start(self) -> "GoogleMapsCrawler":
        """Start the crawler and initialize all components.

        Returns:
            self for method chaining.

        Raises:
            CrawlerBaseException: If initialization fails.
        """
        logger.info("Starting GoogleMapsCrawler for prompt: %s", self._config.search_prompt)

        # Initialize browser manager
        self._browser_manager = BrowserManager(self._config)
        self._browser_manager.initialize()
        self._is_started = True

        return self

    def crawl(self, prompt: str | None = None) -> CrawlResults:
        """Run the complete crawl process.

        This method:
        1. Navigates to Google Maps search results
        2. Extracts company data (recording one outcome per result position)
        3. Removes duplicates
        4. Filters invalid websites
        5. Outputs results

        Args:
            prompt: Optional search prompt override. Uses config if not provided.

        Returns:
            A CrawlResults list of CompanyData objects. It behaves like the
            previous plain list (same companies, same order) and additionally
            exposes ``.outcomes`` with one entry per result position.
        """
        search_prompt = prompt or self._config.search_prompt

        if not search_prompt:
            logger.error("No search prompt provided")
            self._results = CrawlResults([])
            self._outcomes = []
            return self._results

        if not self._is_started:
            self.start()

        if not self._browser_manager:
            logger.error("Browser manager not initialized")
            self._results = CrawlResults([])
            self._outcomes = []
            return self._results

        # Get track_reviews from config
        track_reviews = getattr(self._config, 'track_reviews', True)

        try:
            # Step 1: Navigate to Google Maps
            page = self._browser_manager.navigate_to_maps(search_prompt)

            # Step 2: Extract company data
            extractor = MapsExtractor(page, self._config.selector_timeout)
            extraction = extractor.extract_all(track_reviews=track_reviews)
            outcomes = list(extraction.outcomes)

            if not extraction:
                logger.warning("No companies found for prompt: %s", search_prompt)
                self._results = CrawlResults([], outcomes)
                self._outcomes = outcomes
                return self._results

            logger.info("Extracted %d raw companies", len(extraction))

            # Step 3: Remove duplicates
            deduplicator = DeduplicationProcessor()
            unique_results = deduplicator.process(extraction)

            # Step 4: Filter valid websites
            valid_results = [c for c in unique_results if URLValidator.is_valid(c.website)]

            # Reflect post-processing in the per-position outcomes
            outcomes = self._apply_post_processing_outcomes(
                outcomes, unique_results, valid_results
            )

            self._results = CrawlResults(valid_results, outcomes)
            self._outcomes = outcomes
            logger.info(
                "Crawl complete. Found %d companies with valid websites (%s)",
                len(valid_results),
                ", ".join(
                    f"{status}={count}"
                    for status, count in self._results.counts_by_status().items()
                ) or "no outcomes",
            )

            # Step 5: Output results
            output_handler = OutputHandler()
            output_handler.output(valid_results, search_prompt, self._config.output_format)

            return self._results

        except CrawlerBaseException as e:
            logger.error("Crawler error: %s", e)
            raise
        except Exception as e:
            logger.error("Unexpected error during crawl: %s", e)
            raise
        # Don't close browser here - let the user control the lifecycle

    @staticmethod
    def _apply_post_processing_outcomes(
        outcomes: list[ResultOutcome],
        unique_results: list[CompanyData],
        valid_results: list[CompanyData],
    ) -> list[ResultOutcome]:
        """Update outcomes after de-duplication and website filtering.

        Extraction outcomes are kept as-is for companies that survive both
        steps.  Companies removed by post-processing get their status changed
        so the caller can tell *why* a successfully extracted company is not
        part of the returned list:

        - removed by the deduplication processor -> ``DUPLICATE_COMPANY``,
          pointing at the position of the company that covered it;
        - no valid HTTP(S) website -> ``NO_VALID_WEBSITE``.

        Args:
            outcomes: The per-position outcomes from the extractor.
            unique_results: The list after de-duplication.
            valid_results: The list after website filtering.

        Returns:
            The updated outcomes, ordered by position.
        """
        # Map extracted company object -> its position
        position_by_company: dict[int, int] = {}
        for outcome in outcomes:
            if outcome.company is not None:
                position_by_company[id(outcome.company)] = outcome.position

        # Positions removed by the deduplicator, keyed by duplicate key
        dedup_kept: dict[tuple[str, str], int] = {}
        for company in unique_results:
            key = (company.name.strip().lower(), company.website.strip().lower())
            if key not in dedup_kept and id(company) in position_by_company:
                dedup_kept[key] = position_by_company[id(company)]

        valid_ids = {id(company) for company in valid_results}
        unique_ids = {id(company) for company in unique_results}

        for outcome in outcomes:
            company = outcome.company
            if company is None:
                # Failed/duplicate positions already have their final status.
                continue

            if id(company) not in unique_ids:
                key = (company.name.strip().lower(), company.website.strip().lower())
                covering_position = dedup_kept.get(key)
                outcome.status = ResultStatus.DUPLICATE_COMPANY
                outcome.duplicate_of = covering_position
                outcome.reason = (
                    f"Same name and website as position {covering_position}"
                    if covering_position is not None
                    else "Same name and website as an earlier company"
                )
                continue

            if id(company) not in valid_ids:
                outcome.status = ResultStatus.NO_VALID_WEBSITE
                outcome.reason = "No valid website; company filtered out"

        return sorted(outcomes, key=lambda outcome: outcome.position)

    def get_results(self) -> list[CompanyData]:
        """Get the results from the last crawl.

        Returns:
            A list of CompanyData objects with per-position outcomes.
        """
        return self._results

    def close(self) -> None:
        """Close the crawler and clean up resources."""
        logger.info("Closing GoogleMapsCrawler...")

        if self._browser_manager:
            self._browser_manager.close()

        self._is_started = False
        logger.info("GoogleMapsCrawler closed")

    def __enter__(self) -> "GoogleMapsCrawler":
        """Context manager entry."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.close()


def run_crawler(
    prompt: str,
    headless: bool = False,
    output_format: str = "json",
    track_reviews: bool = True,
) -> CrawlResults:
    """Convenience function to run a crawl with default settings.

    Args:
        prompt: The search term (e.g., "restaurants berlin").
        headless: Whether to run the browser in headless mode.
        output_format: The output format.
        track_reviews: Whether to extract reviews for each company.

    Returns:
        A CrawlResults list of CompanyData objects with per-position outcomes.
    """
    config = CrawlerConfig(
        search_prompt=prompt,
        headless=headless,
        output_format=output_format,
        track_reviews=track_reviews,
    )

    with GoogleMapsCrawler(config) as crawler:
        return crawler.crawl()
