import logging
import random
import time
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from utils.proxy_manager import ProxyManager  # type: ignore

logger = logging.getLogger("linkedin_parser")

# Per-page outcomes recorded in a scrape report.
PAGE_OUTCOME_SUCCESS = "success"
PAGE_OUTCOME_REQUEST_FAILED = "request_failed"
PAGE_OUTCOME_EMPTY_PAGE = "empty_page"
PAGE_OUTCOME_MAX_JOBS_REACHED = "max_jobs_reached"

# Reasons why pagination stopped.
STOP_REASON_REQUEST_FAILED = "request_failed"
STOP_REASON_EMPTY_PAGE = "empty_page"
STOP_REASON_MAX_JOBS_REACHED = "max_jobs_reached"

PAGE_SIZE = 25
MAX_ERROR_SUMMARY_LENGTH = 500

@dataclass
class JobPosting:
    job_title: str
    company_name: str
    location: str
    date_posted: str
    job_description: str
    seniority_level: str
    employment_type: str
    industries: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class PageReport:
    """Outcome of a single paginated results page."""

    start: int
    job_count: int
    outcome: str
    error: Optional[str] = None
    jobs_added: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class ScrapeReport:
    """Outcome of a whole paginated scrape, including a per-page trail."""

    search_url: str
    max_jobs: int
    pages: List[PageReport] = field(default_factory=list)
    jobs: List[Dict[str, Any]] = field(default_factory=list)
    stop_reason: Optional[str] = None
    failed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "search_url": self.search_url,
            "max_jobs": self.max_jobs,
            "pages": [page.to_dict() for page in self.pages],
            "page_count": len(self.pages),
            "job_count": len(self.jobs),
            "jobs": self.jobs,
            "stop_reason": self.stop_reason,
            "failed": self.failed,
            "stopped_early_on_failure": self.failed,
        }

def _summarize_error(exc: BaseException) -> str:
    summary = f"{type(exc).__name__}: {exc}".strip()
    if len(summary) > MAX_ERROR_SUMMARY_LENGTH:
        summary = summary[: MAX_ERROR_SUMMARY_LENGTH - 3] + "..."
    return summary

class LinkedInJobScraper:
    """
    Lightweight LinkedIn job scraper.

    Note: LinkedIn frequently changes its markup and aggressively throttles bots.
    This class is written to be structurally correct and easy to extend, but
    real-world scraping should always respect robots.txt, site terms, and law.
    """

    def __init__(
        self,
        proxy_manager: Optional[ProxyManager] = None,
        max_retries: int = 3,
        request_timeout: int = 20,
        delay_range: tuple[float, float] = (0.5, 1.5),
    ) -> None:
        self.session = requests.Session()
        self.proxy_manager = proxy_manager
        self.max_retries = max_retries
        self.request_timeout = request_timeout
        self.delay_range = delay_range

    def _get_headers(self) -> Dict[str, str]:
        # Basic desktop browser header; rotate User-Agent here if desired.
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        }

    def _fetch_page(self, url: str, params: Optional[Dict[str, Any]] = None) -> str:
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                proxy = self.proxy_manager.get_proxy() if self.proxy_manager else None
                proxies = {"http": proxy, "https": proxy} if proxy else None
                logger.debug(
                    "Fetching URL (attempt %d/%d) %s via proxy %s",
                    attempt,
                    self.max_retries,
                    url,
                    proxy,
                )
                resp = self.session.get(
                    url,
                    params=params,
                    headers=self._get_headers(),
                    proxies=proxies,
                    timeout=self.request_timeout,
                )
                resp.raise_for_status()
                return resp.text
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Request attempt %d/%d failed for %s: %s",
                    attempt,
                    self.max_retries,
                    url,
                    exc,
                )
                time.sleep(random.uniform(*self.delay_range))
        assert last_exc is not None
        raise last_exc

    def _append_start_param(self, base_url: str, start: int) -> str:
        from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

        parsed = urlparse(base_url)
        query = dict(parse_qsl(parsed.query))
        query["start"] = str(start)
        new_query = urlencode(query)
        return urlunparse(parsed._replace(query=new_query))

    def scrape_search(self, search_url: str, max_jobs: int = 1000) -> List[Dict[str, Any]]:
        """
        Crawl paginated LinkedIn search results and return a list of job dicts.

        Kept for backwards compatibility, including its behaviour of raising
        when a page cannot be fetched after ``max_retries``. Use
        :meth:`scrape_search_with_report` when you also need the per-page
        outcome trail and want partial results preserved on failure.
        """
        return self._scrape(search_url, max_jobs, raise_on_page_error=True).jobs

    def scrape_search_with_report(
        self, search_url: str, max_jobs: int = 1000
    ) -> ScrapeReport:
        """
        Crawl paginated LinkedIn search results and return a full scrape report.

        Each page gets an entry in the report; page-level request failures are
        recorded (after exhausting ``max_retries``) and stop pagination instead
        of raising, so jobs collected from earlier pages are preserved.
        """
        return self._scrape(search_url, max_jobs, raise_on_page_error=False)

    def _scrape(
        self, search_url: str, max_jobs: int, raise_on_page_error: bool
    ) -> ScrapeReport:
        report = ScrapeReport(search_url=search_url, max_jobs=max_jobs)
        all_jobs = report.jobs
        start = 0

        while len(all_jobs) < max_jobs:
            paged_url = self._append_start_param(search_url, start)
            logger.info("Scraping page starting at %d: %s", start, paged_url)

            try:
                html = self._fetch_page(paged_url)
            except Exception as exc:
                summary = _summarize_error(exc)
                logger.error(
                    "Giving up on page starting at %d after %d retries: %s",
                    start,
                    self.max_retries,
                    summary,
                )
                report.pages.append(
                    PageReport(
                        start=start,
                        job_count=0,
                        outcome=PAGE_OUTCOME_REQUEST_FAILED,
                        error=summary,
                    )
                )
                report.stop_reason = STOP_REASON_REQUEST_FAILED
                report.failed = True
                if raise_on_page_error:
                    raise
                break

            page_jobs = self.parse_listings(html)
            if not page_jobs:
                logger.info("No further jobs found at start=%d; stopping pagination.", start)
                report.pages.append(
                    PageReport(
                        start=start,
                        job_count=0,
                        outcome=PAGE_OUTCOME_EMPTY_PAGE,
                    )
                )
                report.stop_reason = STOP_REASON_EMPTY_PAGE
                break

            added = 0
            for job in page_jobs:
                if len(all_jobs) >= max_jobs:
                    break
                all_jobs.append(job.to_dict())
                added += 1

            hit_limit = len(all_jobs) >= max_jobs
            report.pages.append(
                PageReport(
                    start=start,
                    job_count=len(page_jobs),
                    jobs_added=added,
                    outcome=(
                        PAGE_OUTCOME_MAX_JOBS_REACHED
                        if hit_limit
                        else PAGE_OUTCOME_SUCCESS
                    ),
                )
            )

            logger.info(
                "Accumulated %d jobs so far (page yielded %d).",
                len(all_jobs),
                len(page_jobs),
            )

            if hit_limit:
                report.stop_reason = STOP_REASON_MAX_JOBS_REACHED
                break

            start += PAGE_SIZE
            time.sleep(random.uniform(*self.delay_range))

        if report.stop_reason is None:
            # max_jobs was already satisfied before the first request.
            report.stop_reason = STOP_REASON_MAX_JOBS_REACHED

        return report

    def parse_listings(self, html: str) -> List[JobPosting]:
        """
        Parse a LinkedIn jobs search HTML page into JobPosting objects.

        The method is intentionally defensive: it prefers getting partial data
        over failing completely when markup changes.
        """
        soup = BeautifulSoup(html, "lxml")

        # Try common LinkedIn job card containers.
        cards = soup.select("li.jobs-search-results__list-item")
        if not cards:
            cards = soup.select("div.base-card")

        jobs: List[JobPosting] = []
        for card in cards:
            job = self._parse_single_card(card)
            if job:
                jobs.append(job)
        return jobs

    def _parse_single_card(self, card: Any) -> Optional[JobPosting]:
        def safe_text(selector: str) -> str:
            el = card.select_one(selector)
            return el.get_text(strip=True) if el else ""

        # Title
        title = (
            safe_text("a.job-card-list__title")
            or safe_text("a.base-card__full-link")
            or safe_text("h3.base-search-card__title")
            or safe_text("h3")
        )

        if not title:
            # Card is probably malformed or an ad; skip.
            return None

        company = (
            safe_text("a.job-card-container__company-name")
            or safe_text("a.base-search-card__subtitle")
            or safe_text("span.job-card-container__primary-description")
        )

        location = (
            safe_text("span.job-card-container__metadata-item")
            or safe_text("span.job-search-card__location")
        )

        date_posted = (
            safe_text("time")
            or safe_text("div.job-card-container__listed-time")
            or safe_text("div.job-search-card__listdate")
        )

        description = ""
        description_el = card.select_one("div.job-card-list__insight")
        if description_el:
            description = description_el.get_text(" ", strip=True)

        # These fields are often not on the list view; leave blank if missing.
        seniority_level = safe_text("span.job-card-container__metadata-item--seniority")
        employment_type = safe_text(
            "span.job-card-container__metadata-item--employment-type"
        )

        industries: List[str] = []
        industries_el = card.select_one("ul.job-card-container__industry-list")
        if industries_el:
            industries = [
                li.get_text(strip=True) for li in industries_el.select("li") if li
            ]

        return JobPosting(
            job_title=title,
            company_name=company,
            location=location,
            date_posted=date_posted,
            job_description=description,
            seniority_level=seniority_level,
            employment_type=employment_type,
            industries=industries,
        )