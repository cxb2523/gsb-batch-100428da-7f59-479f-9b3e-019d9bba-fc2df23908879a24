import asyncio
import time
from typing import Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse

import httpx

from siteprobe.crawler.extractors import extract_page_data
from siteprobe.crawler.robots import RobotsParser
from siteprobe.crawler.sitemap import SitemapParser
from siteprobe.crawler.storage import CrawlStorage
from siteprobe.models.config import CrawlConfig
from siteprobe.models.crawl import (
    CrawlPage,
    CrawlSummary,
    RedirectHop,
    UrlOutcome,
    UrlOutcomeType,
)
from siteprobe.utils.url_helper import is_same_domain, is_valid_http_url, normalize_url

# More than this many redirect hops is treated as a broken chain, not followed silently.
MAX_REDIRECT_HOPS = 6
# A URL returning 429/503 is retried this many times (with backoff) before being abandoned.
MAX_RATE_LIMIT_RETRIES = 2


class BackoffController:
    """Shared polite-delay state.

    The delay lives in one place read by every worker right before sending a
    request, so a 429/503 observed by one worker raises the delay for all
    requests that have not been sent yet, including requests already queued.
    """

    def __init__(self, base_delay_seconds: float):
        self._base_delay = base_delay_seconds
        self.delay_seconds = base_delay_seconds
        self._consecutive_errors = 0

    async def wait(self) -> None:
        await asyncio.sleep(self.delay_seconds)

    def on_rate_limited(self) -> None:
        self._consecutive_errors += 1
        self.delay_seconds = min(self.delay_seconds * 2.0, 5.0)

    def on_connection_error(self) -> None:
        self._consecutive_errors += 1
        if self._consecutive_errors > 3:
            self.delay_seconds = min(self.delay_seconds * 1.5, 3.0)

    def on_success(self) -> None:
        self._consecutive_errors = max(0, self._consecutive_errors - 1)
        # Relax back toward the configured base delay as the server recovers.
        if self.delay_seconds > self._base_delay:
            self.delay_seconds = max(self._base_delay, self.delay_seconds / 1.5)


class AsyncCrawler:
    """Async polite crawler with SQLite storage, rate limiting, and adaptive backoff."""

    def __init__(self, start_url: str, config: Optional[CrawlConfig] = None, storage: Optional[CrawlStorage] = None):
        self.start_url = normalize_url(start_url)
        self.config = config or CrawlConfig()
        self.storage = storage or CrawlStorage()
        self.robots: Optional[RobotsParser] = None
        self.sitemap_urls: List[str] = []
        self.visited_urls: Set[str] = set()
        self.redirect_map: Dict[str, List[RedirectHop]] = {}
        self.backoff = BackoffController(self.config.crawl_delay_ms / 1000.0)
        # One final outcome per discovered URL, keyed by the originally requested URL.
        self._outcomes: Dict[str, UrlOutcome] = {}

    def _record_outcome(self, outcome: UrlOutcome) -> None:
        # Dict assignment guarantees a URL keeps exactly one (final) outcome.
        self._outcomes[outcome.url] = outcome

    async def crawl(self) -> CrawlSummary:
        start_time = time.time()
        await self.storage.initialize()

        limits = httpx.Limits(max_keepalive_connections=self.config.concurrency, max_connections=self.config.concurrency * 2)
        headers = {"User-Agent": self.config.user_agent, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}

        async with httpx.AsyncClient(headers=headers, limits=limits, timeout=self.config.timeout_seconds, follow_redirects=False, trust_env=self.config.trust_env) as client:
            # 1. Fetch robots.txt
            await self._fetch_robots(client)

            # 2. Fetch sitemap.xml
            await self._fetch_sitemaps(client)

            # 3. Setup queue
            queue: asyncio.Queue = asyncio.Queue()
            await queue.put((self.start_url, 0))
            self.visited_urls.add(self.start_url)

            # Enqueue discovered sitemap URLs
            for s_url in self.sitemap_urls:
                norm_s = normalize_url(s_url)
                if is_same_domain(self.start_url, norm_s) and norm_s not in self.visited_urls:
                    if len(self.visited_urls) < self.config.max_pages:
                        self.visited_urls.add(norm_s)
                        await queue.put((norm_s, 1))

            semaphore = asyncio.Semaphore(self.config.concurrency)
            active_tasks = set()
            pages_crawled = 0

            while (not queue.empty() or active_tasks) and pages_crawled < self.config.max_pages:
                while not queue.empty() and pages_crawled + len(active_tasks) < self.config.max_pages:
                    url, depth = await queue.get()

                    # Check robots.txt
                    parsed_path = urlparse(url).path or "/"
                    if self.config.respect_robots and self.robots and not self.robots.is_allowed("SiteProbe", parsed_path):
                        self._record_outcome(UrlOutcome(
                            url=url,
                            outcome=UrlOutcomeType.ROBOTS_DISALLOWED,
                            detail=f"Blocked by robots.txt rule for path {parsed_path}",
                        ))
                        queue.task_done()
                        continue

                    task = asyncio.create_task(self._crawl_single(client, url, depth, semaphore, queue))
                    active_tasks.add(task)
                    task.add_done_callback(lambda t: queue.task_done())

                if not active_tasks:
                    break

                done, active_tasks = await asyncio.wait(active_tasks, return_when=asyncio.FIRST_COMPLETED)
                pages_crawled += len(done)

            # Wait for remaining active tasks
            if active_tasks:
                await asyncio.gather(*active_tasks, return_exceptions=True)

        duration = time.time() - start_time
        all_pages = await self.storage.get_all_pages()
        status_counts: Dict[str, int] = {}
        for p in all_pages:
            code_str = str(p.status_code)
            status_counts[code_str] = status_counts.get(code_str, 0) + 1

        return CrawlSummary(
            start_url=self.start_url,
            total_crawled=len(all_pages),
            total_internal_discovered=len(self.visited_urls),
            total_external_discovered=0,
            broken_links_count=sum(1 for p in all_pages if p.status_code >= 400),
            crawl_duration_seconds=round(duration, 2),
            status_codes_distribution=status_counts,
            url_outcomes=list(self._outcomes.values()),
        )

    async def _fetch_robots(self, client: httpx.AsyncClient) -> None:
        parsed = urlparse(self.start_url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        try:
            resp = await client.get(robots_url, timeout=5.0)
            if resp.status_code == 200:
                self.robots = RobotsParser(resp.text)
            else:
                self.robots = RobotsParser("")
        except Exception:
            self.robots = RobotsParser("")

    async def _fetch_sitemaps(self, client: httpx.AsyncClient) -> None:
        declared_sitemaps = self.robots.sitemaps if self.robots else []
        parsed = urlparse(self.start_url)
        default_sitemap = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"
        if default_sitemap not in declared_sitemaps:
            declared_sitemaps.append(default_sitemap)

        for sm_url in declared_sitemaps:
            found = await SitemapParser.fetch_sitemap_urls(sm_url, client)
            self.sitemap_urls.extend(found)
        self.sitemap_urls = list(dict.fromkeys(self.sitemap_urls))

    async def _crawl_single(self, client: httpx.AsyncClient, url: str, depth: int, semaphore: asyncio.Semaphore, queue: asyncio.Queue) -> None:
        async with semaphore:
            current_url = url
            redirect_chain: List[RedirectHop] = []
            redirect_hops = 0
            response: Optional[httpx.Response] = None

            t0 = time.time()
            try:
                # Manual redirect loop with a hard hop budget and bounded 429/503 retries.
                rate_attempt = 0
                while True:
                    resp: Optional[httpx.Response] = None
                    while True:
                        # Re-read the shared delay on every attempt so backoff raised by
                        # another worker applies to requests not yet sent.
                        await self.backoff.wait()
                        try:
                            resp = await client.get(current_url)
                            break
                        except Exception as e:
                            resp_time = (time.time() - t0) * 1000
                            self.backoff.on_connection_error()
                            error_page = CrawlPage(
                                url=url,
                                status_code=0,
                                content_type="error/connection-failure",
                                html=f"<!-- Error: {str(e)} -->",
                                response_time_ms=resp_time,
                                depth=depth,
                                is_indexable=False,
                            )
                            await self.storage.save_page(error_page)
                            self._record_outcome(UrlOutcome(
                                url=url,
                                outcome=UrlOutcomeType.CONNECTION_FAILED,
                                status_code=0,
                                final_url=current_url if current_url != url else None,
                                redirect_hops=redirect_hops,
                                redirect_chain=redirect_chain,
                                detail=str(e),
                            ))
                            return

                    if resp.status_code in (429, 503):
                        # Raise the shared delay; subsequent queued requests honor it.
                        self.backoff.on_rate_limited()
                        if rate_attempt < MAX_RATE_LIMIT_RETRIES:
                            rate_attempt += 1
                            continue
                        # Retries exhausted: give up but still persist the final response as a
                        # page (preserving the original total_crawled / broken_links semantics)
                        # and record the explicit abandonment outcome.
                        resp_time = (time.time() - t0) * 1000
                        abandoned_page = CrawlPage(
                            url=url,
                            status_code=resp.status_code,
                            content_type=resp.headers.get("content-type", ""),
                            headers=dict(resp.headers.items()),
                            html=resp.text if "text/html" in resp.headers.get("content-type", "") else "",
                            response_time_ms=resp_time,
                            depth=depth,
                            is_indexable=False,
                            redirect_chain=redirect_chain,
                            redirect_hops=redirect_hops,
                            final_url=current_url if current_url != url else None,
                        )
                        await self.storage.save_page(abandoned_page)
                        self._record_outcome(UrlOutcome(
                            url=url,
                            outcome=UrlOutcomeType.RATE_LIMITED_ABANDONED,
                            status_code=resp.status_code,
                            final_url=current_url if current_url != url else None,
                            redirect_hops=redirect_hops,
                            redirect_chain=redirect_chain,
                            detail=(
                                f"HTTP {resp.status_code} persisted after {MAX_RATE_LIMIT_RETRIES} "
                                f"backoff retries ({round(resp_time, 1)}ms)"
                            ),
                        ))
                        return

                    # A non-rate-limited response terminates this hop.
                    redirect_chain.append(RedirectHop(url=current_url, status_code=resp.status_code))
                    if resp.is_redirect and "location" in resp.headers:
                        loc = resp.headers["location"]
                        current_url = normalize_url(urljoin(current_url, loc))
                        redirect_hops += 1
                        # Each hop gets its own bounded set of 429/503 retries.
                        rate_attempt = 0
                        if redirect_hops > MAX_REDIRECT_HOPS:
                            self._record_outcome(UrlOutcome(
                                url=url,
                                outcome=UrlOutcomeType.REDIRECT_LIMIT_EXCEEDED,
                                status_code=resp.status_code,
                                final_url=current_url,
                                redirect_hops=redirect_hops,
                                redirect_chain=redirect_chain,
                                detail=f"Redirect chain exceeded {MAX_REDIRECT_HOPS} hops",
                            ))
                            return
                        continue

                    response = resp
                    break
            except Exception as e:
                # Safety net so an unexpected failure still leaves a verifiable outcome.
                self.backoff.on_connection_error()
                self._record_outcome(UrlOutcome(
                    url=url,
                    outcome=UrlOutcomeType.CONNECTION_FAILED,
                    status_code=0,
                    final_url=current_url if current_url != url else None,
                    redirect_hops=redirect_hops,
                    redirect_chain=redirect_chain,
                    detail=str(e),
                ))
                return

            # Reaching a final response means the server recovered from any earlier strain.
            self.backoff.on_success()
            resp_time = (time.time() - t0) * 1000

            try:
                # Extract against the landing URL so relative links/canonicals resolve correctly,
                # then re-key the page to the originally requested URL so it is retrievable by it.
                html_text = response.text if "text/html" in response.headers.get("content-type", "") or response.text.startswith("<") else ""
                dict_headers = dict(response.headers.items())

                page_data = extract_page_data(
                    url=current_url,
                    html=html_text,
                    status_code=response.status_code,
                    headers=dict_headers,
                    response_time_ms=resp_time,
                    depth=depth,
                )
                page_data.url = url
                page_data.final_url = current_url
                page_data.redirect_chain = redirect_chain
                page_data.redirect_hops = redirect_hops
                await self.storage.save_page(page_data)

                self._record_outcome(UrlOutcome(
                    url=url,
                    outcome=UrlOutcomeType.REDIRECT_SUCCESS if redirect_hops > 0 else UrlOutcomeType.SUCCESS,
                    status_code=response.status_code,
                    final_url=current_url if redirect_hops > 0 else None,
                    redirect_hops=redirect_hops,
                    redirect_chain=redirect_chain,
                ))
            except Exception as e:
                # A response was received but could not be processed; record rather than drop.
                self._record_outcome(UrlOutcome(
                    url=url,
                    outcome=UrlOutcomeType.CONNECTION_FAILED,
                    status_code=response.status_code,
                    final_url=current_url if current_url != url else None,
                    redirect_hops=redirect_hops,
                    redirect_chain=redirect_chain,
                    detail=f"Post-response processing failed: {e}",
                ))
                return

            # Discover internal links for deeper crawling
            if depth < self.config.max_depth and page_data.html:
                for link in page_data.links:
                    if link.get("internal"):
                        target_url = link.get("href", "")
                        if target_url and target_url not in self.visited_urls and is_valid_http_url(target_url):
                            if len(self.visited_urls) < self.config.max_pages:
                                self.visited_urls.add(target_url)
                                await queue.put((target_url, depth + 1))
