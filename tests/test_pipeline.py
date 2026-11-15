import http.server
import json
import socket
import threading

import requests
from conftest import FakeEmbedder

from search_engine.config import Settings
from search_engine.indexer import Indexer
from search_engine.models import (
    OUTCOME_EXTRACTION_FAILED,
    OUTCOME_RATE_LIMITED,
    OUTCOME_REJECTED_NON_PUBLIC,
    OUTCOME_SNIPPET_FALLBACK,
    OUTCOME_SUCCESS,
    OUTCOME_SUCCESS_AFTER_REDIRECT,
    ExtractedContent,
    FetchedPage,
    PageOutcome,
    SearchResult,
)
from search_engine.pipeline import SearchPipeline
from search_engine.scraper import HttpFetcher, URLPolicy
from search_engine.vector_store import NumpyVectorStore


class FakeProvider:
    def __init__(self, results):
        self._results = results

    def search(self, query: str, limit: int):
        return list(self._results)[:limit]


class FakeFetcher:
    def fetch(self, url: str) -> FetchedPage:
        if url.endswith("/fail"):
            raise ValueError("simulated failure")
        return FetchedPage(url, "<p>alpha source text</p>", "text/html", 200)


class FakeExtractor:
    def extract(self, html: str, *, url: str | None = None) -> ExtractedContent:
        del html
        return ExtractedContent("alpha source text", title="Good", canonical_url=url)


def _make_indexer() -> Indexer:
    embedder = FakeEmbedder()
    return Indexer(
        embedder=embedder,
        vector_store=NumpyVectorStore(embedder.dimension),
        chunk_size_tokens=8,
        chunk_overlap_tokens=0,
    )


def _make_settings(**overrides) -> Settings:
    defaults = dict(
        search_results=2,
        top_k=2,
        fetch_k=3,
        chunk_size_tokens=8,
        chunk_overlap_tokens=0,
        max_context_tokens=10,
        score_threshold=-1,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _make_pipeline(results, *, fetcher, extractor=None, settings=None) -> SearchPipeline:
    return SearchPipeline(
        settings or _make_settings(),
        search_provider=FakeProvider(results),
        fetcher=fetcher,  # type: ignore[arg-type]
        extractor=extractor or FakeExtractor(),
        indexer=_make_indexer(),
    )


def test_pipeline_is_injectable_and_reports_fallbacks() -> None:
    results = [
        SearchResult("Good", "https://example.com/good", "alpha summary"),
        SearchResult("Fallback", "https://example.com/fail", "alpha fallback"),
    ]
    pipeline = _make_pipeline(results, fetcher=FakeFetcher())

    response = pipeline.run("alpha")

    assert response.searched_results == 2
    assert response.indexed_documents == 2
    assert response.indexed_chunks == 2
    assert len(response.failures) == 1
    assert any(hit.metadata.get("snippet_only") for hit in response.hits)
    assert response.context_tokens <= 10


def test_redirect_to_private_address_is_rejected_with_single_request() -> None:
    """A redirect hop to a non-public target must be validated before requesting."""
    requested_urls: list[str] = []
    session = requests.Session()

    def get(url: str, **kwargs):
        requested_urls.append(url)
        result = requests.Response()
        result.status_code = 302
        result.headers["Location"] = "http://internal.test/admin"
        result._content = b""
        result._content_consumed = True
        result.url = url
        return result

    session.get = get  # type: ignore[method-assign]

    def resolver(host: str, port: int, *, type: int):
        address = "93.184.216.34" if host == "example.com" else "10.0.0.5"
        return [(socket.AF_INET, type, 6, "", (address, port))]

    fetcher = HttpFetcher(session=session, url_policy=URLPolicy(resolver=resolver))
    results = [SearchResult("Private redirect", "https://example.com/start", "snippet")]
    pipeline = _make_pipeline(
        results,
        fetcher=fetcher,
        settings=_make_settings(use_search_snippets_on_failure=False),
    )

    response = pipeline.run("alpha")

    # Only the public origin was ever contacted; the private hop was rejected.
    assert requested_urls == ["https://example.com/start"]
    assert len(response.failures) == 1
    assert isinstance(response.failures[0].reason, str) and response.failures[0].reason
    [outcome] = response.page_outcomes
    assert outcome.category == OUTCOME_REJECTED_NON_PUBLIC
    assert outcome.original_url == "https://example.com/start"
    assert outcome.final_url is None
    assert "non-public" in outcome.reason
    assert response.indexed_documents == 0


def test_rate_limited_retries_exhausted_records_rate_limit_outcome() -> None:
    class RateLimitHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            server.requests_seen.append(self.path)  # type: ignore[attr-defined]
            self.send_response(429)
            self.send_header("Retry-After", "0")
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RateLimitHandler)
    server.requests_seen = []  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    target = f"http://127.0.0.1:{server.server_address[1]}/limited"

    fetcher = HttpFetcher(
        retries=1,
        connect_timeout=2,
        read_timeout=2,
        allow_private_network=True,
    )
    results = [SearchResult("Throttled", target, "ignored snippet")]
    pipeline = _make_pipeline(
        results,
        fetcher=fetcher,
        settings=_make_settings(use_search_snippets_on_failure=False),
    )

    try:
        response = pipeline.run("alpha")
    finally:
        server.shutdown()

    # Initial attempt plus the single configured retry; the budget is exhausted.
    assert len(server.requests_seen) == 2
    assert len(response.failures) == 1
    [outcome] = response.page_outcomes
    assert outcome.category == OUTCOME_RATE_LIMITED
    assert outcome.original_url == target
    assert outcome.final_url is None
    assert outcome.reason
    assert response.indexed_documents == 0


def test_extraction_failure_falls_back_to_snippet_with_both_outcomes() -> None:
    class ExtractionFailureFetcher:
        def fetch(self, url: str) -> FetchedPage:
            return FetchedPage(url, "<html></html>", "text/html", 200)

    class FailingExtractor:
        def extract(self, html: str, *, url: str | None = None) -> ExtractedContent:
            raise ValueError("extracted document was empty")

    results = [SearchResult("Empty page", "https://example.com/empty", "usable snippet")]
    pipeline = _make_pipeline(
        results,
        fetcher=ExtractionFailureFetcher(),  # type: ignore[arg-type]
        extractor=FailingExtractor(),  # type: ignore[arg-type]
        settings=_make_settings(use_search_snippets_on_failure=True),
    )

    response = pipeline.run("alpha")

    categories = [outcome.category for outcome in response.page_outcomes]
    assert categories == [OUTCOME_EXTRACTION_FAILED, OUTCOME_SNIPPET_FALLBACK]
    failure, fallback = response.page_outcomes
    assert failure.original_url == "https://example.com/empty"
    assert failure.final_url == "https://example.com/empty"
    assert fallback.reason
    # The legacy failure list still reports the page problem.
    assert len(response.failures) == 1
    # The snippet still enters the index and can be retrieved.
    assert response.indexed_documents == 1
    assert any(hit.metadata.get("snippet_only") for hit in response.hits)
    assert response.context_tokens <= 10


def test_all_success_outcomes_match_hit_count_and_token_budget() -> None:
    class RedirectAwareFetcher:
        def fetch(self, url: str) -> FetchedPage:
            if url.endswith("/moved"):
                return FetchedPage(
                    "https://example.com/destination",
                    "<p>beta moved destination text</p>",
                    "text/html",
                    200,
                    redirected=True,
                )
            return FetchedPage(url, "<p>alpha direct source text</p>", "text/html", 200)

    class EchoExtractor:
        def extract(self, html: str, *, url: str | None = None) -> ExtractedContent:
            return ExtractedContent(html, title="Page", canonical_url=url)

    results = [
        SearchResult("Direct", "https://example.com/direct", "alpha summary"),
        SearchResult("Moved", "https://example.com/moved", "beta summary"),
    ]
    pipeline = _make_pipeline(
        results,
        fetcher=RedirectAwareFetcher(),  # type: ignore[arg-type]
        extractor=EchoExtractor(),  # type: ignore[arg-type]
    )

    response = pipeline.run("alpha")

    assert response.searched_results == 2
    assert response.failures == []
    # One auditable outcome per search result, each a successful category.
    assert len(response.page_outcomes) == 2
    assert [outcome.category for outcome in response.page_outcomes] == [
        OUTCOME_SUCCESS,
        OUTCOME_SUCCESS_AFTER_REDIRECT,
    ]
    direct, moved = response.page_outcomes
    assert direct.final_url is None
    assert moved.final_url == "https://example.com/destination"
    # Every result produced a chunk and every chunk is retrieved.
    assert response.indexed_documents == 2
    assert len(response.hits) == response.searched_results == len(response.page_outcomes)
    assert response.context_tokens <= 10


def test_search_response_remains_json_serializable_and_keeps_legacy_fields() -> None:
    response = _make_pipeline(
        [SearchResult("Good", "https://example.com/good", "alpha summary")],
        fetcher=FakeFetcher(),
    ).run("alpha")

    encoded = json.dumps(response.to_dict())
    payload = json.loads(encoded)

    assert "failures" in payload and isinstance(payload["failures"], list)
    assert payload["page_outcomes"] == [
        {
            "original_url": "https://example.com/good",
            "category": OUTCOME_SUCCESS,
            "reason": response.page_outcomes[0].reason,
            "final_url": None,
        }
    ]
    # Constructing a response without the new field still works (old callers).
    legacy = type(response)(
        query="q",
        hits=[],
        searched_results=0,
        indexed_documents=0,
        indexed_chunks=0,
        context_tokens=0,
    )
    assert legacy.page_outcomes == []
    assert json.loads(json.dumps(legacy.to_dict()))["page_outcomes"] == []


def test_page_outcome_uses_known_categories() -> None:
    from search_engine.models import PAGE_OUTCOME_CATEGORIES

    outcome = PageOutcome(
        original_url="https://example.com",
        category=OUTCOME_RATE_LIMITED,
        reason="exhausted",
    )
    assert outcome.category in PAGE_OUTCOME_CATEGORIES
