"""Tests for the Wikipedia digest example's item outcomes.

The example normally talks to Wikipedia over real HTTP; here every response is
produced by in-process stubs (``FakeResponse``/``FakeTransport``), so the tests
are deterministic and never open a socket. They cover:

- a normal summary (success outcome, real words and a non-empty vector)
- HTTP 404 and an empty summary (the soft "no content" outcome)
- persistent HTTP 429 that exhausts the retry strategy ("retry exhausted")
- one mixed batch containing all of the above, proving a single failing item
  never interrupts the run and every source item is still written to JSONL
"""

from __future__ import annotations

__author__ = "Giacomo Berardi <giacbrd.com>"

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pytest
import requests

# The examples directory is not an installed package: make the example module
# importable the same way `python examples/news_crawler.py` does at run time.
_EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples"
if str(_EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(_EXAMPLES_DIR))

import news_crawler as crawler  # noqa: E402
from news_crawler import (  # noqa: E402
    OUTCOME_NO_CONTENT,
    OUTCOME_RETRY_EXHAUSTED,
    OUTCOME_SUCCESS,
    DigestWriter,
    HashEmbedding,
    PageFetcher,
    RandomWikipediaSource,
    TransientHTTPError,
    build_pipeline,
)

from tests.utils import run_with_timeout  # noqa: E402


class FakeResponse:
    """Minimal stand-in for the small subset of ``requests.Response`` used."""

    def __init__(self, status_code: int, payload: Optional[Dict[str, Any]] = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> Dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise TransientHTTPError(f"HTTP {self.status_code}")


class FakeTransport:
    """Route summary requests to canned responses, counting each call.

    ``routes`` maps a page title to either a :class:`FakeResponse` or a
    callable returning one (called on every attempt, so retry exhaustion can
    be observed through ``call_counts``).
    """

    def __init__(self, routes: Dict[str, Any]):
        self._routes = routes
        self.call_counts: Dict[str, int] = {title: 0 for title in routes}

    def __call__(self, url: str, timeout: float = 10.0) -> requests.Response:
        for title in self._routes:
            if url.endswith(title.replace(" ", "_")):
                self.call_counts[title] += 1
                route = self._routes[title]
                return route() if callable(route) else route
        raise AssertionError(f"unexpected request for {url}")


def _summary_response(text: str) -> FakeResponse:
    return FakeResponse(
        200,
        {
            "extract": text,
            "content_urls": {"desktop": {"page": "https://example.org/page"}},
        },
    )


def _make_args(workers: int = 1, queue_size: int = 100) -> argparse.Namespace:
    return argparse.Namespace(workers=workers, queue_size=queue_size)


def _build_test_pipeline(
    output: Path,
    titles: Sequence[str],
    transport: FakeTransport,
    *,
    max_retries: int = 2,
    workers: int = 1,
) -> crawler.Pipeline[crawler.ArticleData]:
    source = RandomWikipediaSource(pages=len(titles), titles=titles)
    fetcher = PageFetcher(transport=transport)
    writer = DigestWriter(output, size=8, timeout=0.0)
    return build_pipeline(
        _make_args(workers=workers),
        output,
        source=source,
        fetcher=fetcher,
        writer=writer,
        max_retries=max_retries,
        backoff=0.0,
    )


def _read_rows(output: Path) -> List[Dict[str, Any]]:
    with output.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _rows_by_title(rows: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {row["title"]: row for row in rows}


def _run(output: Path, titles: Sequence[str], transport: FakeTransport, **kwargs: Any):
    pipeline = _build_test_pipeline(output, titles, transport, **kwargs)
    items = run_with_timeout(pipeline)
    writer = pipeline.get_stage("digest")
    assert isinstance(writer, DigestWriter)
    return items, writer


def test_success_item_is_cleaned_and_embedded(tmp_path: Path) -> None:
    output = tmp_path / "digest.jsonl"
    titles = ["Ada Lovelace"]
    transport = FakeTransport(
        {"Ada Lovelace": _summary_response("Ada wrote the first algorithm [1] today")}
    )

    items, writer = _run(output, titles, transport)

    assert len(items) == 1
    rows = _read_rows(output)
    assert len(rows) == 1
    row = rows[0]
    assert row["outcome"] == OUTCOME_SUCCESS
    assert row["title"] == "Ada Lovelace"
    # the [1] reference is stripped by cleaning before counting words
    assert row["words"] == 6
    assert row["top_terms"]  # non-empty term statistics for real content
    assert ["algorithm", 1] in row["top_terms"]
    assert len(row["vector"]) == crawler.VECTOR_DIM
    assert any(value != 0.0 for value in row["vector"])
    assert row["errors"] == []
    assert writer.outcome_counts == {
        OUTCOME_SUCCESS: 1,
        OUTCOME_NO_CONTENT: 0,
        OUTCOME_RETRY_EXHAUSTED: 0,
    }


def test_http_404_is_a_no_content_outcome(tmp_path: Path) -> None:
    output = tmp_path / "digest.jsonl"
    titles = ["Ghost Page"]
    transport = FakeTransport({"Ghost Page": FakeResponse(404)})

    items, writer = _run(output, titles, transport)

    assert len(items) == 1
    row = _rows_by_title(_read_rows(output))["Ghost Page"]
    assert row["outcome"] == OUTCOME_NO_CONTENT
    assert row["words"] == 0
    assert row["top_terms"] == []
    assert row["vector"] == []
    assert any("404" in error or "no Wikipedia summary" in error for error in row["errors"])
    # 404 is a permanent answer: the retry strategy must not kick in.
    assert transport.call_counts["Ghost Page"] == 1
    assert writer.outcome_counts[OUTCOME_NO_CONTENT] == 1


def test_empty_summary_is_a_no_content_outcome(tmp_path: Path) -> None:
    output = tmp_path / "digest.jsonl"
    titles = ["Blank Page"]
    transport = FakeTransport({"Blank Page": _summary_response("   ")})

    items, writer = _run(output, titles, transport)

    assert len(items) == 1
    row = _rows_by_title(_read_rows(output))["Blank Page"]
    assert row["outcome"] == OUTCOME_NO_CONTENT
    assert row["words"] == 0
    assert row["top_terms"] == []
    assert row["vector"] == []
    assert any("empty extract" in error for error in row["errors"])
    assert writer.outcome_counts[OUTCOME_NO_CONTENT] == 1


def test_persistent_429_exhausts_retries_and_marks_failure(tmp_path: Path) -> None:
    output = tmp_path / "digest.jsonl"
    titles = ["Rate Limited"]
    transport = FakeTransport({"Rate Limited": FakeResponse(429)})

    items, writer = _run(output, titles, transport, max_retries=2)

    assert len(items) == 1
    # First attempt plus the two configured retries, then the item gives up.
    assert transport.call_counts["Rate Limited"] == 3
    row = _rows_by_title(_read_rows(output))["Rate Limited"]
    assert row["outcome"] == OUTCOME_RETRY_EXHAUSTED
    assert row["words"] == 0
    assert row["top_terms"] == []
    assert row["vector"] == []
    assert any("HTTP 429" in error for error in row["errors"])
    assert writer.outcome_counts[OUTCOME_RETRY_EXHAUSTED] == 1


def test_transient_failure_recovers_on_retry(tmp_path: Path) -> None:
    output = tmp_path / "digest.jsonl"
    titles = ["Flaky Page"]
    attempts = {"n": 0}

    def flaky() -> FakeResponse:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return FakeResponse(503)
        return _summary_response("The flaky service finally answered.")

    transport = FakeTransport({"Flaky Page": flaky})

    items, writer = _run(output, titles, transport, max_retries=3)

    assert attempts["n"] == 3
    row = _rows_by_title(_read_rows(output))["Flaky Page"]
    assert row["outcome"] == OUTCOME_SUCCESS
    assert row["words"] > 0
    assert len(row["vector"]) == crawler.VECTOR_DIM
    assert writer.outcome_counts[OUTCOME_SUCCESS] == 1


def test_mixed_batch_writes_every_item_with_the_right_outcome(tmp_path: Path) -> None:
    output = tmp_path / "digest.jsonl"
    titles = [
        "Good Page",
        "Missing Page",
        "Empty Page",
        "Limited Page",
        "Server Sad Page",
    ]
    transport = FakeTransport(
        {
            "Good Page": _summary_response("A perfectly normal Wikipedia summary."),
            "Missing Page": FakeResponse(404),
            "Empty Page": _summary_response(""),
            "Limited Page": FakeResponse(429),
            "Server Sad Page": FakeResponse(500),
        }
    )

    items, writer = _run(output, titles, transport, max_retries=1)

    # No item failure interrupts the run: the whole source batch is delivered.
    assert len(items) == len(titles)
    rows = _read_rows(output)
    assert len(rows) == len(titles)
    by_title = _rows_by_title(rows)

    good = by_title["Good Page"]
    assert good["outcome"] == OUTCOME_SUCCESS
    assert good["words"] > 0
    assert good["top_terms"]
    assert len(good["vector"]) == crawler.VECTOR_DIM

    for failed_title in ("Missing Page", "Empty Page"):
        row = by_title[failed_title]
        assert row["outcome"] == OUTCOME_NO_CONTENT
        assert row["words"] == 0
        assert row["top_terms"] == []
        assert row["vector"] == []

    for exhausted_title in ("Limited Page", "Server Sad Page"):
        row = by_title[exhausted_title]
        assert row["outcome"] == OUTCOME_RETRY_EXHAUSTED
        assert row["words"] == 0
        assert row["top_terms"] == []
        assert row["vector"] == []
        # max_retries=1 means the initial attempt plus one retry.
        assert transport.call_counts[exhausted_title] == 2

    # Every row keeps the original schema plus the explicit outcome field.
    for row in rows:
        assert set(
            ["title", "url", "words", "top_terms", "vector", "outcome"]
        ).issubset(row)

    assert writer.outcome_counts == {
        OUTCOME_SUCCESS: 1,
        OUTCOME_NO_CONTENT: 2,
        OUTCOME_RETRY_EXHAUSTED: 2,
    }
    assert sum(writer.outcome_counts.values()) == len(titles)


def test_mixed_batch_also_completes_with_concurrent_workers(tmp_path: Path) -> None:
    output = tmp_path / "digest.jsonl"
    titles = ["One", "Two", "Three", "Four"]
    transport = FakeTransport(
        {
            "One": _summary_response("One fine article with several words inside."),
            "Two": FakeResponse(404),
            "Three": _summary_response(""),
            "Four": FakeResponse(429),
        }
    )

    items, writer = _run(
        output, titles, transport, max_retries=1, workers=4
    )

    assert len(items) == len(titles)
    rows = _rows_by_title(_read_rows(output))
    assert rows["One"]["outcome"] == OUTCOME_SUCCESS
    assert rows["Two"]["outcome"] == OUTCOME_NO_CONTENT
    assert rows["Three"]["outcome"] == OUTCOME_NO_CONTENT
    assert rows["Four"]["outcome"] == OUTCOME_RETRY_EXHAUSTED
    assert rows["Four"]["vector"] == []
    assert writer.outcome_counts == {
        OUTCOME_SUCCESS: 1,
        OUTCOME_NO_CONTENT: 2,
        OUTCOME_RETRY_EXHAUSTED: 1,
    }


def test_failed_items_stay_empty_through_the_embedding_batch() -> None:
    # Directly drive the batch stage to prove it never fabricates a vector for
    # failed items even when success and failure share one batch.
    embed = HashEmbedding(size=8, timeout=0.0)
    success = crawler.Item[crawler.ArticleData](
        {
            "title": "Ok",
            "url": "u",
            "extract": "some real text here",
            "word_count": 4,
            "top_terms": [],
            "vector": [],
            "outcome": OUTCOME_SUCCESS,
        }
    )
    no_content = crawler.Item[crawler.ArticleData](
        {
            "title": "No",
            "url": "u",
            "extract": "",
            "word_count": 0,
            "top_terms": [],
            "vector": [],
            "outcome": OUTCOME_NO_CONTENT,
        }
    )
    exhausted = crawler.Item[crawler.ArticleData](
        {
            "title": "Boom",
            "url": "u",
            "extract": "",
            "word_count": 0,
            "top_terms": [],
            "vector": [],
            "outcome": OUTCOME_RETRY_EXHAUSTED,
        }
    )

    processed = embed.process_batch([success, no_content, exhausted])

    assert processed[0].data["vector"] and len(processed[0].data["vector"]) == crawler.VECTOR_DIM
    assert processed[1].data["vector"] == []
    assert processed[2].data["vector"] == []


@pytest.mark.parametrize(
    "outcome", [OUTCOME_NO_CONTENT, OUTCOME_RETRY_EXHAUSTED]
)
def test_cleaning_never_fabricates_words_for_failures(outcome: str) -> None:
    clean = crawler.CleanText()
    item = crawler.Item[crawler.ArticleData](
        {
            "title": "T",
            "url": "u",
            "extract": "left over text that must be ignored",
            "word_count": 0,
            "top_terms": [],
            "vector": [],
            "outcome": outcome,
        }
    )

    result = clean.process(item)

    assert result.data["word_count"] == 0
    assert result.data["top_terms"] == []
