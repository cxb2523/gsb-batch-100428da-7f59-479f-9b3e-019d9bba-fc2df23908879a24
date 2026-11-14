import asyncio
import logging
from typing import Dict, List, Optional, Set
from urllib.robotparser import RobotFileParser

from feedsearch_crawler.crawler.middleware.base import BaseDownloaderMiddleware
from feedsearch_crawler.crawler.request import Request
from feedsearch_crawler.crawler.response import Response
from feedsearch_crawler.exceptions import RobotsBlockedError

logger = logging.getLogger(__name__)

# Upper bound, in seconds, on how long an ordinary request waits for its host's
# robots.txt result. A robots fetch that hangs must never stall a whole crawl.
DEFAULT_ROBOTS_TIMEOUT: float = 5.0


class RobotsMiddleware(BaseDownloaderMiddleware):
    """Blocks requests disallowed by robots.txt.

    The robots.txt content itself is fetched asynchronously by the crawler
    (queued at highest priority at crawl start) and registered here via
    ``register_robots_txt``. This middleware never performs any I/O of its
    own.

    Instead of the old permissive-before-registration behaviour, ordinary
    requests for an origin are held until that origin's robots.txt result has
    been registered, or until ``timeout`` expires. The crawler calls
    ``expect_robots_txt`` when it queues a robots.txt request so that the wait
    applies exactly to origins whose rules are still being fetched. A result
    that is empty, failed, or unparseable is registered as "no rules" and
    releases all waiters.

    robots.txt requests themselves never wait and are never blocked by the
    rules they are fetching, which also means they can never deadlock behind
    the requests waiting for them.
    """

    def __init__(
        self,
        user_agent: str = "Feedsearch-Crawler",
        timeout: float = DEFAULT_ROBOTS_TIMEOUT,
    ) -> None:
        self.user_agent = user_agent
        # A timeout of 0 (or less) disables waiting entirely: requests are
        # allowed while the rules are still being fetched, but still checked
        # against them once registered.
        self.timeout = DEFAULT_ROBOTS_TIMEOUT if timeout is None else float(timeout)
        self.cache: Dict[str, Optional[RobotFileParser]] = {}
        self.sitemap_urls: Dict[str, List[str]] = {}  # host -> list of sitemap URLs
        # Canonical robots.txt key -> event set once a result is registered.
        self._ready_events: Dict[str, asyncio.Event] = {}
        # Origins (canonical robots.txt keys) with a robots.txt fetch queued.
        self._expected: Set[str] = set()

    @staticmethod
    def cache_key(request_url) -> str:
        """Canonical robots.txt key for a URL: scheme + host + port.

        Using the URL origin (rather than ``with_path`` on the request URL)
        normalises default ports, host case and userinfo, so a robots.txt
        registered from a retried or redirected fetch always matches the keys
        later requests look up.
        """
        if isinstance(request_url, str):
            from yarl import URL

            try:
                request_url = URL(request_url)
            except (ValueError, TypeError):
                return ""
        try:
            origin = request_url.origin()
        except (ValueError, AttributeError):
            return ""
        if not origin.host:
            return ""
        return str(origin.with_path("/robots.txt"))

    @staticmethod
    def _cache_key(request_url) -> str:
        """Backwards-compatible alias for :meth:`cache_key`."""
        return RobotsMiddleware.cache_key(request_url)

    @staticmethod
    def is_robots_request(url) -> bool:
        """Whether the URL is a robots.txt request.

        These requests are exempt from both waiting and rule checks.
        """
        return bool(url) and url.path == "/robots.txt"

    def expect_robots_txt(self, robots_url) -> None:
        """Mark an origin as having a robots.txt fetch in flight.

        Ordinary requests for that origin wait for the result instead of being
        allowed immediately.

        :param robots_url: The robots.txt URL that has been queued.
        """
        key = self.cache_key(robots_url)
        if not key:
            return
        self._expected.add(key)
        if key not in self.cache:
            self._ready_events.setdefault(key, asyncio.Event())

    def is_expected(self, url) -> bool:
        """Whether a robots.txt fetch is known to be pending for the URL's origin."""
        return self.cache_key(url) in self._expected

    def is_registered(self, url) -> bool:
        """Whether a robots.txt result is already cached for the URL's origin."""
        return self.cache_key(url) in self.cache

    def register_robots_txt(self, robots_url, text: Optional[str]) -> None:
        """Register fetched robots.txt content for a host.

        Empty, failed, or unparseable content is registered as ``None`` (no
        rules), which unblocks any requests waiting for this origin.

        :param robots_url: The robots.txt URL that was fetched.
        :param text: robots.txt content, or None if unavailable.
        """
        key = self.cache_key(robots_url) or str(robots_url)

        if not text:
            # Cache None to indicate robots.txt is unavailable
            self.cache[key] = None
            self._mark_ready(key)
            return

        try:
            rp = RobotFileParser()
            rp.set_url(key)
            rp.parse(text.splitlines())
        except Exception as e:
            logger.debug("Failed to parse robots.txt from %s: %s", key, e)
            self.cache[key] = None
        else:
            self.cache[key] = rp
            self._extract_sitemaps(key, rp)

        self._mark_ready(key)

    def _mark_ready(self, key: str) -> None:
        """Release requests waiting for the robots.txt result of a key."""
        event = self._ready_events.get(key)
        if event is not None:
            event.set()

    async def wait_until_ready(self, request: Request) -> None:
        """Wait, with an upper bound, for the request's robots.txt result.

        Only origins announced through ``expect_robots_txt`` wait. The crawler
        announces every robots.txt fetch it queues, so exactly those origins
        are gated; a host it never fetches rules for is allowed without delay.

        If the result has already been registered this returns immediately.
        If a result is never registered, the wait times out and the origin is
        treated as having no rules; later requests for it do not wait again.

        :param request: The request about to be sent.
        """
        key = self.cache_key(request.url)
        if not key or key in self.cache:
            return

        event = self._ready_events.get(key)
        if event is None:
            # No robots.txt fetch is in flight for this origin.
            return

        if not event.is_set():
            if self.timeout <= 0:
                # Waiting disabled: proceed without the rules.
                return
            try:
                await asyncio.wait_for(event.wait(), timeout=self.timeout)
            except asyncio.TimeoutError:
                logger.debug(
                    "Timed out after %.2fs waiting for robots.txt result for %s; "
                    "treating as no rules",
                    self.timeout,
                    key,
                )
                # Treat as no rules so neither this request nor later ones wait.
                self.cache.setdefault(key, None)
                event.set()
                return

        # Defensive: an event that was set without a registration means no rules.
        self.cache.setdefault(key, None)

    def is_allowed(self, request: Request) -> bool:
        """Whether the request is permitted by the registered rules."""
        rp = self.cache.get(self.cache_key(request.url))
        # Unknown or unavailable robots.txt allows the request (permissive).
        return not rp or rp.can_fetch(self.user_agent, str(request.url))

    def check_request(self, request: Request) -> None:
        """Raise RobotsBlockedError if the request is disallowed."""
        if not self.is_allowed(request):
            raise RobotsBlockedError(f"Blocked by robots.txt: {request.url}")

    async def pre_request(self, request: Request) -> None:
        """Wait for rules, then block the request if robots.txt disallows it."""
        if not request.url.host:
            return  # Allow requests without host

        # The robots.txt request itself must never be blocked or wait.
        if self.is_robots_request(request.url):
            return

        await self.wait_until_ready(request)
        self.check_request(request)

    async def process_request(self, request: Request) -> None:
        """Called after the request has been sent."""
        pass

    def _extract_sitemaps(self, robots_url: str, robot_parser: RobotFileParser) -> None:
        """Extract sitemap URLs from robots.txt parser.

        :param robots_url: The robots.txt URL
        :param robot_parser: Parsed RobotFileParser instance
        """
        try:
            # Extract host for storage
            from urllib.parse import urlparse

            parsed = urlparse(robots_url)
            host = f"{parsed.scheme}://{parsed.netloc}"

            # RobotFileParser exposes sitemaps via the site_maps() method
            sitemaps = robot_parser.site_maps()
            if sitemaps:
                self.sitemap_urls[host] = list(sitemaps)
                logger.debug(
                    f"Found {len(sitemaps)} sitemap(s) in {robots_url}: {sitemaps}"
                )
        except Exception as e:
            logger.debug(f"Failed to extract sitemaps from {robots_url}: {e}")

    def get_sitemaps_for_host(self, host: str) -> List[str]:
        """Get list of sitemap URLs for a given host.

        :param host: Host URL (e.g., https://example.com)
        :return: List of sitemap URLs
        """
        return self.sitemap_urls.get(host, [])

    async def process_response(self, response: Response) -> None:
        """Called after processing a response."""
        pass

    async def process_exception(self, request: Request, exception: Exception) -> None:
        """Called when an exception occurs during request processing."""
        pass
