"""Site-wide article URL discovery via sitemaps and RSS/Atom."""

from __future__ import annotations

import re
from urllib.parse import urljoin

import requests
from lxml import etree

_SITEMAP_FALLBACKS = ("/sitemap.xml", "/sitemap_news.xml", "/news-sitemap.xml", "/sitemap-news.xml")
_MAX_FEEDS = 8
_MAX_SITEMAP_DEPTH = 1
DEFAULT_MAX_REQUESTS = 32
_TIMEOUT = 10
_FEED_TYPE_RE = re.compile(r"rss|atom|xml", re.I)


class DiscoveredArticle:
    __slots__ = ("url", "title", "date")

    def __init__(self, url: str, title: str | None = None, date: str | None = None) -> None:
        self.url = url
        self.title = title
        self.date = date

    def to_dict(self) -> dict:
        return {"url": self.url, "title": self.title, "date": self.date}


class DiscoveryResult(list):
    """A ``list`` of discovered entries carrying truncation metadata.

    Subclasses :class:`list`, so callers that iterate, index, slice, ``len()``,
    or JSON-serialize the result keep working unchanged. The extra attributes
    explain whether discovery stopped early:

    ``truncated``
        ``True`` when some discovered entries or candidate sources were not
        returned/expanded.
    ``budget_exhausted``
        ``True`` when the request budget stopped further feed/sitemap fetches.
    ``limit_truncated``
        ``True`` when ``limit`` dropped already-discovered entries.
    ``remaining_candidates`` / ``unexpanded_candidates``
        Number/list of candidate feed or sitemap URLs that were never fetched
        (request budget, or the per-run candidate cap).
    ``remaining_entries``
        Number of discovered entries not returned because of ``limit``.
    """

    def __init__(
        self,
        items=(),
        *,
        truncated: bool = False,
        budget_exhausted: bool = False,
        limit_truncated: bool = False,
        remaining_candidates: int = 0,
        remaining_entries: int = 0,
        unexpanded_candidates: list[str] | None = None,
        request_count: int = 0,
        max_requests: int | None = None,
    ) -> None:
        super().__init__(items)
        self.truncated = truncated
        self.budget_exhausted = budget_exhausted
        self.limit_truncated = limit_truncated
        self.remaining_candidates = remaining_candidates
        self.remaining_entries = remaining_entries
        self.unexpanded_candidates = list(unexpanded_candidates or [])
        self.request_count = request_count
        self.max_requests = max_requests

    @property
    def has_more(self) -> bool:
        """True when unexpanded candidate sources or unreturned entries remain."""
        return self.remaining_candidates > 0 or self.remaining_entries > 0

    def meta(self) -> dict:
        """Truncation metadata as a plain ``dict``."""
        return {
            "truncated": self.truncated,
            "budget_exhausted": self.budget_exhausted,
            "limit_truncated": self.limit_truncated,
            "remaining_candidates": self.remaining_candidates,
            "remaining_entries": self.remaining_entries,
            "unexpanded_candidates": list(self.unexpanded_candidates),
            "request_count": self.request_count,
            "max_requests": self.max_requests,
        }


def discover(
    news_domain: str,
    *,
    limit: int | None = 50,
    timeout: float = _TIMEOUT,
    max_requests: int | None = DEFAULT_MAX_REQUESTS,
) -> DiscoveryResult:
    """Discover recent article URLs for a news site.

    Uses robots.txt Sitemap directives, homepage feed autodiscovery, and common
    sitemap paths — not a search engine.

    ``max_requests`` caps the number of HTTP requests (robots.txt, homepage,
    feed and sitemap documents) made during discovery. Once the budget is
    exhausted, discovery stops fetching and returns whatever was collected.
    Pass ``None`` for no budget.

    The returned value is a ``list[dict]`` (a :class:`DiscoveryResult` subclass)
    so existing callers are unaffected; its attributes report truncation.
    """
    extractor = SiteDiscoverer(
        news_domain, limit=limit, timeout=timeout, max_requests=max_requests
    )
    return extractor.as_dicts()


class SiteDiscoverer:
    """Discover article URLs from sitemaps and feeds.

    ``max_requests`` caps how many HTTP requests (robots.txt, homepage, feed and
    sitemap documents) discovery may issue. When the budget runs out no further
    requests are made and whatever was collected is returned. ``limit`` still
    truncates the final entry list. Truncation state is exposed on the instance
    (``truncated``, ``budget_exhausted``, ``limit_truncated``,
    ``remaining_candidates``, ``remaining_entries``, ``unexpanded_candidates``).
    """

    def __init__(
        self,
        news_domain: str,
        *,
        limit: int | None = 50,
        timeout: float = _TIMEOUT,
        max_requests: int | None = DEFAULT_MAX_REQUESTS,
    ) -> None:
        self.news_domain = news_domain.rstrip("/")
        self.timeout = timeout
        self.max_requests = max_requests
        self._request_count = 0
        self._budget_exhausted = False
        self._capped_candidates: list[str] = []
        self._nested_unexpanded: list[str] = []

        # Truncation metadata (populated by _discover)
        self.truncated = False
        self.budget_exhausted = False
        self.limit_truncated = False
        self.remaining_candidates = 0
        self.remaining_entries = 0
        self.unexpanded_candidates: list[str] = []
        self.request_count = 0

        self.articles: list[DiscoveredArticle] = self._discover(limit)
        self.urls: list[str] = [a.url for a in self.articles]

    def _budget_available(self) -> bool:
        return self.max_requests is None or self._request_count < self.max_requests

    def _get(self, url: str) -> requests.Response | None:
        if not self._budget_available():
            self._budget_exhausted = True
            return None
        self._request_count += 1
        try:
            resp = requests.get(url, timeout=self.timeout)
            resp.raise_for_status()
            return resp
        except Exception:
            return None

    def _discover(self, limit: int | None) -> list[DiscoveredArticle]:
        found: dict[str, DiscoveredArticle] = {}
        candidates = self._candidate_feed_urls()
        unexpanded: list[str] = []

        for index, feed_url in enumerate(candidates):
            if not self._budget_available():
                self._budget_exhausted = True
                unexpanded = candidates[index:]
                break
            # A single failing source (network error, unparsable body, nested
            # sitemap failure) yields no entries but never aborts discovery.
            for article in self._parse_feed(feed_url, depth=0):
                found.setdefault(article.url, article)
            if self._budget_exhausted:
                # Budget was consumed by this feed or one of its nested sitemaps.
                unexpanded = candidates[index + 1 :]
                break

        # Candidates dropped by the per-run candidate cap (see _MAX_FEEDS).
        for extra in self._capped_candidates + self._nested_unexpanded:
            if extra not in unexpanded:
                unexpanded.append(extra)

        results = list(found.values())
        returned = results if limit is None else results[:limit]

        self.remaining_entries = max(0, len(results) - len(returned))
        self.limit_truncated = self.remaining_entries > 0
        self.unexpanded_candidates = unexpanded
        self.remaining_candidates = len(unexpanded)
        self.budget_exhausted = self._budget_exhausted
        self.request_count = self._request_count
        self.truncated = (
            self.budget_exhausted or self.limit_truncated or self.remaining_candidates > 0
        )
        return returned

    def as_dicts(self) -> DiscoveryResult:
        """Return entries as a ``list[dict]`` that also carries truncation metadata."""
        return DiscoveryResult(
            [a.to_dict() if hasattr(a, "to_dict") else dict(a) for a in self.articles],
            truncated=self.truncated,
            budget_exhausted=self.budget_exhausted,
            limit_truncated=self.limit_truncated,
            remaining_candidates=self.remaining_candidates,
            remaining_entries=self.remaining_entries,
            unexpanded_candidates=self.unexpanded_candidates,
            request_count=self.request_count,
            max_requests=self.max_requests,
        )

    def _candidate_feed_urls(self) -> list[str]:
        candidates: list[str] = []

        robots = self._get(f"{self.news_domain}/robots.txt")
        if robots is not None:
            for line in robots.text.splitlines():
                if line.lower().startswith("sitemap:"):
                    candidates.append(line.split(":", 1)[1].strip())

        homepage = self._get(self.news_domain)
        if homepage is not None:
            try:
                root = etree.HTML(homepage.content)
            except Exception:
                root = None
            if root is not None:
                for link in root.xpath("//link[@href]"):
                    type_ = (link.get("type") or "") + " " + (link.get("rel") or "")
                    if _FEED_TYPE_RE.search(type_):
                        href = link.get("href")
                        if href:
                            candidates.append(urljoin(self.news_domain, href))

        if not candidates:
            candidates = [self.news_domain + path for path in _SITEMAP_FALLBACKS]

        seen: set[str] = set()
        deduped: list[str] = []
        for url in candidates:
            if url not in seen:
                seen.add(url)
                deduped.append(url)
        deduped.sort(key=lambda u: "news" not in u.lower())
        # Candidates beyond the per-run cap are recorded as unexpanded so
        # callers can see that further sources exist.
        self._capped_candidates = deduped[_MAX_FEEDS:]
        return deduped[:_MAX_FEEDS]

    def _parse_feed(self, feed_url: str, depth: int) -> list[DiscoveredArticle]:
        if depth > _MAX_SITEMAP_DEPTH:
            # Nested sitemap beyond the depth cap: never fetched, reported as
            # an unexpanded candidate source.
            if feed_url not in self._nested_unexpanded:
                self._nested_unexpanded.append(feed_url)
            return []
        response = self._get(feed_url)
        if response is None:
            return []
        try:
            root = etree.fromstring(response.content)
        except Exception:
            try:
                root = etree.HTML(response.content)
            except Exception:
                return []

        # Strip namespaces for simpler queries
        for elem in root.iter():
            if isinstance(elem.tag, str) and "}" in elem.tag:
                elem.tag = elem.tag.split("}", 1)[1]

        tag = root.tag.lower() if isinstance(root.tag, str) else ""
        if root.find(".//sitemapindex") is not None or tag == "sitemapindex":
            articles: list[DiscoveredArticle] = []
            locs = root.findall(".//loc")
            for index, loc in enumerate(locs):
                text = (loc.text or "").strip()
                if not text or "news" not in text.lower():
                    continue
                if not self._budget_available():
                    self._budget_exhausted = True
                    # Record nested sitemaps that will not be expanded.
                    self._nested_unexpanded.extend(
                        (rest.text or "").strip()
                        for rest in locs[index:]
                        if (rest.text or "").strip()
                        and "news" in (rest.text or "").lower()
                    )
                    break
                articles.extend(self._parse_feed(text, depth=depth + 1))
            return articles

        if root.find(".//urlset") is not None or tag == "urlset":
            results = []
            for url_tag in root.findall(".//url"):
                entry = self._entry_from_url(url_tag)
                if entry:
                    results.append(entry)
            return results

        if (
            root.find(".//rss") is not None
            or tag == "rss"
            or root.find(".//feed") is not None
            or tag == "feed"
        ):
            results = []
            for item in root.findall(".//item") + root.findall(".//entry"):
                entry = self._entry_from_item(item)
                if entry:
                    results.append(entry)
            return results

        return []

    @staticmethod
    def _entry_from_url(tag) -> DiscoveredArticle | None:
        loc = tag.find("loc")
        url = (loc.text or "").strip() if loc is not None else ""
        if not url:
            return None
        title_tag = tag.find(".//title")
        date = None
        for key in ("publication_date", "lastmod"):
            node = tag.find(f".//{key}")
            if node is not None and (node.text or "").strip():
                date = node.text.strip()
                break
        title = title_tag.text.strip() if title_tag is not None and title_tag.text else None
        return DiscoveredArticle(url=url, title=title, date=date)

    @staticmethod
    def _entry_from_item(tag) -> DiscoveredArticle | None:
        link = tag.find("link")
        url = None
        if link is not None:
            url = (link.get("href") or (link.text or "")).strip() or None
        if not url:
            return None
        title_tag = tag.find("title")
        date = None
        for key in ("pubDate", "published", "updated"):
            node = tag.find(key)
            if node is not None and (node.text or "").strip():
                date = node.text.strip()
                break
        title = title_tag.text.strip() if title_tag is not None and title_tag.text else None
        return DiscoveredArticle(url=url, title=title, date=date)


# Back-compat alias used by older news-fetch code
class NewsSiteURLExtractor(SiteDiscoverer):
    def __init__(
        self,
        news_domain: str,
        limit: int | None = 50,
        *,
        max_requests: int | None = DEFAULT_MAX_REQUESTS,
    ) -> None:
        super().__init__(news_domain, limit=limit, max_requests=max_requests)
        # Old API exposed list[dict]
        self.articles = [a.to_dict() for a in self.articles]  # type: ignore[assignment]
