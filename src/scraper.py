"""
News article scraper module for extracting content from URLs.

This module provides functionality to scrape news articles from various websites,
extracting the headline, text content, publication date, and other metadata.
"""

import logging
import time
import re
from typing import Callable, Dict, Optional, List, Any, Tuple
from urllib.parse import urlparse
from dataclasses import dataclass

import requests
from newspaper import Article, ArticleException, Config
from bs4 import BeautifulSoup
from tqdm import tqdm

from src.config import MAX_RETRY_ATTEMPTS, SCRAPE_MIN_INTERVAL_MS

# Default used when no interval is supplied to ArticleScraper. Kept small so the
# throttle does not noticeably slow down small batches while still protecting a
# single site from a burst of consecutive requests.
DEFAULT_MIN_SCRAPE_INTERVAL_MS = SCRAPE_MIN_INTERVAL_MS

# Configure logging
logger = logging.getLogger(__name__)


@dataclass
class ScrapedArticle:
    """Data class representing a scraped news article."""
    url: str
    headline: str
    text: str
    publish_date: Optional[str] = None
    authors: List[str] = None
    source_domain: Optional[str] = None
    image_url: Optional[str] = None
    article_posted: Optional[str] = None  # Publication date in ISO format
    article_indexed: Optional[str] = None  # When the article was scraped/indexed in ISO format

    def __post_init__(self):
        """Initialize default values for optional fields."""
        if self.authors is None:
            self.authors = []

        # Extract domain from URL if not provided
        if self.source_domain is None and self.url:
            parsed_url = urlparse(self.url)
            self.source_domain = parsed_url.netloc

        # Set article_posted from publish_date if available
        if self.publish_date and not self.article_posted:
            self.article_posted = self.publish_date


class FailureReason:
    """Stable reason codes for URLs that could not be scraped in a batch."""
    REQUEST_OR_PARSE_ERROR = "request_or_parse_error"
    EMPTY_RESULT = "empty_result"
    EMPTY_CONTENT = "empty_content"


@dataclass
class ScrapeOutcome:
    """
    Per-URL record produced by :meth:`ArticleScraper.scrape_urls`.

    Attributes:
        url: The URL exactly as it was passed to the batch entry point.
        host: Lower-cased host name including a non-default port (``netloc``).
        status: ``"success"`` or ``"failed"``.
        waited_ms: Milliseconds actually spent waiting for the host throttle
            before this URL was scraped (``0.0`` when no wait was needed).
        headline: Article headline for successful scrapes.
        text_length: Length of the cleaned article body for successful scrapes.
        failure_reason: One of :class:`FailureReason` for failed scrapes.
        error: Human readable error detail for failed scrapes (if available).
        article: The scraped article object for successful scrapes.
    """
    url: str
    host: str
    status: str
    waited_ms: float = 0.0
    headline: Optional[str] = None
    text_length: int = 0
    failure_reason: Optional[str] = None
    error: Optional[str] = None
    article: Optional[ScrapedArticle] = None

    @property
    def succeeded(self) -> bool:
        """Whether the URL produced a usable article."""
        return self.status == "success"

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly representation (excluding the article object)."""
        return {
            "url": self.url,
            "host": self.host,
            "status": self.status,
            "waited_ms": self.waited_ms,
            "headline": self.headline,
            "text_length": self.text_length,
            "failure_reason": self.failure_reason,
            "error": self.error,
        }


class ScrapeBatchResult(list):
    """
    List of successfully scraped articles with per-URL outcome details.

    This is a real ``list`` subclass, so existing callers that treat the result
    of :meth:`ArticleScraper.scrape_urls` as ``List[ScrapedArticle]`` keep
    working unchanged. In addition it exposes ``outcomes`` (one entry per input
    URL, in input order) plus aggregate counters.
    """

    def __init__(self, outcomes: Optional[List[ScrapeOutcome]] = None):
        self.outcomes = list(outcomes or [])
        super().__init__(
            outcome.article for outcome in self.outcomes
            if outcome.succeeded and outcome.article is not None
        )

    @property
    def articles(self) -> List[ScrapedArticle]:
        """Alias returning the successful articles as a plain list."""
        return list(self)

    @property
    def successes(self) -> List[ScrapeOutcome]:
        """Outcome records for URLs that were scraped successfully."""
        return [outcome for outcome in self.outcomes if outcome.succeeded]

    @property
    def failures(self) -> List[ScrapeOutcome]:
        """Outcome records for URLs that failed."""
        return [outcome for outcome in self.outcomes if not outcome.succeeded]

    @property
    def success_count(self) -> int:
        """Number of URLs that were scraped successfully."""
        return sum(1 for outcome in self.outcomes if outcome.succeeded)

    @property
    def failure_count(self) -> int:
        """Number of URLs that failed."""
        return len(self.outcomes) - self.success_count

    @property
    def total_waited_ms(self) -> float:
        """Total milliseconds spent waiting on the per-host throttle."""
        return sum(outcome.waited_ms for outcome in self.outcomes)

    @property
    def input_count(self) -> int:
        """Number of URLs the batch was asked to scrape."""
        return len(self.outcomes)


class ArticleScraper:
    """Class for scraping news articles from URLs."""
    
    # Class constants for content extraction patterns
    HEADLINE_CLASSES = [
        'headline', 'title', 'article-title', 'entry-title', 'post-title',
        'story-title', 'content-title', 'article__title', 'article-headline',
        'pg-headline'
    ]
    
    CONTENT_CLASSES = [
        'article-body', 'article-content', 'entry-content', 'post-content',
        'story-content', 'article__body', 'story__body', 'content-body',
        'main-content', 'article', 'content', 'post'
    ]
    
    CONTENT_IDS = [
        'article-body', 'article-content', 'entry-content', 'post-content',
        'content', 'main-content', 'story-body', 'article', 'story'
    ]
    
    AUTHOR_CLASSES = [
        'author', 'byline', 'article-byline', 'meta-author', 'creator',
        'article__author', 'story-meta__authors'
    ]
    
    DATE_CLASSES = [
        'date', 'published-date', 'article-date', 'post-date', 'meta-date',
        'story-date', 'article__date', 'publish-date', 'timestamp'
    ]
    
    # Patterns to clean up article text
    CLEANUP_PATTERNS = [
        (r'(\n\s*\n)+', '\n\n'),  # Multiple newlines
        (r'Related Articles.*?\n', ''),  # Related articles sections
        (r'Share this article.*?\n', ''),  # Share sections
        (r'Follow us on.*?\n', ''),  # Follow us sections
        (r'Advertisement.*?\n', ''),  # Advertisement markers
    ]
    
    def __init__(
        self,
        max_retries: int = MAX_RETRY_ATTEMPTS,
        min_interval_ms: Optional[float] = None,
        clock: Optional[Callable[[], float]] = None,
        sleep: Optional[Callable[[float], None]] = None,
    ):
        """
        Initialize the ArticleScraper.

        Args:
            max_retries: Maximum number of retry attempts for failed requests.
            min_interval_ms: Minimum delay in milliseconds between two scrapes
                that target the same host when using :meth:`scrape_urls`.
                Defaults to ``SCRAPE_MIN_INTERVAL_MS`` (configurable through the
                ``SCRAPE_MIN_INTERVAL_MS`` environment variable, 500 ms).
            clock: Callable returning a monotonic time in seconds. Injectable for
                tests; defaults to :func:`time.monotonic`.
            sleep: Callable used to wait, receiving seconds. Injectable for
                tests; defaults to :func:`time.sleep`.
        """
        self.max_retries = max_retries
        if min_interval_ms is None:
            min_interval_ms = DEFAULT_MIN_SCRAPE_INTERVAL_MS
        self.min_interval_ms = max(0.0, float(min_interval_ms))
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        # Per-host monotonic timestamp of the last scrape start, used by the
        # batch throttle. Keyed by host name (including port), lower-cased.
        self._host_last_start: Dict[str, float] = {}
        self.session = requests.Session()
        
        # Use a more realistic browser user agent
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36 Edg/113.0.1774.57',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Cache-Control': 'max-age=0'
        })
        
        # Configure newspaper
        self.newspaper_config = Config()
        self.newspaper_config.fetch_images = False  # Skip image fetching for speed
        self.newspaper_config.request_timeout = 20  # Longer timeout for slow sites
        self.newspaper_config.number_threads = 10  # More threads for parallel downloads
        self.newspaper_config.browser_user_agent = self.session.headers['User-Agent']

    def scrape_url(self, url: str) -> Optional[ScrapedArticle]:
        """
        Scrape a news article from the provided URL.
        
        Args:
            url: URL of the article to scrape.
            
        Returns:
            ScrapedArticle object containing the extracted content, or None if scraping failed.
        """
        # Check for known problematic domains that need special handling
        parsed_url = urlparse(url)
        domain = parsed_url.netloc.lower()
        
        # Reuters and some other sites often block scrapers
        if 'reuters.com' in domain:
            logger.info(f"Using specialized method for Reuters: {url}")
            return self._handle_reuters_article(url)
        elif any(site in domain for site in ['wsj.com', 'ft.com', 'nytimes.com']):
            logger.info(f"Using direct BeautifulSoup approach for paywalled site: {url}")
            # These sites often have paywalls, try BS4 directly
            return self._advanced_scrape_with_beautifulsoup(url)
        
        # Standard approach for most sites
        for attempt in range(self.max_retries):
            try:
                # Add a random delay between attempts to avoid rate limiting
                if attempt > 0:
                    delay = 2 + attempt * 2 + (time.time() % 2)  # Semi-random increasing delay
                    time.sleep(delay)
                
                logger.info(f"Scraping article from {url} (attempt {attempt + 1}/{self.max_retries})")
                
                # Use newspaper3k to extract article content
                article = Article(url, config=self.newspaper_config)
                article.download()
                article.parse()
                
                # Check if article text was successfully extracted
                if not article.text or not article.title:
                    # Try a fallback with BeautifulSoup if newspaper3k fails
                    return self._advanced_scrape_with_beautifulsoup(url)
                
                # Convert newspaper authors to list
                authors = article.authors if article.authors else []
                
                # Format publication date if available
                publish_date = None
                if article.publish_date:
                    publish_date = article.publish_date.isoformat()
                
                # Clean up the article text
                cleaned_text = self._clean_article_text(article.text)
                
                # Create ScrapedArticle dataclass instance
                return ScrapedArticle(
                    url=url,
                    headline=article.title,
                    text=cleaned_text,
                    publish_date=publish_date,
                    authors=authors,
                    image_url=article.top_image
                )
            
            except ArticleException as e:
                logger.warning(f"newspaper3k failed to scrape {url}: {str(e)}")
                # Try fallback method
                return self._advanced_scrape_with_beautifulsoup(url)
                
            except Exception as e:
                logger.error(f"Error scraping {url}: {str(e)}")
                if attempt < self.max_retries - 1:
                    wait_time = 2 ** attempt  # Exponential backoff
                    logger.info(f"Retrying in {wait_time} seconds...")
                    time.sleep(wait_time)
                else:
                    # As a final resort, try the advanced BS4 method
                    logger.info(f"Trying advanced BeautifulSoup method as last resort for {url}")
                    result = self._advanced_scrape_with_beautifulsoup(url)
                    if not result:
                        logger.error(f"Failed to scrape {url} after all methods")
                    return result
        
        return None

    def _scrape_with_beautifulsoup(self, url: str) -> Optional[ScrapedArticle]:
        """
        Fallback method to scrape a URL using BeautifulSoup if newspaper3k fails.
        
        Args:
            url: URL to scrape.
            
        Returns:
            ScrapedArticle object or None if scraping failed.
        """
        try:
            # Use a more realistic browser user agent
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/95.0.4638.69 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.google.com/",
                "DNT": "1"
            }
            
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Try to find the headline
            headline = self._extract_headline(soup)
            if not headline:
                logger.warning(f"Could not extract headline from {url}")
                # Try extracting from URL as last resort
                headline = self._extract_title_from_url(url)
            
            # Try to extract the article content
            content = self._extract_content(soup)
            if not content:
                logger.warning(f"Could not extract content from {url}")
                # Return minimal article with just the URL and headline
                return ScrapedArticle(
                    url=url,
                    headline=headline,
                    text=f"Content could not be extracted from this article. The site may be blocking automated access.\n\nURL: {url}",
                    source_domain=urlparse(url).netloc
                )
            
            # Extract additional metadata
            publish_date, authors, image_url = self._extract_metadata(soup)
            
            # Clean up the content
            content = self._clean_article_text(content)
            
            return ScrapedArticle(
                url=url,
                headline=headline,
                text=content,
                publish_date=publish_date,
                authors=authors,
                image_url=image_url,
                source_domain=urlparse(url).netloc
            )
            
        except Exception as e:
            logger.error(f"BeautifulSoup fallback failed for {url}: {str(e)}")
            
            # Create minimal article with just the URL
            parsed = urlparse(url)
            domain = parsed.netloc
            
            try:
                title = self._extract_title_from_url(url)
            except:
                title = f"Article from {domain}"
                
            return ScrapedArticle(
                url=url,
                headline=title,
                text=f"This article could not be accessed due to an error: {str(e)}\n\nURL: {url}",
                source_domain=domain
            )
        
    def _extract_headline(self, soup: BeautifulSoup) -> Optional[str]:
        """
        Extract headline from the page using various common patterns.
        
        Args:
            soup: BeautifulSoup object of the page.
            
        Returns:
            Headline string or None if not found.
        """
        # Try common HTML tags for headlines
        candidates = []
        
        # Try the h1 tag
        h1_tags = soup.find_all('h1')
        candidates.extend(h1_tags)
        
        # Try class-based selectors
        for class_name in self.HEADLINE_CLASSES:
            # Look for elements with this class
            elements = soup.find_all(class_=class_name)
            candidates.extend(elements)
            
            # Also look for h1, h2 with this class
            for tag in ['h1', 'h2']:
                elements = soup.find_all(tag, class_=class_name)
                candidates.extend(elements)
        
        # Try OpenGraph and Twitter meta tags
        og_title = soup.find('meta', property='og:title')
        if og_title:
            candidates.append(og_title)
            
        twitter_title = soup.find('meta', {'name': 'twitter:title'})
        if twitter_title:
            candidates.append(twitter_title)
        
        # Process candidates to extract text
        for candidate in candidates:
            if candidate.name == 'meta':
                title = candidate.get('content')
                if title:
                    return title.strip()
            else:
                title = candidate.get_text()
                if title:
                    return title.strip()
        
        return None

    def _extract_content(self, soup: BeautifulSoup) -> Optional[str]:
        """
        Extract article content using various common patterns.
        
        Args:
            soup: BeautifulSoup object of the page.
            
        Returns:
            Article content or None if not found.
        """
        # Try class-based content containers
        for class_name in self.CONTENT_CLASSES:
            container = soup.find(class_=class_name)
            if container:
                content = self._extract_text_from_container(container)
                if content:
                    return content
        
        # Try ID-based content containers
        for id_name in self.CONTENT_IDS:
            container = soup.find(id=id_name)
            if container:
                content = self._extract_text_from_container(container)
                if content:
                    return content
        
        # Try article tag
        article = soup.find('article')
        if article:
            content = self._extract_text_from_container(article)
            if content:
                return content
                
        # Try main tag
        main_content = soup.find('main')
        if main_content:
            content = self._extract_text_from_container(main_content)
            if content:
                return content
        
        # Last resort: combine all paragraphs from the page
        paragraphs = soup.find_all('p')
        if paragraphs:
            # Filter out very short paragraphs and navigation elements
            filtered_paragraphs = []
            for p in paragraphs:
                text = p.get_text().strip()
                # Skip short paragraphs and likely navigation text
                if len(text) > 20 and not any(nav in text.lower() for nav in ['cookie', 'privacy', 'terms', 'sign up', 'log in']):
                    filtered_paragraphs.append(text)
            
            content = "\n\n".join(filtered_paragraphs)
            return content
        
        return None
        
    def _extract_text_from_container(self, container: BeautifulSoup) -> str:
        """
        Extract readable text content from a container element.
        
        Args:
            container: BeautifulSoup element containing article content.
            
        Returns:
            Extracted text content.
        """
        # Find all paragraph elements
        paragraphs = container.find_all('p')
        if paragraphs:
            return "\n\n".join([p.get_text().strip() for p in paragraphs])
        
        # If no paragraphs, get all text
        return container.get_text().strip()
        
    def _extract_metadata(self, soup: BeautifulSoup) -> Tuple[Optional[str], List[str], Optional[str]]:
        """
        Extract metadata (publish date, authors, image) from the article.
        
        Args:
            soup: BeautifulSoup object of the page.
            
        Returns:
            Tuple of (publish_date, authors, image_url)
        """
        # Extract publication date
        publish_date = None
        
        # Check meta tags first (most reliable)
        date_meta = soup.find('meta', property='article:published_time')
        if date_meta and date_meta.get('content'):
            publish_date = date_meta.get('content')
        
        # Try common date classes
        if not publish_date:
            for class_name in self.DATE_CLASSES:
                date_element = soup.find(class_=class_name)
                if date_element:
                    publish_date = date_element.get_text().strip()
                    break
        
        # Extract authors
        authors = []
        
        # Try meta tags first
        author_meta = soup.find('meta', property='article:author')
        if author_meta and author_meta.get('content'):
            authors.append(author_meta.get('content'))
        
        # Try common author classes
        if not authors:
            for class_name in self.AUTHOR_CLASSES:
                author_element = soup.find(class_=class_name)
                if author_element:
                    author_text = author_element.get_text().strip()
                    # Clean up the author text
                    author_text = re.sub(r'By |Author[s]?: ', '', author_text, flags=re.IGNORECASE)
                    authors.append(author_text)
                    break
        
        # Extract image URL
        image_url = None
        
        # Check Open Graph image tag
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            image_url = og_image.get('content')
        
        # Try Twitter card image
        if not image_url:
            twitter_image = soup.find('meta', {'name': 'twitter:image'})
            if twitter_image and twitter_image.get('content'):
                image_url = twitter_image.get('content')
        
        # Try first large image in content
        if not image_url:
            for img in soup.find_all('img'):
                if img.get('src') and (img.get('width') is None or int(img.get('width', '0')) >= 300):
                    image_url = img.get('src')
                    break
        
        return publish_date, authors, image_url

    @staticmethod
    def _host_key(url: str) -> str:
        """
        Return the throttle key for a URL: its host name (including an explicit
        port), lower-cased. URLs without a host resolve to an empty string, which
        disables throttling for them.
        """
        try:
            if not isinstance(url, str):
                return ""
            parsed = urlparse(url)
        except (AttributeError, TypeError, ValueError):
            return ""
        netloc = parsed.netloc.lower()
        # Ignore any userinfo, the throttle is per site, not per credentials.
        if "@" in netloc:
            netloc = netloc.rsplit("@", 1)[1]
        return netloc

    def _throttle(self, host: str) -> float:
        """
        Wait if the previous scrape of ``host`` started less than the minimum
        interval ago, then remember the start of this scrape.

        Spacing is measured between request starts against a monotonic clock,
        so a slow previous request already satisfies the interval and no extra
        sleep happens. Only the host being scraped is considered; other hosts
        never block each other. Throttling never raises: if waiting fails, the
        scrape still proceeds.

        Args:
            host: Host key obtained from :meth:`_host_key`.

        Returns:
            Milliseconds actually spent waiting.
        """
        interval = self.min_interval_ms / 1000.0
        if interval <= 0 or not host:
            return 0.0

        previous = self._host_last_start.get(host)
        waited = 0.0
        if previous is not None:
            remaining = previous + interval - self._clock()
            if remaining > 0:
                started = self._clock()
                try:
                    self._sleep(remaining)
                except Exception as exc:  # throttling must not fail the scrape
                    logger.warning(
                        "Could not wait %.0f ms before scraping %s: %s",
                        remaining * 1000.0, host, exc,
                    )
                waited = max(0.0, (self._clock() - started) * 1000.0)
        self._host_last_start[host] = self._clock()
        return waited

    def _classify_result(self, article: Optional[ScrapedArticle]) -> Tuple[Optional[str], Optional[str]]:
        """
        Derive a failure reason for a scrape result, if it is not usable.

        Args:
            article: Result returned by :meth:`scrape_url`.

        Returns:
            Tuple ``(failure_reason, error_detail)``. Both are ``None`` when the
            article is usable.
        """
        if article is None:
            return FailureReason.EMPTY_RESULT, None
        if not article.text or not article.text.strip():
            return FailureReason.EMPTY_CONTENT, None
        return None, None

    def scrape_urls(self, urls: List[str]) -> ScrapeBatchResult:
        """
        Scrape multiple URLs, throttling requests per host.

        URLs are still requested one after another, but consecutive requests to
        the same host (host name including port, lower-cased) are spaced by at
        least ``min_interval_ms``. Requests to different hosts never wait for
        each other. An exception raised while scraping one URL is recorded as a
        failed outcome and does not abort the remaining URLs.

        Args:
            urls: List of URLs to scrape.

        Returns:
            ScrapeBatchResult: A list of successfully scraped ScrapedArticle
            objects (unchanged shape for old callers) that also carries
            ``outcomes`` with one record per input URL, in input order, plus
            success/failure/wait counters.
        """
        outcomes: List[ScrapeOutcome] = []

        for url in tqdm(urls, desc="Scraping articles"):
            host = self._host_key(url)
            try:
                waited_ms = self._throttle(host)
            except Exception as exc:  # throttling must never fail a scrape
                logger.warning(f"Could not throttle requests to {host}: {exc}")
                waited_ms = 0.0

            article: Optional[ScrapedArticle] = None
            failure_reason: Optional[str] = None
            error: Optional[str] = None
            try:
                article = self.scrape_url(url)
                failure_reason, error = self._classify_result(article)
            except Exception as exc:  # never let one URL abort the batch
                logger.error(f"Error scraping {url}: {exc}")
                failure_reason, error = FailureReason.REQUEST_OR_PARSE_ERROR, str(exc)
                article = None

            if failure_reason is None and article is not None:
                outcomes.append(ScrapeOutcome(
                    url=url,
                    host=host,
                    status="success",
                    waited_ms=waited_ms,
                    headline=article.headline,
                    text_length=len(article.text or ""),
                    article=article,
                ))
            else:
                outcomes.append(ScrapeOutcome(
                    url=url,
                    host=host,
                    status="failed",
                    waited_ms=waited_ms,
                    failure_reason=failure_reason,
                    error=error,
                ))

        result = ScrapeBatchResult(outcomes)
        logger.info(
            "Batch scrape finished: %d succeeded, %d failed out of %d URL(s); "
            "waited %.0f ms on per-host throttling",
            result.success_count,
            result.failure_count,
            result.input_count,
            result.total_waited_ms,
        )
        return result

    def _extract_with_beautiful_soup(self, url: str) -> Optional[ScrapedArticle]:
        """
        Extract article content using BeautifulSoup as a fallback method.
        
        Args:
            url: URL to extract from.
            
        Returns:
            Optional[ScrapedArticle]: Scraped article or None if failed.
        """
        try:
            # Make the request with headers to simulate a browser
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, "html.parser")
            
            # Try to extract the title
            title = None
            for title_tag in [
                soup.find("h1"),  # Most common
                soup.find("meta", property="og:title"),  # Open Graph
                soup.find("meta", {"name": "twitter:title"}),  # Twitter
                soup.find("title")  # HTML title
            ]:
                if title_tag:
                    if hasattr(title_tag, "content"):
                        title = title_tag["content"]
                    else:
                        title = title_tag.get_text().strip()
                    break
                
            if not title:
                title = "Unknown Title"
                
            # Try multiple strategies for content extraction
            content = ""
            
            # Strategy 1: Look for article tag
            article_tag = soup.find("article")
            if article_tag:
                content = article_tag.get_text()
                
            # Strategy 2: Look for common content containers
            if not content:
                for container in soup.find_all(["div", "section"], class_=lambda c: c and any(x in str(c).lower() for x in ["content", "article", "story", "body", "text", "main"])):
                    if len(container.get_text()) > 200:  # Likely main content if it has substantial text
                        content = container.get_text()
                        break
                    
            # Strategy 3: Find all paragraphs in the document
            if not content:
                # Get all paragraphs
                paragraphs = soup.find_all("p")
                content = "\n".join(p.get_text().strip() for p in paragraphs if len(p.get_text().strip()) > 40)
                
            # Clean up the content
            content = self._clean_article_text(content)
            
            if not content:
                logger.error(f"Failed to extract content from {url} using BeautifulSoup")
                return None
            
            # Create the article
            return ScrapedArticle(
                url=url,
                title=title,
                text=content,
                html=str(soup),
                publish_date=None  # We don't try to extract date with BS4
            )
        
        except Exception as e:
            logger.error(f"BeautifulSoup fallback failed for {url}: {str(e)}")
            return None
        
    def _clean_article_text(self, text: str) -> str:
        """
        Clean up article text by removing extra whitespace, etc.
        
        Args:
            text: Raw article text.
            
        Returns:
            str: Cleaned text.
        """
        if not text:
            return ""
        
        # Replace multiple newlines with a single one
        text = re.sub(r'\n\s*\n', '\n\n', text)
        
        # Replace multiple spaces with a single one
        text = re.sub(r' +', ' ', text)
        
        # Remove whitespace at the beginning and end of each line
        text = '\n'.join(line.strip() for line in text.split('\n'))
        
        # Remove boilerplate text often found in articles
        patterns_to_remove = [
            r'Related Articles',
            r'Share this article',
            r'Share on Facebook',
            r'Share on Twitter',
            r'Share via Email',
            r'Advertisement',
            r'Click to follow',
            r'Follow us',
            r'Subscribe to our newsletter',
            r'Terms of [Ss]ervice',
            r'Privacy [Pp]olicy',
            r'Copyright ©'
        ]
        
        for pattern in patterns_to_remove:
            text = re.sub(pattern, '', text)
        
        return text.strip()

    def _handle_reuters_article(self, url: str) -> Optional[ScrapedArticle]:
        """
        Special handler for Reuters articles which often block standard scrapers.
        
        Args:
            url: URL to scrape.
            
        Returns:
            ScrapedArticle object or None if scraping failed.
        """
        try:
            # Use a completely different set of headers that mimic a real browser more closely
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.4 Safari/605.1.15',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Referer': 'https://www.google.com/',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'cross-site',
                'Sec-Fetch-User': '?1',
                'Upgrade-Insecure-Requests': '1'
            }
            
            # Add a delay to avoid rate limiting
            time.sleep(2 + (time.time() % 3))  # Random delay between 2-5 seconds
            
            # Use a raw requests get to bypass newspaper's request handling
            response = requests.get(url, headers=headers, timeout=20)
            
            # Check if we got a successful response
            if response.status_code != 200:
                logger.error(f"Failed to fetch Reuters article: {response.status_code}")
                
                # Generate a minimal article with the information we have
                headline = self._extract_title_from_url(url)
                return ScrapedArticle(
                    url=url,
                    headline=headline or "Reuters Article (Access Restricted)",
                    text=f"This article from Reuters could not be accessed due to access restrictions.\n\nURL: {url}",
                    authors=["Reuters"],
                    source_domain="reuters.com"
                )
            
            # Try to parse with BeautifulSoup
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Extract headline
            headline = None
            headline_selectors = [
                'h1.article-header__title__3R6zT',  # Reuters common headline class
                'h1.text__text__1FZLe',              # Another Reuters headline class
                'h1.article-header__heading',        # Yet another Reuters variant
                'h1',                                # Fallback to any h1
            ]
            
            for selector in headline_selectors:
                if selector.startswith('h1.'):
                    # Class-based selector
                    class_name = selector.split('.')[1]
                    headline_elem = soup.find('h1', class_=class_name)
                else:
                    # Tag-based selector
                    headline_elem = soup.find(selector)
                    
                if headline_elem:
                    headline = headline_elem.get_text().strip()
                    break
                    
            if not headline:
                # Try meta tags
                meta_title = soup.find('meta', property='og:title')
                if meta_title:
                    headline = meta_title.get('content', '').strip()
                    
            if not headline:
                headline = self._extract_title_from_url(url)
            
            # Extract content - Reuters usually has article bodies in specific divs
            content_selectors = [
                '.article-body__content__3VtU3',  # Common Reuters content class
                '.paywall-article',              # Another Reuters content area
                '.StandardArticleBody_body',      # Another Reuters body class
                '.article-body'                   # Generic fallback
            ]
            
            content = ""
            for selector in content_selectors:
                content_div = soup.select_one(selector)
                if content_div:
                    paragraphs = content_div.find_all('p')
                    if paragraphs:
                        content = "\n\n".join(p.get_text().strip() for p in paragraphs)
                        break
            
            # If we still don't have content, try a more general approach
            if not content:
                # Get all paragraphs with reasonable length
                paragraphs = [p.get_text().strip() for p in soup.find_all('p') 
                             if len(p.get_text().strip()) > 40]
                if paragraphs:
                    content = "\n\n".join(paragraphs)
            
            # If we got content, create an article
            if content:
                return ScrapedArticle(
                    url=url,
                    headline=headline or "Reuters Article",
                    text=content,
                    authors=["Reuters"],  # Default author
                    source_domain="reuters.com"
                )
            else:
                # Create minimal article if we couldn't get content
                return ScrapedArticle(
                    url=url,
                    headline=headline or "Reuters Article (Limited Access)",
                    text=f"This Reuters article could not be fully accessed. Only the headline was retrieved.\n\nURL: {url}",
                    authors=["Reuters"],
                    source_domain="reuters.com"
                )
                
        except Exception as e:
            logger.error(f"Reuters specialized handler failed for {url}: {str(e)}")
            # Create a placeholder article as a last resort
            return ScrapedArticle(
                url=url,
                headline="Reuters Article (Scraping Failed)",
                text=f"Failed to access this Reuters article. The website may be blocking automated access.\n\nURL: {url}",
                authors=["Reuters"],
                source_domain="reuters.com"
            )
            
    def _extract_title_from_url(self, url: str) -> str:
        """
        Extract a title from the URL if all else fails.
        
        Args:
            url: The article URL.
            
        Returns:
            A title derived from the URL.
        """
        # Parse URL path to extract potential title
        parsed = urlparse(url)
        path = parsed.path
        
        # Remove any file extensions and trailing slashes
        path = path.rstrip('/')
        if path.endswith(('.html', '.htm')):
            path = path[:-5]
            
        # Split the path into segments
        segments = [s for s in path.split('/') if s]
        
        # The last segment is usually the slug
        if segments:
            slug = segments[-1]
            
            # Replace hyphens and underscores with spaces
            title = slug.replace('-', ' ').replace('_', ' ')
            
            # Capitalize words
            title = ' '.join(word.capitalize() for word in title.split())
            
            return title
        
        # Fallback to domain name
        return f"Article from {parsed.netloc}"
            
    def _advanced_scrape_with_beautifulsoup(self, url: str) -> Optional[ScrapedArticle]:
        """
        Enhanced BeautifulSoup scraping with more sophisticated content extraction.
        
        Args:
            url: URL to scrape.
            
        Returns:
            ScrapedArticle object or None if scraping failed.
        """
        try:
            # Vary the request to appear more like a real browser
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/94.0.4606.81 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Referer": "https://www.google.com/",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Cache-Control": "max-age=0"
            }
            
            # Add a random delay to avoid detection
            time.sleep(1 + (time.time() % 2))
            
            response = requests.get(url, headers=headers, timeout=20)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Enhanced title extraction - try multiple approaches
            headline = None
            
            # Method 1: Try standard meta tags
            meta_title_options = [
                ('meta', {'property': 'og:title'}),
                ('meta', {'property': 'twitter:title'}),
                ('meta', {'name': 'twitter:title'}),
                ('meta', {'name': 'title'}),
                ('title', {}),  # Regular HTML title tag
            ]
            
            for tag, attrs in meta_title_options:
                element = soup.find(tag, attrs)
                if element:
                    if tag == 'title':
                        headline = element.get_text().strip()
                    else:
                        headline = element.get('content', '').strip()
                    break
            
            # Method 2: Try h1 tags if no meta title was found
            if not headline:
                h1s = soup.find_all('h1')
                for h1 in h1s:
                    # Skip very short h1s or those likely in the site header/navigation
                    text = h1.get_text().strip()
                    if len(text) > 15:  # Reasonable length for a title
                        headline = text
                        break
            
            # Method 3: Extract from URL as last resort
            if not headline:
                headline = self._extract_title_from_url(url)
            
            # Enhanced content extraction - try multiple approaches
            content = ""
            
            # Method 1: Look for article tags
            article_tag = soup.find('article')
            if article_tag:
                # Get paragraphs from the article
                paragraphs = article_tag.find_all('p')
                if paragraphs:
                    content = "\n\n".join(p.get_text().strip() for p in paragraphs)
            
            # Method 2: Look for content in likely containers
            if not content:
                content_containers = [
                    # Class-based common containers
                    ('div', {'class': lambda x: x and any(c in str(x).lower() for c in 
                             ['article', 'content', 'story', 'body', 'text', 'entry'])}),
                    # ID-based common containers
                    ('div', {'id': lambda x: x and any(c in str(x).lower() for c in 
                             ['article', 'content', 'story', 'body', 'text', 'entry'])}),
                ]
                
                for tag, attrs in content_containers:
                    containers = soup.find_all(tag, attrs)
                    for container in containers:
                        paragraphs = container.find_all('p')
                        if len(paragraphs) >= 3:  # Likely a content area if it has several paragraphs
                            content = "\n\n".join(p.get_text().strip() for p in paragraphs)
                            break
                    if content:
                        break
            
            # Method 3: Get all substantial paragraphs as a last resort
            if not content:
                paragraphs = [
                    p.get_text().strip() for p in soup.find_all('p')
                    if len(p.get_text().strip()) > 40 and  # Skip short paragraphs
                       not any(nav in p.get_text().lower() for nav in 
                              ['cookie', 'privacy', 'terms', 'sign up', 'log in'])
                ]
                if paragraphs:
                    content = "\n\n".join(paragraphs)
            
            # Fallback content
            if not content:
                logger.warning(f"Could not extract content from {url}")
                # Create partial article with what we have
                content = f"[Content extraction failed for this article. The website may be using techniques to prevent automated reading.]"
            
            # Extract author information
            authors = []
            author_containers = [
                ('meta', {'property': 'article:author'}),
                ('meta', {'name': 'author'}),
                ('a', {'rel': 'author'}),
                ('span', {'class': lambda x: x and 'author' in str(x).lower()}),
                ('div', {'class': lambda x: x and 'author' in str(x).lower()}),
                ('p', {'class': lambda x: x and 'byline' in str(x).lower()}),
            ]
            
            for tag, attrs in author_containers:
                elements = soup.find_all(tag, attrs)
                for element in elements:
                    if tag == 'meta':
                        author = element.get('content')
                        if author:
                            authors.append(author)
                    else:
                        author = element.get_text().strip()
                        if author:
                            # Clean up author text
                            author = re.sub(r'^By\s+|^Author[s]?:?\s+', '', author, flags=re.IGNORECASE)
                            authors.append(author)
                if authors:
                    break
            
            # Extract published date
            publish_date = None
            date_containers = [
                ('meta', {'property': 'article:published_time'}),
                ('meta', {'property': 'article:published'}),
                ('meta', {'name': 'article:published_time'}),
                ('meta', {'name': 'date'}),
                ('time', {}),
                ('span', {'class': lambda x: x and any(d in str(x).lower() for d in ['date', 'time', 'published'])}),
            ]
            
            for tag, attrs in date_containers:
                elements = soup.find_all(tag, attrs)
                for element in elements:
                    if tag == 'meta':
                        date_str = element.get('content')
                        if date_str:
                            publish_date = date_str
                            break
                    else:
                        date_str = element.get_text().strip()
                        if date_str and len(date_str) > 5:  # Skip too short strings
                            publish_date = date_str
                            break
                if publish_date:
                    break
            
            # Extract image URL
            image_url = None
            image_containers = [
                ('meta', {'property': 'og:image'}),
                ('meta', {'name': 'twitter:image'}),
                ('meta', {'name': 'thumbnail'}),
            ]
            
            for tag, attrs in image_containers:
                element = soup.find(tag, attrs)
                if element:
                    image_url = element.get('content')
                    if image_url:
                        break
            
            # If no image found, try to find the first large image
            if not image_url:
                for img in soup.find_all('img'):
                    src = img.get('src')
                    if src and not src.endswith(('.gif', '.svg')):
                        # Check if it has width/height attributes
                        width = img.get('width')
                        height = img.get('height')
                        if width and height:
                            try:
                                if int(width) >= 300 and int(height) >= 200:
                                    image_url = src
                                    break
                            except ValueError:
                                pass
            
            # Clean up the content
            content = self._clean_article_text(content)
            
            # Create the article
            return ScrapedArticle(
                url=url,
                headline=headline,
                text=content,
                publish_date=publish_date,
                authors=authors,
                source_domain=urlparse(url).netloc,
                image_url=image_url
            )
            
        except Exception as e:
            logger.error(f"Advanced BeautifulSoup scraping failed for {url}: {str(e)}")
            try:
                # Last resort: Create minimal article from URL
                parsed = urlparse(url)
                domain = parsed.netloc
                headline = self._extract_title_from_url(url)
                
                return ScrapedArticle(
                    url=url,
                    headline=headline,
                    text=f"This article from {domain} could not be accessed.\n\nURL: {url}",
                    source_domain=domain
                )
            except:
                return None
