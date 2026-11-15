"""Offline tests for site discovery request budget / truncation metadata."""

from __future__ import annotations

import requests

from newsfetch.discover import DiscoveryResult, NewsSiteURLExtractor, SiteDiscoverer, discover


class FakeResponse:
    """Minimal stand-in for ``requests.Response``."""

    def __init__(self, body: str, status: int = 200) -> None:
        self.text = body
        self.content = body.encode("utf-8")
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _install_responses(monkeypatch, routes: dict[str, str | int]) -> list[str]:
    """Serve canned responses by URL and record every request made."""
    calls: list[str] = []

    def fake_get(url, timeout=None, **kwargs):
        calls.append(url)
        if url not in routes:
            # Simulate a transport-level failure for unknown URLs.
            raise RuntimeError("connection refused")
        body = routes[url]
        if isinstance(body, int):
            return FakeResponse("", status=body)
        return FakeResponse(body)

    monkeypatch.setattr(requests, "get", fake_get)
    return calls


def _sitemap_index(*locations: str) -> str:
    entries = "".join(f"<sitemap><loc>{loc}</loc></sitemap>" for loc in locations)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{entries}</sitemapindex>"
    )


def _urlset(*paths: str) -> str:
    entries = "".join(
        f"<url><loc>https://example.com{n}</loc><lastmod>2025-11-01</lastmod></url>"
        for n in paths
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{entries}</urlset>"
    )


HOMEPAGE_HTML = "<html><head></head><body>no feeds here</body></html>"


def test_budget_exhaustion_stops_requests_and_reports_unexpanded(monkeypatch):
    index_url = "https://example.com/sitemap_index.xml"
    first_news = "https://example.com/news-sitemap-1.xml"
    second_news = "https://example.com/news-sitemap-2.xml"

    calls = _install_responses(
        monkeypatch,
        {
            "https://example.com/robots.txt": f"Sitemap: {index_url}",
            "https://example.com": HOMEPAGE_HTML,
            index_url: _sitemap_index(first_news, second_news),
            first_news: _urlset("/a/1", "/a/2"),
            second_news: _urlset("/a/3", "/a/4"),
        },
    )

    # robots.txt, homepage, sitemap index, first nested sitemap => budget spent.
    result = discover("https://example.com", limit=50, max_requests=4)

    assert isinstance(result, DiscoveryResult)
    assert isinstance(result, list)
    assert [item["url"] for item in result] == [
        "https://example.com/a/1",
        "https://example.com/a/2",
    ]
    assert result.budget_exhausted is True
    assert result.truncated is True
    assert result.limit_truncated is False
    assert result.remaining_candidates == 1
    assert result.unexpanded_candidates == [second_news]
    assert result.request_count == 4
    assert result.max_requests == 4
    # The second nested sitemap must never have been requested.
    assert calls == [
        "https://example.com/robots.txt",
        "https://example.com",
        index_url,
        first_news,
    ]
    assert second_news not in calls


def test_budget_exhaustion_before_any_feed_fetch(monkeypatch):
    index_url = "https://example.com/sitemap_index.xml"
    calls = _install_responses(
        monkeypatch,
        {
            "https://example.com/robots.txt": f"Sitemap: {index_url}",
            "https://example.com": HOMEPAGE_HTML,
            index_url: _urlset("/a/1"),
        },
    )

    result = discover("https://example.com", max_requests=2)

    assert result == []
    assert result.budget_exhausted is True
    assert result.truncated is True
    assert result.remaining_candidates == 1
    assert result.unexpanded_candidates == [index_url]
    assert result.request_count == 2
    assert calls == ["https://example.com/robots.txt", "https://example.com"]


def test_limit_truncation_is_reported(monkeypatch):
    sitemap_url = "https://example.com/sitemap.xml"
    calls = _install_responses(
        monkeypatch,
        {
            "https://example.com/robots.txt": f"Sitemap: {sitemap_url}",
            "https://example.com": HOMEPAGE_HTML,
            sitemap_url: _urlset("/a/1", "/a/2", "/a/3", "/a/4", "/a/5"),
        },
    )

    result = discover("https://example.com", limit=2)

    assert [item["url"] for item in result] == [
        "https://example.com/a/1",
        "https://example.com/a/2",
    ]
    assert result.limit_truncated is True
    assert result.truncated is True
    assert result.budget_exhausted is False
    assert result.remaining_entries == 3
    assert result.remaining_candidates == 0
    assert len(calls) == 3  # robots.txt, homepage, sitemap — nothing else


def test_single_source_failure_is_skipped(monkeypatch):
    broken = "https://example.com/broken-news.xml"
    working = "https://example.com/working-news.xml"
    calls = _install_responses(
        monkeypatch,
        {
            "https://example.com/robots.txt": f"Sitemap: {broken}\nSitemap: {working}",
            "https://example.com": HOMEPAGE_HTML,
            broken: 500,
            working: _urlset("/a/1", "/a/2"),
        },
    )

    result = discover("https://example.com")

    assert [item["url"] for item in result] == [
        "https://example.com/a/1",
        "https://example.com/a/2",
    ]
    # Discovery continued past the failing source and was not truncated.
    assert result.truncated is False
    assert result.budget_exhausted is False
    assert result.limit_truncated is False
    assert result.remaining_candidates == 0
    assert working in calls


def test_malformed_source_is_skipped(monkeypatch):
    garbage = "https://example.com/garbage-news.xml"
    working = "https://example.com/working-news.xml"
    _install_responses(
        monkeypatch,
        {
            "https://example.com/robots.txt": f"Sitemap: {garbage}\nSitemap: {working}",
            "https://example.com": HOMEPAGE_HTML,
            garbage: "not xml at all {{{",
            working: _urlset("/a/1"),
        },
    )

    result = discover("https://example.com")

    assert [item["url"] for item in result] == ["https://example.com/a/1"]
    assert result.truncated is False


def test_zero_budget_makes_no_requests(monkeypatch):
    calls = _install_responses(monkeypatch, {})

    result = discover("https://example.com", max_requests=0)

    assert result == []
    assert result.budget_exhausted is True
    assert result.truncated is True
    assert result.request_count == 0
    # Fallback sitemap paths are known candidates but were never fetched.
    assert result.remaining_candidates == 4
    assert all(url.startswith("https://example.com/") for url in result.unexpanded_candidates)
    assert calls == []


def test_no_budget_fetches_all_candidates(monkeypatch):
    paths = ("/sitemap.xml", "/sitemap_news.xml", "/news-sitemap.xml", "/sitemap-news.xml")
    routes = {f"https://example.com{p}": _urlset(f"/{i}") for i, p in enumerate(paths)}
    calls = _install_responses(
        monkeypatch,
        {
            "https://example.com/robots.txt": "",
            "https://example.com": HOMEPAGE_HTML,
            **routes,
        },
    )

    result = discover("https://example.com", max_requests=None)

    assert len(result) == 4
    assert result.truncated is False
    assert result.budget_exhausted is False
    assert result.remaining_candidates == 0
    assert result.max_requests is None
    assert len(calls) == 6  # robots.txt, homepage, four fallbacks


def test_cli_reports_truncation(monkeypatch, capsys):
    sitemap_url = "https://example.com/sitemap.xml"
    _install_responses(
        monkeypatch,
        {
            "https://example.com/robots.txt": f"Sitemap: {sitemap_url}",
            "https://example.com": HOMEPAGE_HTML,
            sitemap_url: _urlset("/a/1", "/a/2"),
        },
    )
    from newsfetch.cli import main

    code = main(["discover", "https://example.com", "--limit", "1"])
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.strip() == "https://example.com/a/1"
    assert "truncated" in captured.err


def test_backward_compatible_returns(monkeypatch):
    sitemap_url = "https://example.com/sitemap.xml"
    _install_responses(
        monkeypatch,
        {
            "https://example.com/robots.txt": f"Sitemap: {sitemap_url}",
            "https://example.com": HOMEPAGE_HTML,
            sitemap_url: _urlset("/a/1"),
        },
    )

    result = discover("https://example.com", limit=10)
    assert list(result) == [{"url": "https://example.com/a/1", "title": None, "date": "2025-11-01"}]
    assert result.meta()["truncated"] is False
    assert result.has_more is False

    extractor = SiteDiscoverer("https://example.com", limit=10)
    assert isinstance(extractor.articles, list)
    assert extractor.truncated is False

    legacy = NewsSiteURLExtractor("https://example.com", limit=10)
    assert isinstance(legacy.articles, list)
    assert isinstance(legacy.articles[0], dict)
    assert legacy.articles[0]["url"] == "https://example.com/a/1"
