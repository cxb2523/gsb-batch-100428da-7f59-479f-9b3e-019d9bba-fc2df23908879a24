import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import urlparse

import httpx

from .robots import USER_AGENT

_NS_SITEMAP = "http://www.sitemaps.org/schemas/sitemap/0.9"
_HEADERS = {"User-Agent": USER_AGENT}
_MAX_DEPTH = 3  # maximum sitemap index recursion depth
SITEMAP_MAX_URLS = 5000  # cap per domain to avoid queue explosion


class SitemapStatus(StrEnum):
    """Outcome of fetching a single sitemap (index or urlset)."""

    SUCCESS = "success"
    NON_200 = "non_200"
    PARSE_ERROR = "parse_error"
    NETWORK_ERROR = "network_error"
    DEPTH_EXCEEDED = "depth_exceeded"
    DUPLICATE_SKIPPED = "duplicate_skipped"
    NON_HTTP_FILTERED = "non_http_filtered"


@dataclass
class SitemapOutcome:
    """Per-sitemap record of what happened during a load."""

    url: str
    depth: int
    status: SitemapStatus
    pages_contributed: int = 0


@dataclass
class SitemapLoadResult:
    """Verifiable result of loading one domain's sitemaps.

    ``pages`` holds the final (possibly capped) http/https page URLs, while
    ``outcomes`` records every sitemap that was encountered, including the
    ones that were skipped and why. ``total_pages`` counts the raw pages
    contributed before the cap was applied, so truncation never hides how
    many pages the sitemaps actually contained.
    """

    pages: list[str] = field(default_factory=list)
    outcomes: list[SitemapOutcome] = field(default_factory=list)
    total_pages: int = 0
    truncated: bool = False
    truncation_limit: int = SITEMAP_MAX_URLS

    def outcome_for(self, url: str) -> SitemapOutcome | None:
        """Return the first recorded outcome for ``url``, if any."""
        for outcome in self.outcomes:
            if outcome.url == url:
                return outcome
        return None


class SitemapLoader:
    """Fetches and parses sitemaps for a domain. Async."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def load(
        self,
        domain: str,
        sitemap_urls: list[str],
        *,
        max_urls: int = SITEMAP_MAX_URLS,
    ) -> SitemapLoadResult:
        """Load all page URLs found across the given sitemap URLs for a domain.

        Falls back to /sitemap.xml if no sitemap URLs are provided. Returns a
        :class:`SitemapLoadResult` carrying both the page list and a per-sitemap
        outcome trail. Each sitemap URL is fetched at most once per load,
        recursion is bounded by the maximum depth, and only http/https page
        URLs are kept. When more than ``max_urls`` pages are found, the page
        list keeps only the first ``max_urls`` and ``truncated`` is set; the
        raw per-sitemap contribution counts are left untouched.
        """
        if not sitemap_urls:
            sitemap_urls = [f"https://{domain}/sitemap.xml"]

        result = SitemapLoadResult(truncation_limit=max_urls)
        seen_sitemaps: set[str] = set()
        for sitemap_url in sitemap_urls:
            await self._fetch(sitemap_url, result, seen_sitemaps, depth=0)

        result.truncated = result.total_pages > max_urls
        if result.truncated:
            result.pages = result.pages[:max_urls]
        return result

    async def _fetch(
        self,
        sitemap_url: str,
        result: SitemapLoadResult,
        seen_sitemaps: set[str],
        depth: int,
    ) -> None:
        if depth > _MAX_DEPTH:
            result.outcomes.append(
                SitemapOutcome(sitemap_url, depth, SitemapStatus.DEPTH_EXCEEDED)
            )
            return
        if sitemap_url in seen_sitemaps:
            result.outcomes.append(
                SitemapOutcome(sitemap_url, depth, SitemapStatus.DUPLICATE_SKIPPED)
            )
            return

        parsed = urlparse(sitemap_url)
        if parsed.scheme not in ("http", "https"):
            # Non-HTTP sitemap addresses can never be fetched; record and skip.
            result.outcomes.append(
                SitemapOutcome(sitemap_url, depth, SitemapStatus.NON_HTTP_FILTERED)
            )
            return

        seen_sitemaps.add(sitemap_url)

        try:
            resp = await self._client.get(
                sitemap_url, headers=_HEADERS, follow_redirects=True, timeout=30
            )
        except Exception:
            result.outcomes.append(
                SitemapOutcome(sitemap_url, depth, SitemapStatus.NETWORK_ERROR)
            )
            return

        if resp.status_code != 200:
            result.outcomes.append(
                SitemapOutcome(
                    sitemap_url,
                    depth,
                    SitemapStatus.NON_200,
                )
            )
            return

        try:
            root = ET.fromstring(resp.text)
        except Exception:
            result.outcomes.append(
                SitemapOutcome(sitemap_url, depth, SitemapStatus.PARSE_ERROR)
            )
            return

        tag = root.tag.removeprefix(f"{{{_NS_SITEMAP}}}")

        if tag == "sitemapindex":
            # Index itself contributes no pages; recurse into each child.
            result.outcomes.append(
                SitemapOutcome(sitemap_url, depth, SitemapStatus.SUCCESS)
            )
            for sitemap_el in root.findall(f"{{{_NS_SITEMAP}}}sitemap"):
                loc = sitemap_el.findtext(f"{{{_NS_SITEMAP}}}loc")
                if loc:
                    await self._fetch(
                        loc.strip(), result, seen_sitemaps, depth + 1
                    )
            return

        if tag == "urlset":
            contributed = 0
            for url_el in root.findall(f"{{{_NS_SITEMAP}}}url"):
                loc = url_el.findtext(f"{{{_NS_SITEMAP}}}loc")
                if loc:
                    page_url = loc.strip()
                    page_parsed = urlparse(page_url)
                    if page_parsed.scheme in ("http", "https"):
                        result.pages.append(page_url)
                        contributed += 1
            result.total_pages += contributed
            result.outcomes.append(
                SitemapOutcome(
                    sitemap_url,
                    depth,
                    SitemapStatus.SUCCESS,
                    pages_contributed=contributed,
                )
            )
            return

        # Parsed XML whose root is neither a sitemap index nor a urlset.
        result.outcomes.append(
            SitemapOutcome(sitemap_url, depth, SitemapStatus.PARSE_ERROR)
        )
