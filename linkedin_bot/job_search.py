"""Job search, pagination, and listing extraction from LinkedIn.

Validates job_id extraction with UUID fallback (BUG-MED-2),
reduces CC by extracting helper functions.
"""

import uuid
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlencode

from playwright.async_api import ElementHandle, Page

from linkedin_bot.browser import human_delay, scroll_element
from linkedin_bot.logger import get_logger

log = get_logger("job_search")

JOBS_BASE_URL = "https://www.linkedin.com/jobs/search/?"

# Prefix for identifiers of cards whose details could not be extracted.
# Real LinkedIn job_ids are numeric, so this prefix keeps placeholders
# distinguishable from genuine job_ids.
FAILED_CARD_ID_PREFIX = "failed-card-"

EXPERIENCE_MAP = {1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6"}
DATE_POSTED_MAP = {1: "r86400", 2: "r604800", 3: "r2592000"}

# Selectors (multi-fallback for resilience against LinkedIn UI changes)
_JOB_LIST_SELECTORS = [
    ".scaffold-layout__list-container",
    ".jobs-search-results-list",
    '[class*="jobs-search-results"]',
    ".scaffold-layout__list",
    ".scaffold-layout__list-detail-container",
    '[class*="scaffold-layout__list"]',
]

_JOB_CARD_SELECTORS = [
    ".scaffold-layout__list-container li.ember-view",
    "li.ember-view.jobs-search-results__list-item",
    ".job-card-container",
    "[data-occludable-job-id]",
    "li.jobs-search-results__list-item",
    ".scaffold-layout__list-container .artdeco-list__item",
    "li[data-occludable-entity-urn]",
]

_TITLE_SELECTORS = [
    ".job-details-jobs-unified-top-card__job-title",
    ".jobs-unified-top-card__job-title",
    'h1 a[data-tracking-control-name="public_jobs_topcard-title"]',
    ".t-24.job-details-jobs-unified-top-card__job-title",
]

_COMPANY_SELECTORS = [
    ".job-details-jobs-unified-top-card__company-name",
    ".jobs-unified-top-card__company-name",
    ".job-details-jobs-unified-top-card__primary-description-without-tagline a",
]

_DESCRIPTION_SELECTORS = [
    ".jobs-description__content",
    ".jobs-box__html-content",
    "#job-details",
]


@dataclass(frozen=True, slots=True)
class JobListing:
    """Represents a single job listing extracted from LinkedIn."""

    job_id: str
    title: str
    company: str
    location: str
    description: str
    url: str
    is_easy_apply: bool
    already_applied: bool


class PageOutcome(StrEnum):
    """Verifiable end state of scraping one search results page.

    COMPLETE: every card on the page was extracted successfully.
    PARTIAL_FAILURE: the container appeared but one or more cards failed
        extraction; any successful listings are still returned, and the
        all-cards-failed case is reported here with zero successes.
    CONTAINER_MISSING: the results container never appeared, so the page
        could not be scraped at all.
    """

    COMPLETE = "complete"
    PARTIAL_FAILURE = "partial_failure"
    CONTAINER_MISSING = "container_missing"


@dataclass(frozen=True, slots=True)
class FailedCard:
    """A card whose detail extraction failed.

    Attributes:
        position: Zero-based position of the card on the page.
        identifier: Placeholder identifier (FAILED_CARD_ID_PREFIX-based),
            never a real numeric job_id.
        error: String form of the exception that aborted extraction.
    """

    position: int
    identifier: str
    error: str


@dataclass(frozen=True, slots=True)
class PageExtractionResult:
    """Outcome of scraping one search results page.

    Attributes:
        outcome: Page-level end state (complete / partial / missing).
        listings: Successfully extracted JobListing objects. Failed cards
            are never represented here, so no failure can be mistaken for
            a real job downstream.
        expected_card_count: Number of cards found on the page before
            extraction (the number that should have been processed).
        success_count: Number of successfully extracted cards.
        failure_count: Number of cards whose extraction raised or returned
            no listing.
        failed_cards: Per-card failure details with positions/identifiers.
    """

    outcome: PageOutcome
    listings: tuple[JobListing, ...] = ()
    expected_card_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    failed_cards: tuple[FailedCard, ...] = ()

    @property
    def has_success(self) -> bool:
        """True when at least one card was extracted successfully."""
        return self.success_count > 0


def build_search_url(
    keyword: str,
    location: str = "",
    remote_only: bool = True,
    experience_levels: list[int] | None = None,
    date_posted: int = 2,
    easy_apply_only: bool = True,
    start: int = 0,
) -> str:
    """Build a LinkedIn jobs search URL with filters.

    Args:
        keyword: Job search keyword.
        location: Location filter.
        remote_only: Filter for remote positions only.
        experience_levels: List of experience level codes.
        date_posted: Date posted filter code (1, 2, or 3).
        easy_apply_only: Filter for Easy Apply jobs only.
        start: Pagination offset (0, 25, 50, ...).

    Returns:
        Full search URL with query parameters.
    """
    params: dict[str, str] = {
        "keywords": keyword,
        "origin": "JOB_SEARCH_PAGE_JOB_FILTER",
        "sortBy": "DD",
        "start": str(start),
    }

    if location:
        params["location"] = location
    if remote_only:
        params["f_WT"] = "2"
    if easy_apply_only:
        params["f_AL"] = "true"
    if experience_levels:
        params["f_E"] = ",".join(EXPERIENCE_MAP.get(lvl, str(lvl)) for lvl in experience_levels)
    if date_posted in DATE_POSTED_MAP:
        params["f_TPR"] = DATE_POSTED_MAP[date_posted]

    return JOBS_BASE_URL + urlencode(params)


async def _find_with_fallback(page: Page, selectors: list[str]) -> str | None:
    """Try multiple selectors and return the text of the first match.

    Args:
        page: Playwright page instance.
        selectors: List of CSS selectors to try in order.

    Returns:
        Inner text of the first matching element, or None.
    """
    for selector in selectors:
        el = await page.query_selector(selector)
        if el:
            return str((await el.inner_text()).strip())
    return None


async def _scroll_job_list(page: Page) -> None:
    """Scroll the job results sidebar to trigger lazy loading.

    Args:
        page: Playwright page instance.
    """
    for selector in _JOB_LIST_SELECTORS:
        el = await page.query_selector(selector)
        if el:
            for _ in range(5):
                await scroll_element(page, selector, 600)
            return

    for _ in range(5):
        await page.evaluate("window.scrollBy(0, 500)")
        await human_delay(0.5, 1.0)


async def _wait_for_results(page: Page) -> bool:
    """Wait for job results container to appear.

    Args:
        page: Playwright page instance.

    Returns:
        True if results container was found.
    """
    for selector in _JOB_LIST_SELECTORS:
        try:
            await page.wait_for_selector(selector, timeout=15_000)
            log.debug("results_container_found", selector=selector)
            return True
        except Exception:
            continue

    # Last resort: check if ANY job cards exist directly
    await human_delay(3.0, 5.0)
    for card_sel in _JOB_CARD_SELECTORS:
        cards = await page.query_selector_all(card_sel)
        if cards:
            log.debug("results_found_via_cards", selector=card_sel)
            return True

    return False


async def _extract_single_card(page: Page, card: ElementHandle) -> JobListing | None:
    """Extract job details from a single card element.

    Args:
        page: Playwright page instance.
        card: Playwright element handle for the job card.

    Returns:
        JobListing if extraction succeeded, None otherwise.
    """
    # Check for "Applied" badge BEFORE clicking
    already_applied = False
    applied_badge = await card.query_selector(
        '[class*="applied"], .job-card-container__footer-item--applied'
    )
    if applied_badge:
        already_applied = True

    # Click the card to load details in the right panel
    await card.click()
    await human_delay(1.5, 2.5)

    title = await _find_with_fallback(page, _TITLE_SELECTORS) or "Unknown"
    company = await _find_with_fallback(page, _COMPANY_SELECTORS) or "Unknown"

    location = ""
    location_el = await page.query_selector(
        ".job-details-jobs-unified-top-card__primary-description-container"
    )
    if location_el:
        location = (await location_el.inner_text()).strip()

    description = await _extract_description(page)

    easy_apply_btn = await page.query_selector(
        "button.jobs-apply-button, "
        'button[aria-label*="Easy Apply"], '
        'button:has-text("Easy Apply")'
    )
    is_easy_apply = easy_apply_btn is not None

    if not already_applied:
        applied_text = await page.query_selector(
            'span:has-text("Applied"), .artdeco-inline-feedback:has-text("Applied")'
        )
        if applied_text:
            already_applied = True

    job_id = _extract_job_id(page.url)

    return JobListing(
        job_id=job_id,
        title=title,
        company=company,
        location=location,
        description=description,
        url=page.url,
        is_easy_apply=is_easy_apply,
        already_applied=already_applied,
    )


async def _extract_description(page: Page) -> str:
    """Extract job description from the details panel.

    Args:
        page: Playwright page instance.

    Returns:
        Job description text, truncated to 3000 chars.
    """
    for sel in _DESCRIPTION_SELECTORS:
        desc_el = await page.query_selector(sel)
        if desc_el:
            return str((await desc_el.inner_text()).strip()[:3000])
    return ""


def _card_placeholder_id(card: ElementHandle, position: int) -> str:
    """Build a placeholder identifier for a card that failed extraction.

    Uses the card's own job-id attributes when readable so failures can be
    cross-checked against the page, otherwise falls back to the card
    position. The result always carries FAILED_CARD_ID_PREFIX, so it can
    never be confused with a real numeric LinkedIn job_id.

    Args:
        card: Playwright element handle for the failed job card.
        position: Zero-based position of the card on the page.

    Returns:
        Placeholder identifier string, distinguishable from real job_ids.
    """
    raw_marker: str | None = None
    try:
        raw_marker = card.get_attribute("data-occludable-job-id")
        if not raw_marker:
            raw_marker = card.get_attribute("data-job-id")
        if not raw_marker:
            urn = card.get_attribute("data-occludable-entity-urn")
            if urn:
                # urn:li:jobPosting:1234567890 -> 1234567890
                raw_marker = urn.rsplit(":", 1)[-1]
    except Exception as attr_exc:
        log.debug("failed_card_attribute_read", position=position, error=str(attr_exc))

    marker = raw_marker.strip() if raw_marker and raw_marker.strip() else f"pos-{position}"
    return f"{FAILED_CARD_ID_PREFIX}{marker}"


async def get_job_listings(page: Page, max_jobs: int = 25) -> PageExtractionResult:
    """Extract job listings from the current search results page.

    Scrolls to load lazy-loaded cards, extracts details for each, and
    detects if the user has already applied. Every card is processed
    independently: a failure on one card is recorded but never aborts
    the page, never removes already-extracted listings, and is never
    turned into a fake successful listing.

    Args:
        page: Playwright page instance (on a search results page).
        max_jobs: Maximum number of jobs to extract per page.

    Returns:
        PageExtractionResult carrying the page outcome (complete,
        partial failure, or missing results container), the successful
        listings, and expected/success/failure counts with per-card
        failure identifiers.
    """
    await human_delay(2.0, 4.0)

    if not await _wait_for_results(page):
        log.warning("no_job_results_container")
        return PageExtractionResult(outcome=PageOutcome.CONTAINER_MISSING)

    await _scroll_job_list(page)

    # Find job cards with fallback selectors
    job_cards: list[ElementHandle] = []
    for selector in _JOB_CARD_SELECTORS:
        job_cards = await page.query_selector_all(selector)
        if job_cards:
            break

    cards_to_process = job_cards[:max_jobs]
    expected_count = len(cards_to_process)
    log.info("job_cards_found", count=len(job_cards), processing=expected_count)

    listings: list[JobListing] = []
    failed_cards: list[FailedCard] = []

    for position, card in enumerate(cards_to_process):
        try:
            listing = await _extract_single_card(page, card)
        except Exception as exc:
            # Preserve the existing skip-on-error behavior while making
            # the loss auditable at page level.
            identifier = _card_placeholder_id(card, position)
            log.warning(
                "job_card_extraction_error",
                position=position,
                card=identifier,
                error=str(exc),
            )
            failed_cards.append(
                FailedCard(position=position, identifier=identifier, error=str(exc))
            )
            continue

        if listing is None:
            identifier = _card_placeholder_id(card, position)
            log.warning("job_card_extraction_empty", position=position, card=identifier)
            failed_cards.append(
                FailedCard(
                    position=position,
                    identifier=identifier,
                    error="extraction returned no listing",
                )
            )
            continue

        listings.append(listing)
        log.debug(
            "job_extracted",
            position=position,
            title=listing.title[:40],
            company=listing.company,
            easy_apply=listing.is_easy_apply,
            already_applied=listing.already_applied,
        )

    success_count = len(listings)
    failure_count = len(failed_cards)
    outcome = PageOutcome.PARTIAL_FAILURE if failure_count else PageOutcome.COMPLETE

    result = PageExtractionResult(
        outcome=outcome,
        listings=tuple(listings),
        expected_card_count=expected_count,
        success_count=success_count,
        failure_count=failure_count,
        failed_cards=tuple(failed_cards),
    )

    log.info(
        "page_extraction_summary",
        outcome=outcome.value,
        expected=expected_count,
        succeeded=success_count,
        failed=failure_count,
        failed_cards=[failed.identifier for failed in failed_cards],
    )

    return result


def _extract_job_id(url: str) -> str:
    """Extract the job ID from a LinkedIn URL.

    Validates that the ID is numeric. Falls back to a UUID if
    no valid ID can be extracted (fixes BUG-MED-2: empty job_id collision).

    Args:
        url: LinkedIn job URL.

    Returns:
        Numeric job ID string, or a UUID fallback.
    """
    candidates: list[str] = []

    if "currentJobId=" in url:
        candidates.append(url.split("currentJobId=")[1].split("&")[0])
    if "/view/" in url:
        candidates.append(url.split("/view/")[1].split("/")[0].split("?")[0])
    if "/jobs/" in url:
        parts = url.split("/jobs/")
        if len(parts) > 1:
            segment = parts[1].split("/")[0].split("?")[0]
            candidates.append(segment)

    # Return first numeric candidate
    for candidate in candidates:
        if candidate and candidate.isdigit():
            return candidate

    # Fallback: generate UUID to prevent UNIQUE constraint collision
    fallback_id = f"unknown-{uuid.uuid4().hex[:12]}"
    log.warning("job_id_extraction_fallback", url=url[:100], fallback_id=fallback_id)
    return fallback_id


def should_skip_job(
    listing: JobListing,
    blacklist_titles: list[str],
    blacklist_companies: list[str],
) -> tuple[bool, str]:
    """Check if a job should be skipped based on blacklists and state.

    Args:
        listing: The job listing to check.
        blacklist_titles: List of title keywords to skip.
        blacklist_companies: List of company names to skip.

    Returns:
        Tuple of (should_skip, reason).
    """
    if listing.already_applied:
        return True, "Already applied (LinkedIn badge)"

    if not listing.is_easy_apply:
        return True, "Not Easy Apply"

    title_lower = listing.title.lower()
    for keyword in blacklist_titles:
        if keyword.lower() in title_lower:
            return True, f"Blacklisted title keyword: {keyword}"

    company_lower = listing.company.lower()
    for company in blacklist_companies:
        if company.lower() in company_lower:
            return True, f"Blacklisted company: {company}"

    return False, ""
