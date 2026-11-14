"""Acceptance tests for crawl_sitemap per-URL outcomes and retry-failed mode.

Driven by a fake crawl client that raises or returns empty content for
designated URLs, plus fake db/embedding/enhancer services — no network, no DB.
"""

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import crawler as crawler_module
from crawler import WebCrawler

SITEMAP_URL = "http://example.com/sitemap.xml"
URL_A = "http://example.com/a"
URL_B = "http://example.com/b"
URL_C = "http://example.com/c"
URL_EXTERNAL = "http://evil.external.com/x"

SITEMAP_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{URL_A}</loc></url>
  <url><loc>{URL_B}</loc></url>
  <url><loc>{URL_C}</loc></url>
  <url><loc>{URL_EXTERNAL}</loc></url>
</urlset>
""".encode("utf-8")


class _FakeResponse:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        pass


class _FakeCrawlClient:
    """behavior: url -> 'ok' | 'error' | 'empty'."""

    def __init__(self, behavior):
        self.behavior = behavior
        self.attempted = []

    def crawl_and_wait(self, url, **kwargs):
        self.attempted.append(url)
        mode = self.behavior.get(url, "ok")
        if mode == "error":
            raise RuntimeError(f"boom for {url}")
        if mode == "empty":
            return {"results": [{"url": url, "markdown": "", "metadata": {"title": "t"}}]}
        return {
            "results": [
                {
                    "url": url,
                    "markdown": f"Useful content for {url} page",
                    "metadata": {"title": f"Title {url}"},
                }
            ]
        }


class _FakeTokenizer:
    def encode(self, text):
        return text.split()

    def decode(self, tokens):
        return " ".join(tokens)


class _FakeEmbeddingGenerator:
    tokenizer = _FakeTokenizer()

    def count_tokens(self, text):
        return len(text.split())

    def generate_embedding(self, content):
        return [0.1] * 8


class _FakeContentEnhancer:
    model = "fake"

    async def generate_title_and_summary_async(self, content, url):
        return {"title": f"Title for {url}", "summary": "fake summary"}


class _FakeDB:
    def __init__(self):
        self.sites = {}
        self.pages = []
        self.jobs = {}
        self._next_site_id = 1

    def get_site_by_url(self, url):
        return self.sites.get(url)

    def add_site(self, name, url, description=None):
        site = {"id": self._next_site_id, "name": name, "url": url, "description": description}
        self._next_site_id += 1
        self.sites[url] = site
        return site["id"]

    def update_site_description(self, site_id, description):
        for site in self.sites.values():
            if site["id"] == site_id:
                site["description"] = description

    def add_pages(self, site_id, pages, replace_chunks=False):
        self.pages.extend(pages)
        return list(range(1, len(pages) + 1))

    def get_latest_crawl_job_by_site_id(self, site_id):
        return self.jobs.get(site_id)

    def stored_parent_urls(self):
        return [p["url"] for p in self.pages if not p.get("is_chunk")]


def _make_crawler(behavior):
    crawler = WebCrawler.__new__(WebCrawler)
    crawler.crawl_client = _FakeCrawlClient(behavior)
    crawler.embedding_generator = _FakeEmbeddingGenerator()
    crawler.db_client = _FakeDB()
    crawler.content_enhancer = _FakeContentEnhancer()
    return crawler


@pytest.fixture(autouse=True)
def _sitemap_env(monkeypatch):
    # Real validate_fetch_url / filter_safe_crawl_urls, but without DNS resolution.
    monkeypatch.setenv("ALLOW_PRIVATE_CRAWL_URLS", "1")
    monkeypatch.delenv("CRAWL_SITEMAP_ALLOW_EXTERNAL", raising=False)
    monkeypatch.delenv("CRAWL_ALLOWED_HOSTS", raising=False)
    monkeypatch.setattr(
        crawler_module,
        "fetch_validated_url",
        lambda *args, **kwargs: _FakeResponse(SITEMAP_XML),
    )


def _assert_counts_consistent(report):
    assert report["failed_count"] == len(report["failed_urls"])
    assert report["skipped_unsafe_count"] == len(report["skipped_unsafe_urls"])
    assert report["stored_count"] + report["failed_count"] == report["total_urls"]
    assert len(report["url_outcomes"]) == report["total_urls"]


def test_all_urls_succeed():
    crawler = _make_crawler({})
    site_id = crawler.crawl_sitemap(SITEMAP_URL, max_urls=50, raise_on_error=True)

    assert site_id == 1
    report = crawler.last_sitemap_report
    _assert_counts_consistent(report)
    assert report["stored_count"] == 3
    assert report["failed_count"] == 0
    # The external URL was blocked by the safety policy and never fetched.
    assert report["skipped_unsafe_count"] == 1
    assert report["skipped_unsafe_urls"][0]["url"] == URL_EXTERNAL
    assert report["skipped_unsafe_urls"][0]["reason"]
    assert URL_EXTERNAL not in crawler.crawl_client.attempted
    assert sorted(crawler.crawl_client.attempted) == sorted([URL_A, URL_B, URL_C])
    assert sorted(crawler.db_client.stored_parent_urls()) == sorted([URL_A, URL_B, URL_C])


def test_partial_failure_still_completes():
    crawler = _make_crawler({URL_B: "error", URL_C: "empty"})
    # At least one success: no raise even with raise_on_error=True.
    site_id = crawler.crawl_sitemap(SITEMAP_URL, max_urls=50, raise_on_error=True)

    assert site_id == 1
    report = crawler.last_sitemap_report
    _assert_counts_consistent(report)
    assert report["stored_count"] == 1
    assert report["failed_count"] == 2

    failures = {e["url"]: e for e in report["failed_urls"]}
    assert set(failures) == {URL_B, URL_C}
    assert failures[URL_B]["status"] == "crawl_error"
    assert "boom" in failures[URL_B]["reason"]
    assert failures[URL_C]["status"] == "empty_content"
    assert failures[URL_C]["reason"]

    # Only the successful URL was stored.
    assert crawler.db_client.stored_parent_urls() == [URL_A]


def test_all_urls_fail_marks_batch_failed():
    behavior = {URL_A: "error", URL_B: "error", URL_C: "empty"}
    crawler = _make_crawler(behavior)

    with pytest.raises(RuntimeError, match="No pages were successfully crawled"):
        crawler.crawl_sitemap(SITEMAP_URL, max_urls=50, raise_on_error=True)

    report = crawler.last_sitemap_report
    _assert_counts_consistent(report)
    assert report["stored_count"] == 0
    assert report["failed_count"] == 3
    assert all(e["reason"] for e in report["failed_urls"])
    assert crawler.db_client.stored_parent_urls() == []

    # Without raise_on_error the site id is still returned, report intact.
    crawler2 = _make_crawler(behavior)
    site_id = crawler2.crawl_sitemap(SITEMAP_URL, max_urls=50, raise_on_error=False)
    assert site_id == 1
    assert crawler2.last_sitemap_report["failed_count"] == 3


def test_retry_failed_only_recrawls_only_failures():
    behavior = {URL_B: "error"}
    crawler = _make_crawler(behavior)
    crawler.crawl_sitemap(SITEMAP_URL, max_urls=50, raise_on_error=True)
    assert crawler.last_sitemap_report["stored_count"] == 2
    assert crawler.last_sitemap_report["failed_count"] == 1

    # Fix the failing URL, then re-run in retry mode.
    behavior[URL_B] = "ok"
    crawler.crawl_client.attempted.clear()
    crawler.crawl_sitemap(SITEMAP_URL, max_urls=50, raise_on_error=True, retry_failed_only=True)

    # Only the previously failed URL was re-crawled.
    assert crawler.crawl_client.attempted == [URL_B]

    report = crawler.last_sitemap_report
    _assert_counts_consistent(report)
    assert report["stored_count"] == 3
    assert report["failed_count"] == 0
    reused = [e for e in report["url_outcomes"] if e.get("reused")]
    assert sorted(e["url"] for e in reused) == sorted([URL_A, URL_C])

    # Previously stored pages were not stored again.
    assert sorted(crawler.db_client.stored_parent_urls()) == sorted([URL_A, URL_B, URL_C])


def test_retry_failed_only_reads_report_from_job_record():
    behavior = {URL_B: "error"}
    crawler = _make_crawler(behavior)
    site_id = crawler.crawl_sitemap(SITEMAP_URL, max_urls=50, raise_on_error=True)
    first_report = crawler.last_sitemap_report

    # Simulate the API layer persisting the report on the crawl job record,
    # then a fresh crawler (new process) running in retry mode.
    db = crawler.db_client
    db.jobs[site_id] = {"options": {"sitemap_report": first_report}}

    behavior[URL_B] = "ok"
    crawler2 = _make_crawler(behavior)
    crawler2.db_client = db
    crawler2.crawl_sitemap(SITEMAP_URL, max_urls=50, raise_on_error=True, retry_failed_only=True)

    assert crawler2.crawl_client.attempted == [URL_B]
    report = crawler2.last_sitemap_report
    _assert_counts_consistent(report)
    assert report["stored_count"] == 3
    assert report["failed_count"] == 0
    assert sorted(db.stored_parent_urls()) == sorted([URL_A, URL_B, URL_C])
