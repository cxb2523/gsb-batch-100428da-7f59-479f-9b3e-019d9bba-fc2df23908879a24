"""Maps extractor for the GoogleMapsCrawler.

This module handles extracting company data from Google Maps search results.
It navigates through result links and extracts detailed company information.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from urllib.parse import unquote, urlsplit

from playwright.sync_api import Page, TimeoutError

# Add the parent directory to sys.path so that `from core.*` imports work
# This allows running from both the root directory and from within the core directory
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from core.exceptions import DetailPageLoadError, ExtractionError, ReviewsTimeoutError
from core.models import CompanyData, CrawlResults, ResultOutcome, ResultStatus, ReviewData

logger = logging.getLogger(__name__)


class MapsExtractor:
    """Extracts company data from Google Maps search results.

    This class handles:
    - Scrolling through the results feed
    - Collecting result links
    - Navigating to individual company pages
    - Extracting detailed company information

    Usage:
        extractor = MapsExtractor(page, config)
        companies = extractor.extract_all()
    """

    # CSS Selectors
    FEED_SELECTOR = '[role="feed"]'
    LINK_SELECTOR = "a.hfpxzc"
    NAME_SELECTOR = "h1.DUwDvf"
    RATING_SELECTOR = "div.F7nice"
    CATEGORY_BUTTON_SELECTOR = 'button.DkEaL[jsaction*=".category"]'
    ADDRESS_CONTAINER_SELECTOR = 'button[data-item-id="address"] div.Io6YTe'
    WEBSITE_SELECTOR = 'a[data-item-id="authority"]'
    PHONE_CONTAINER_SELECTOR = 'button[data-item-id*="phone:tel:"] div.Io6YTe'
    PLUS_CODE_SELECTOR = 'button[data-item-id="oloc"] div.Io6YTe'

    # Review selectors
    REVIEWS_TAB_SELECTOR = 'button[aria-label*="Rezensionen"][aria-label*="Rezensionen"]'
    REVIEW_ITEM_SELECTOR = "div.jftiEf.fontBodyMedium"
    REVIEW_AUTHOR_NAME_SELECTOR = "div.d4r55.fontTitleMedium"
    REVIEW_AUTHOR_INFO_SELECTOR = "div.RfnDt"
    REVIEW_AUTHOR_IMAGE_SELECTOR = "img.NBa7we"
    REVIEW_RATING_SELECTOR = "span.kvMYJc[role='img']"
    REVIEW_TIME_SELECTOR = "span.rsqaWe"
    REVIEW_TEXT_SELECTOR = "span.wiI7pd"
    REVIEW_PHOTOS_SELECTOR = "button.Tya61d"
    REVIEW_LIKES_COUNT_SELECTOR = "span.pkWtMe"
    # The scrollable reviews container - specific class combination
    REVIEWS_SCROLL_CONTAINER_SELECTOR = "div.m6QErb.DxyBCb.kA9KIf.dS8AEf.XiKgde"
    # The outer container that holds the reviews section
    REVIEWS_SECTION_SELECTOR = "div.m6QErb.WNBkOb.XiKgde"

    def __init__(
        self,
        page: Page,
        selector_timeout: int = 15000,
        reviews_timeout: int = 10000,
    ) -> None:
        """Initialize the MapsExtractor.

        Args:
            page: The Playwright Page object.
            selector_timeout: Timeout for selector waits in milliseconds.
            reviews_timeout: Total budget (milliseconds) for waiting on the
                reviews section to appear.  If the reviews never load within
                this window, the position is recorded as ``reviews_timeout``
                and the crawl continues.
        """
        self._page = page
        self._selector_timeout = selector_timeout
        self._reviews_timeout = reviews_timeout
        self._last_outcomes: list[ResultOutcome] = []

    @property
    def outcomes(self) -> list[ResultOutcome]:
        """Get the per-position outcomes of the most recent ``extract_all``.

        Returns:
            A list of ResultOutcome objects ordered by result position.
        """
        return self._last_outcomes

    def extract_all(self, track_reviews: bool = True) -> CrawlResults:
        """Extract all company data from the current Google Maps search results page.

        Every position in the result feed gets an outcome record (success,
        detail page failure, reviews timeout or duplicate).  The returned
        value is a :class:`CrawlResults` list, so existing code that treats it
        as a plain list of companies keeps working.

        Args:
            track_reviews: Whether to extract reviews for each company.

        Returns:
            A CrawlResults list of CompanyData objects carrying outcomes.

        Raises:
            ExtractionError: If the extraction process fails.
        """
        try:
            result_links = self._collect_result_links()
            if not result_links:
                logger.warning("No result links found on the page")
                self._last_outcomes = []
                return CrawlResults([])

            logger.info("Found %d result links to process (track_reviews=%s)", len(result_links), track_reviews)
            results = self._process_links(result_links, track_reviews=track_reviews)
            self._last_outcomes = results.outcomes
            logger.info(
                "Extracted %d companies from %d positions (%s)",
                len(results),
                len(result_links),
                ", ".join(f"{status}={count}" for status, count in results.counts_by_status().items()) or "no outcomes",
            )
            return results

        except Exception as e:
            raise ExtractionError(
                message="Failed to extract data from Google Maps.",
                cause=e,
            ) from e

    def _collect_result_links(self) -> list[str]:
        """Collect all result links from the Google Maps feed.

        The links are returned in feed order, *including* duplicates, because
        every link corresponds to one result position that needs an outcome.
        Duplicate detection happens later, on normalized place links.

        Returns:
            A list of Google Maps place URLs in result order.
        """
        parent_element = self._wait_for_feed()
        if not parent_element:
            return []

        self._scroll_through_results()

        return self._extract_links_from_feed(parent_element)

    def _wait_for_feed(self):
        """Wait for the results feed to appear.

        Returns:
            The feed element or None if timeout.
        """
        try:
            return self._page.wait_for_selector(
                self.FEED_SELECTOR,
                timeout=25000,
            )
        except TimeoutError:
            logger.warning("Timeout waiting for feed selector")
            return None
        except Exception as e:
            logger.warning("Error waiting for feed: %s", e)
            return None

    def _scroll_through_results(self) -> None:
        """Scroll through the results feed to load all companies."""
        start_time = time.time()
        last_height = self._page.evaluate(
            '() => document.querySelector(\'[role="feed"]\').scrollHeight'
        )
        scroll_attempts = 0

        while time.time() - start_time < 45:
            self._page.evaluate(
                "document.querySelector('[role=\"feed\"]').scrollTop = "
                "document.querySelector('[role=\"feed\"]').scrollHeight"
            )
            self._page.wait_for_timeout(1500)

            new_height = self._page.evaluate(
                "() => document.querySelector('[role=\"feed\"]').scrollHeight"
            )

            if new_height == last_height:
                scroll_attempts += 1
                if scroll_attempts >= 5:
                    break
            else:
                scroll_attempts = 0
            last_height = new_height

    def _extract_links_from_feed(self, parent_element) -> list[str]:
        """Extract valid Google Maps place URLs from the feed.

        Args:
            parent_element: The feed container element.

        Returns:
            A list of valid place URLs.
        """
        link_elements = parent_element.query_selector_all(self.LINK_SELECTOR)
        links = []

        for link_element in link_elements:
            href = link_element.get_attribute("href")
            if href and href.startswith("https://www.google.com/maps/place/"):
                if len(href) > 40:
                    links.append(href)

        return links

    @staticmethod
    def _remove_duplicate_links(links: list[str]) -> list[str]:
        """Remove duplicate links while preserving order.

        Duplicates are detected on the normalized place link (see
        :meth:`_normalize_place_url`).

        Args:
            links: A list of URLs.

        Returns:
            A list of unique URLs, in their original form and order.
        """
        seen: set[str] = set()
        unique: list[str] = []
        for link in links:
            key = MapsExtractor._normalize_place_url(link)
            if key not in seen:
                seen.add(key)
                unique.append(link)
        return unique

    @staticmethod
    def _normalize_place_url(url: str) -> str:
        """Normalize a Google Maps place link for duplicate detection.

        Google Maps emits the same place with different tracking/query
        parameters and encodings.  The normalized form ignores the scheme,
        a leading ``www.``, the query string, the fragment and trailing
        slashes, and decodes percent-escapes/case differences in the path.

        Args:
            url: A Google Maps place URL.

        Returns:
            A normalized key that is equal for equivalent place links.
        """
        if not url:
            return ""

        try:
            parts = urlsplit(url.strip())
        except ValueError:
            return url.strip().lower()

        host = parts.netloc.lower()
        if host.startswith("www."):
            host = host[4:]

        path = unquote(parts.path)
        # Google Maps encodes spaces in the place segment as "+".
        path = path.replace("+", " ")
        path = re.sub(r"\s+", " ", path)
        # Drop the volatile "/data=!..." segment that differs between the
        # search-results link and a later navigation to the same place.
        path = re.sub(r"/?data=.*$", "", path, flags=re.IGNORECASE)
        path = re.sub(r"/+\s*$", "", path).lower()

        return f"{host}{path}"

    @staticmethod
    def _name_from_url(url: str) -> str:
        """Derive a readable place name from a Google Maps place URL.

        Used to identify duplicates and failed positions even when no detail
        page could be read.

        Args:
            url: A Google Maps place URL.

        Returns:
            The decoded place name, or an empty string if none can be found.
        """
        if not url:
            return ""

        match = re.search(r"/maps/place/([^/@?]+)", url, re.IGNORECASE)
        if not match:
            return ""

        return unquote(match.group(1).replace("+", " ")).strip()

    def _process_links(self, links: list[str], track_reviews: bool = True) -> CrawlResults:
        """Process each link and extract company data, recording an outcome per position.

        Positions are zero-based indexes into the collected result feed.
        A position is only considered "covered" after it was successfully
        extracted; later positions with the same normalized place link are
        skipped and marked as duplicates of that successful position.

        Args:
            links: A list of Google Maps place URLs in result order.
            track_reviews: Whether to extract reviews for each company.

        Returns:
            A CrawlResults list of successfully extracted CompanyData objects
            (same meaning and order as before) carrying the outcomes.
        """
        companies: list[CompanyData] = []
        outcomes: list[ResultOutcome] = []
        # normalized place link -> position that was successfully extracted
        covered_by: dict[str, int] = {}

        for i, url in enumerate(links):
            normalized = self._normalize_place_url(url)

            if normalized and normalized in covered_by:
                covering_position = covered_by[normalized]
                logger.info(
                    "Skipping duplicate result at position %d (same place as position %d)",
                    i,
                    covering_position,
                )
                outcomes.append(
                    ResultOutcome(
                        position=i,
                        status=ResultStatus.DUPLICATE,
                        url=url,
                        name=self._name_from_url(url),
                        reason=f"Duplicate of successful position {covering_position}",
                        duplicate_of=covering_position,
                    )
                )
                continue

            try:
                company, review_warning = self._extract_company_details(
                    url, i, track_reviews=track_reviews
                )
            except DetailPageLoadError as e:
                logger.warning("Detail page failed at position %d (%s): %s", i, url, e)
                outcomes.append(
                    ResultOutcome(
                        position=i,
                        status=ResultStatus.DETAIL_PAGE_FAILED,
                        url=url,
                        name=self._name_from_url(url),
                        reason=str(e),
                    )
                )
                continue
            except Exception as e:
                # Unexpected errors must not abort the whole crawl either.
                logger.warning("Failed to extract company from %s: %s", url, e)
                outcomes.append(
                    ResultOutcome(
                        position=i,
                        status=ResultStatus.DETAIL_PAGE_FAILED,
                        url=url,
                        name=self._name_from_url(url),
                        reason=f"Unexpected extraction error: {e}",
                    )
                )
                continue

            if company is None:
                outcomes.append(
                    ResultOutcome(
                        position=i,
                        status=ResultStatus.DETAIL_PAGE_FAILED,
                        url=url,
                        name=self._name_from_url(url),
                        reason="No company data could be extracted",
                    )
                )
                continue

            if normalized:
                covered_by[normalized] = i

            companies.append(company)

            if review_warning:
                status = ResultStatus.REVIEWS_TIMEOUT
                reason = review_warning
            else:
                status = ResultStatus.SUCCESS
                reason = ""

            outcomes.append(
                ResultOutcome(
                    position=i,
                    status=status,
                    url=url,
                    name=company.name,
                    reason=reason,
                    company=company,
                )
            )

            # Small delay between requests
            time.sleep(0.3 + (i % 5) * 0.1)

        return CrawlResults(companies, outcomes)

    def _extract_company_details(
        self, url: str, index: int, track_reviews: bool = True
    ) -> tuple[CompanyData, str | None]:
        """Navigate to a company page and extract its details.

        Args:
            url: The Google Maps place URL.
            index: The index of this link in the results (for logging).
            track_reviews: Whether to extract reviews for this company.

        Returns:
            A tuple of (CompanyData, review_warning). ``review_warning`` is
            ``None`` when everything was extracted, or a reason string when
            the company details were read but the reviews timed out. In the
            latter case the company is still returned (without reviews).

        Raises:
            DetailPageLoadError: If the page cannot be opened or the company
                name cannot be found on it.
        """
        try:
            self._page.goto(url, timeout=25000, wait_until="domcontentloaded")

            # Wait for the company name to confirm the page loaded
            try:
                self._page.wait_for_selector(self.NAME_SELECTOR, timeout=15000)
            except TimeoutError as e:
                raise DetailPageLoadError(
                    message="Company name not found on detail page",
                    url=url,
                    cause=e,
                ) from e

            # Build details dictionary
            details: dict = {
                "name": self._safe_text(self._page.locator(self.NAME_SELECTOR)),
                "rating": self._extract_rating(),
                "category": self._extract_category(),
                "address": self._safe_text(
                    self._page.locator(self.ADDRESS_CONTAINER_SELECTOR)
                ),
                "website": self._safe_attribute(
                    self._page.locator(self.WEBSITE_SELECTOR), "href"
                ),
                "phone": self._safe_text(
                    self._page.locator(self.PHONE_CONTAINER_SELECTOR)
                ),
                "plus_code": self._safe_text(
                    self._page.locator(self.PLUS_CODE_SELECTOR)
                ),
                "opening_hours": self._extract_opening_hours(),
                "attributes": self._extract_attributes(),
            }

            review_warning: str | None = None

            # Only extract reviews if track_reviews is True
            if track_reviews:
                try:
                    details["reviews"] = self._extract_reviews()
                except ReviewsTimeoutError as e:
                    # Reviews failing to load must not discard the company or
                    # abort the crawl - record it and move on.
                    review_warning = str(e)
                    details["reviews"] = []
                    logger.warning("Reviews timed out at position %d (%s)", index, url)
                except Exception as e:
                    # Unexpected review errors are equally non-fatal.
                    review_warning = f"Error extracting reviews: {e}"
                    details["reviews"] = []
                    logger.warning("Error extracting reviews at position %d (%s): %s", index, url, e)
            else:
                details["reviews"] = []
                logger.debug("Review tracking disabled for company at index %d", index)

            return CompanyData(**details), review_warning

        except DetailPageLoadError:
            raise
        except TimeoutError as e:
            raise DetailPageLoadError(
                message="Timed out loading detail page",
                url=url,
                cause=e,
            ) from e
        except Exception as e:
            raise DetailPageLoadError(
                message=f"Error extracting company details: {e}",
                url=url,
                cause=e,
            ) from e

    def _extract_rating(self) -> str:
        """Extract the company rating and review count.

        Returns:
            The rating as a string (e.g., "4.5").
        """
        rating_text = self._safe_text(self._page.locator(self.RATING_SELECTOR))
        if not rating_text:
            return "N/A"

        rating_match = re.search(r"([\d,\.]+)", rating_text)
        return rating_match.group(1).replace(",", ".") if rating_match else "N/A"

    def _extract_category(self) -> str:
        """Extract the company category.

        Returns:
            The category as a string.
        """
        category_locator = self._page.locator(self.CATEGORY_BUTTON_SELECTOR)
        if category_locator.count() > 0:
            return self._safe_text(category_locator)

        # Fallback selector
        fallback = self._page.locator("div.fontBodyMedium span button.DkEaL")
        return self._safe_text(fallback, default="N/A")

    def _extract_opening_hours(self) -> str:
        """Extract the opening hours information.

        Returns:
            The opening hours as a formatted string.
        """
        hours_text = "N/A"

        # Try main hours container
        hours_container = self._page.locator(
            'div[aria-label*="Öffnungszeiten"], div.MkV9'
        )
        hours_button = self._page.locator('button[jsaction*="openhours"]')

        if hours_container.count() > 0:
            hours_text = self._safe_text(hours_container.first)
            if not hours_text or "Öffnungszeiten für die ganze Woche" in hours_text:
                if hours_button.count() > 0:
                    try:
                        hours_button.click(timeout=2000)
                        self._page.wait_for_timeout(500)
                        hours_text = self._safe_text(hours_container.first)
                    except Exception:
                        hours_text = self._safe_text(hours_container.first)

        # Fallback selectors
        if not hours_text or hours_text == "N/A":
            fallback_selectors = [
                'div.fontBodyMedium span[class*=""]:has-text("Öffnet")',
                'div.fontBodyMedium span[class*=""]:has-text("Geschlossen")',
                'div.fontBodyMedium span[class*=""]:has-text("Rund um die Uhr geöffnet")',
            ]
            for selector in fallback_selectors:
                extracted = self._safe_text(self._page.locator(selector).first)
                if extracted:
                    hours_text = extracted
                    break

        if hours_text and hours_text != "N/A":
            hours_text = hours_text.replace("\n", " ").strip()

        return hours_text

    def _extract_attributes(self) -> list[str]:
        """Extract company attributes (e.g., wheelchair accessibility).

        Returns:
            A list of attribute strings.
        """
        attributes = []

        # Wheelchair accessibility
        wc_locator = self._page.locator(
            'span.google-symbols[aria-label*="Rollstuhl"], span.google-symbols[data-tooltip*="Rollstuhl"]'
        )
        if wc_locator.count() > 0:
            wc_label = self._safe_attribute(wc_locator.first, "aria-label") or self._safe_attribute(
                wc_locator.first, "data-tooltip"
            )
            attributes.append(wc_label if wc_label else "Rollstuhlgerechter Eingang")

        # On-site services
        service_locator = self._page.locator(
            'div.Ahnjwc:has-text("Service/Leistungen vor Ort")'
        )
        if service_locator.count() > 0:
            attributes.append("Service/Leistungen vor Ort")

        return attributes if attributes else ["N/A"]

    def _safe_text(self, locator, default: str = "N/A", timeout: int = 2000) -> str:
        """Safely extract text from a locator.

        Args:
            locator: The Playwright locator.
            default: The default value if extraction fails.
            timeout: The timeout in milliseconds.

        Returns:
            The extracted text or the default value.
        """
        try:
            return locator.inner_text(timeout=timeout).strip() or default
        except TimeoutError:
            return default
        except Exception:
            return default

    def _extract_reviews(self, max_reviews: int = 50) -> list[ReviewData]:
        """Extract all reviews from the currently active reviews tab.

        This method:
        1. Clicks the Reviews tab button to activate it
        2. Waits for the reviews scroll container to appear
        3. Scrolls through the reviews to load all of them
        4. Parses each review element within the correct container and extracts data

        Args:
            max_reviews: Maximum number of reviews to extract.

        Returns:
            A list of ReviewData objects.

        Raises:
            ReviewsTimeoutError: If the reviews tab could be activated but the
                reviews section did not appear within the configured reviews
                timeout.  When no reviews tab exists at all (a place without
                reviews), an empty list is returned.  Individual review parsing
                errors do not raise; they only skip that review.
        """
        reviews = []

        # Step 1: Click the Reviews tab button. If there is no reviews tab at
        # all, the place simply has no reviews to load.
        if not self._click_reviews_tab():
            logger.debug("No reviews tab found; place likely has no reviews")
            return []

        # Step 2: Wait for the reviews scroll container to appear
        try:
            self._page.wait_for_selector(
                self.REVIEWS_SCROLL_CONTAINER_SELECTOR,
                timeout=self._reviews_timeout,
            )
            logger.info("Reviews scroll container found")
        except TimeoutError:
            logger.warning("Reviews scroll container not found, trying fallback")
            # Fallback: wait for any review items, with a shorter budget
            try:
                self._page.wait_for_selector(
                    self.REVIEW_ITEM_SELECTOR,
                    timeout=max(2000, self._reviews_timeout // 3),
                )
            except TimeoutError as e:
                raise ReviewsTimeoutError(
                    message="Reviews did not load within the timeout",
                    cause=e,
                ) from e

        # Additional wait for animations to complete
        self._page.wait_for_timeout(1000)

        # Step 3: Scroll through reviews to load all
        self._scroll_through_reviews()

        # Step 4: Extract reviews ONLY from the reviews scroll container
        review_container = self._page.query_selector(self.REVIEWS_SCROLL_CONTAINER_SELECTOR)
        if review_container:
            review_elements = review_container.query_selector_all(self.REVIEW_ITEM_SELECTOR)
            logger.info("Found %d review elements in scroll container", len(review_elements))
        else:
            # Fallback: try to find reviews in the page
            review_elements = self._page.query_selector_all(self.REVIEW_ITEM_SELECTOR)
            logger.info("Scroll container not found, using page-wide search: %d reviews", len(review_elements))

        for idx, elem in enumerate(review_elements[:max_reviews]):
            try:
                review = self._parse_single_review(elem)
                if review and review.review_text != "N/A":
                    reviews.append(review)
            except Exception as e:
                logger.warning("Failed to parse review %d: %s", idx, e)
                continue

        logger.info("Successfully extracted %d reviews", len(reviews))

        return reviews

    def _click_reviews_tab(self) -> bool:
        """Click the Reviews tab button to show the reviews section.

        Uses the specific Google Maps tab structure with role="tab" buttons.

        Returns:
            True if a reviews tab was found and clicked, False otherwise.
        """
        clicked = False

        # Primary selector: exact match for reviews tab with role="tab"
        # The button has aria-label like "Rezensionen zu „Company Name“"
        reviews_tab = self._page.locator('button[role="tab"][aria-label*="Rezensionen"]')
        if reviews_tab.count() > 0:
            try:
                reviews_tab.first.click(timeout=3000)
                self._page.wait_for_timeout(800)
                clicked = True
                logger.info("Clicked reviews tab (German selector)")
            except Exception as e:
                logger.debug("Failed to click German reviews tab: %s", e)

        # Fallback: try English selector
        if not clicked:
            reviews_tab_en = self._page.locator('button[role="tab"][aria-label*="Reviews"]')
            if reviews_tab_en.count() > 0:
                try:
                    reviews_tab_en.first.click(timeout=3000)
                    self._page.wait_for_timeout(800)
                    clicked = True
                    logger.info("Clicked reviews tab (English selector)")
                except Exception as e:
                    logger.debug("Failed to click English reviews tab: %s", e)

        if not clicked:
            logger.debug("Reviews tab button not found or click failed")

        return clicked

    def _scroll_through_reviews(self) -> None:
        """Scroll through the reviews section to load all reviews.

        Uses the specific reviews scroll container selector to ensure
        we scroll the correct area. Optimized for longer and faster scrolling.
        """
        start_time = time.time()
        max_scroll_time = 120  # Increased from 90 to allow even more time for loading

        # Primary selector: the specific reviews scroll container
        scroll_selector = self.REVIEWS_SCROLL_CONTAINER_SELECTOR

        # Verify the container exists
        container = self._page.query_selector(scroll_selector)
        if not container:
            logger.debug("Reviews scroll container not found, trying fallback selectors")
            # Fallback selectors
            fallback_selectors = [
                'div.m6QErb.DxyBCb.kA9KIf.dS8AEf.XiKgde',
                '[role="feed"]',
                'div[role="feed"]',
            ]
            for sel in fallback_selectors:
                try:
                    container = self._page.query_selector(sel)
                    if container:
                        scroll_selector = sel
                        logger.debug("Using fallback selector: %s", sel)
                        break
                except Exception:
                    continue

        if not container:
            logger.debug("No reviews container found for scrolling")
            return

        # Get initial scroll height using the correct selector
        try:
            last_height = self._page.evaluate(
                f"""() => {{
                    const el = document.querySelector('{scroll_selector}');
                    return el ? el.scrollHeight : 0;
                }}"""
            )
        except Exception:
            return

        scroll_attempts = 0
        scroll_distance = 500  # Larger scroll steps for faster loading

        while time.time() - start_time < max_scroll_time:
            # Scroll down by a larger fixed amount for faster loading
            try:
                self._page.evaluate(
                    f"""() => {{
                        const el = document.querySelector('{scroll_selector}');
                        if (el) {{
                            el.scrollTop = el.scrollTop + {scroll_distance};
                        }}
                    }}"""
                )
            except Exception:
                break

            # Shorter wait time between scrolls for faster processing
            self._page.wait_for_timeout(800)

            # Check if new content loaded
            try:
                new_height = self._page.evaluate(
                    f"""() => {{
                        const el = document.querySelector('{scroll_selector}');
                        return el ? el.scrollHeight : 0;
                    }}"""
                )
            except Exception:
                break

            if new_height == last_height:
                scroll_attempts += 1
                if scroll_attempts >= 6:  # More consecutive attempts before giving up
                    logger.debug("No new content after %d consecutive attempts", scroll_attempts)
                    break
            else:
                scroll_attempts = 0
                last_height = new_height

        logger.info("Scrolling complete. Final scroll height: %d", last_height)

    def _parse_single_review(self, review_element) -> ReviewData | None:
        """Parse a single review element and extract review data.

        Args:
            review_element: The Playwright element for a single review.

        Returns:
            A ReviewData object or None if parsing fails.
        """
        try:
            # Author name
            author_name = "N/A"
            name_elem = review_element.query_selector(self.REVIEW_AUTHOR_NAME_SELECTOR)
            if name_elem:
                author_name = name_elem.inner_text().strip() or "N/A"

            # Author info (Local Guide, review count, photo count)
            author_local_guide = False
            author_review_count = 0
            author_info = review_element.query_selector(self.REVIEW_AUTHOR_INFO_SELECTOR)
            if author_info:
                info_text = author_info.inner_text().strip()
                # Check for "Local Guide"
                if "Local Guide" in info_text or "Localer Guide" in info_text or "LOKALER GUIDE" in info_text:
                    author_local_guide = True
                # Extract review count from text like "12 Rezensionen" or "12 reviews"
                count_match = re.search(r'(\d+)\s*(?:Rezensionen|reviews|Review)', info_text)
                if count_match:
                    author_review_count = int(count_match.group(1))

            # Author image
            author_image = "N/A"
            img_elem = review_element.query_selector(self.REVIEW_AUTHOR_IMAGE_SELECTOR)
            if img_elem:
                author_image = img_elem.get_attribute("src") or "N/A"

            # Rating (from aria-label like "5 Sterne")
            rating = 0.0
            rating_elem = review_element.query_selector(self.REVIEW_RATING_SELECTOR)
            if rating_elem:
                aria_label = rating_elem.get_attribute("aria-label") or ""
                rating_match = re.search(r'([\d.]+)', aria_label)
                if rating_match:
                    rating = float(rating_match.group(1))

            # Time relative (e.g., "vor 6 Monaten")
            time_relative = "N/A"
            time_elem = review_element.query_selector(self.REVIEW_TIME_SELECTOR)
            if time_elem:
                time_relative = time_elem.inner_text().strip() or "N/A"

            # Review text
            review_text = "N/A"
            text_elem = review_element.query_selector(self.REVIEW_TEXT_SELECTOR)
            if text_elem:
                text = text_elem.inner_text().strip()
                if text and text != "N/A":
                    review_text = text

            # Review ID
            review_id = review_element.get_attribute("data-review-id") or "N/A"

            # Likes count
            likes = 0
            likes_elem = review_element.query_selector(self.REVIEW_LIKES_COUNT_SELECTOR)
            if likes_elem:
                likes_text = likes_elem.inner_text().strip()
                if likes_text.isdigit():
                    likes = int(likes_text)

            # Photos
            photos = []
            photo_elems = review_element.query_selector_all(self.REVIEW_PHOTOS_SELECTOR)
            for photo_elem in photo_elems:
                bg_style = photo_elem.get_attribute("style") or ""
                url_match = re.search(r'url\(["\']?(.*?)["\']?\)', bg_style)
                if url_match:
                    photo_url = url_match.group(1)
                    # Normalize URL parameters for better quality
                    photo_url = photo_url.replace("w600-h900-p-k-no", "w1200-h900-p-k-no")
                    photos.append(photo_url)

            return ReviewData(
                author_name=author_name,
                author_image=author_image,
                author_local_guide=author_local_guide,
                author_review_count=author_review_count,
                rating=rating,
                review_text=review_text,
                time_relative=time_relative,
                review_id=review_id,
                likes=likes,
                photos=photos,
                owner_response="N/A",  # Owner responses would need separate extraction
            )

        except Exception as e:
            logger.warning("Failed to parse single review: %s", e)
            return None

    def _safe_attribute(self, locator, attribute: str, default: str = "N/A", timeout: int = 2000) -> str:
        """Safely extract an attribute from a locator.

        Args:
            locator: The Playwright locator.
            attribute: The attribute name to extract.
            default: The default value if extraction fails.
            timeout: The timeout in milliseconds.

        Returns:
            The attribute value or the default value.
        """
        try:
            value = locator.get_attribute(attribute, timeout=timeout)
            return value if value else default
        except TimeoutError:
            return default
        except Exception:
            return default
