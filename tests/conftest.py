"""Shared fixtures for crawler statistics tests.

Every fixture keeps the crawl fully offline: fetching, LLM extraction,
robots.txt and sitemap.xml are all replaced by in-memory fakes so the tests
exercise the crawler's own bookkeeping rather than the network.
"""
from types import SimpleNamespace
from urllib import robotparser

import pytest

from runo.core import crawler
from runo.models.request import CrawlConfig, ExtractOptions, SchemaField

BASE = "https://example.test"
PATTERN = BASE + "/p/*"


@pytest.fixture
def schema_fields() -> list[SchemaField]:
    return [SchemaField(field="title", type="string")]


@pytest.fixture
def options() -> ExtractOptions:
    # no_cache keeps the async path off result_cache (no Redis in tests) and
    # makes every URL take the fetch branch deterministically.
    return ExtractOptions(no_cache=True, async_mode=False)


@pytest.fixture
def make_config():
    def _make(**overrides) -> CrawlConfig:
        params = dict(
            follow_pattern=PATTERN,
            max_pages=50,
            max_depth=2,
            use_sitemap=False,
            ignore_robots=True,
        )
        params.update(overrides)
        return CrawlConfig(**params)
    return _make


@pytest.fixture
def offline(monkeypatch):
    """Patch the crawler's network/LLM touchpoints with controllable fakes.

    Returns a mutable SimpleNamespace holding:
      - ``html_by_url``: url -> HTML served by the fake fetcher
      - ``robots_rules``: list of "Allow:/..."/"Disallow:/..."/"" lines
      - ``sitemap_urls``: urls a successful sitemap fetch should expose
      - ``sitemap_fails``: when True, the sitemap request raises
    """
    state = SimpleNamespace(
        html_by_url={},
        robots_rules=None,       # None -> fail-open (no robots.txt fetched)
        sitemap_urls=[],
        sitemap_fails=False,
    )

    async def fake_pace(self, url):
        return None

    async def fake_fetch_robots(url):
        if state.robots_rules is None:
            return None
        rp = robotparser.RobotFileParser()
        rp.parse(state.robots_rules)
        return rp

    class _FakeResponse:
        def __init__(self, text, status_code=200):
            self.text = text
            self.status_code = status_code

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            if url.endswith("/sitemap.xml"):
                if state.sitemap_fails:
                    raise RuntimeError("sitemap boom")
                body = (
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                    + "".join(f"<url><loc>{u}</loc></url>" for u in state.sitemap_urls)
                    + "</urlset>"
                )
                return _FakeResponse(body)
            raise AssertionError(f"unexpected request in offline test: {url}")

    async def fake_fetch_url(**kwargs):
        url = kwargs["url"]
        html = state.html_by_url.get(url, f"<html><body>{url}</body></html>")
        return SimpleNamespace(html=html, status_code=200, render_mode="plain")

    monkeypatch.setattr(crawler.HostPolicy, "pace", fake_pace)
    monkeypatch.setattr(crawler, "_fetch_robots", fake_fetch_robots)
    monkeypatch.setattr(crawler.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(crawler.fetcher, "fetch_url", fake_fetch_url)
    return state
