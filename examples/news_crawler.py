"""Wikipedia topic digest: a data journalist's crawler.

Persona: a data journalist who wants a daily digest of random Wikipedia
articles, with a compact text fingerprint for each one.

Stresses:
- real HTTP I/O on threads, with per-thread ``requests.Session`` objects
- retry/backoff on transient HTTP failures and soft errors on permanent ones
- a slow external API feeding fast cleaning and batch feature extraction
- batch stages downstream of a much slower stage (backpressure)
- JSONL output produced by a single-worker batch sink

Every article ends the run with an explicit outcome, so a consumer of the
JSONL can tell a page without usable content apart from one the upstream
rate limiter dropped:

- :data:`OUTCOME_SUCCESS`: a summary was fetched, cleaned and embedded
- :data:`OUTCOME_NO_CONTENT`: a permanent soft failure (HTTP 404 or an
  empty summary): no words, terms or vector are fabricated for it
- :data:`OUTCOME_RETRY_EXHAUSTED`: a transient failure (HTTP 429/5xx, or a
  network error) kept failing through the whole retry strategy: the item is
  not treated as a summary and its vector stays empty

Run from the repository root::

    python examples/news_crawler.py --items 120 --workers 8
"""

from __future__ import annotations

__author__ = "Giacomo Berardi <giacbrd.com>"

import argparse
import hashlib
import json
import math
import re
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence, TypedDict
from urllib.parse import quote

import requests
from _common import RunMetrics, add_scale_args, configure_logging, consume

from smartpipeline import BatchStage, Item, Pipeline, SoftError, Source, Stage
from smartpipeline.error.exceptions import RetryError

WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
USER_AGENT = (
    "SmartPipeline-release-check/1.0 (https://github.com/giacbrd/SmartPipeline)"
)
TOKEN_RE = re.compile(r"[a-z][a-z'-]{2,}")
TERM_WORDS = frozenset({"the", "and", "for", "that", "with", "from", "this", "are"})
VECTOR_DIM = 128

# Explicit item outcomes written to every JSONL row.
OUTCOME_SUCCESS = "success"
OUTCOME_NO_CONTENT = "no_content"
OUTCOME_RETRY_EXHAUSTED = "retry_exhausted"
OUTCOMES = (OUTCOME_SUCCESS, OUTCOME_NO_CONTENT, OUTCOME_RETRY_EXHAUSTED)

# HTTP status codes worth retrying instead of failing the item immediately.
TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class ArticleData(TypedDict):
    title: str
    url: str
    extract: str
    word_count: int
    top_terms: list[tuple[str, int]]
    vector: list[float]
    outcome: str


class TransientHTTPError(Exception):
    """A response worth retrying (429 or 5xx)."""


class HttpGet(Protocol):
    """Shape of the HTTP call used by :class:`PageFetcher`.

    ``requests.Session.get`` satisfies it, and so do the in-process test stubs:
    this keeps the stage testable without touching the network or the global
    ``requests`` module.
    """

    def __call__(self, url: str, timeout: float = ...) -> requests.Response: ...


def is_retry_exhausted(item: Item[ArticleData]) -> bool:
    """True when the item's fetch stage gave up after exhausting its retries."""
    return any(isinstance(error, RetryError) for error in item.soft_errors())


class RandomWikipediaSource(Source[ArticleData]):
    """Pull random article titles from the MediaWiki API."""

    def __init__(self, pages: int, timeout: float = 15.0, titles: Optional[Sequence[str]] = None):
        self._pages = pages
        self._timeout = timeout
        # An explicit title list skips the random-title API entirely: tests use
        # it to drive the pipeline with fully stubbed HTTP responses.
        self._titles: List[str] = list(titles) if titles is not None else []
        self._seen: set[str] = set(self._titles)
        self._emitted = 0

    def _fetch_titles(self) -> None:
        # rnlimit is capped by MediaWiki; ask for batches until we have enough
        # unique titles, giving up after a few empty rounds.
        empty_rounds = 0
        while len(self._titles) < self._pages and empty_rounds < 3:
            before = len(self._titles)
            response = requests.get(
                WIKI_API,
                params={
                    "action": "query",
                    "list": "random",
                    "rnnamespace": 0,
                    "rnlimit": min(50, self._pages - len(self._titles)),
                    "format": "json",
                },
                headers={"User-Agent": USER_AGENT},
                timeout=self._timeout,
            )
            response.raise_for_status()
            batch = response.json().get("query", {}).get("random", [])
            if not batch:
                empty_rounds += 1
                continue
            for page in batch:
                title = page.get("title")
                if title and title not in self._seen:
                    self._seen.add(title)
                    self._titles.append(title)
            if len(self._titles) == before:
                empty_rounds += 1
        if not self._titles:
            raise RuntimeError("the Wikipedia random API returned no titles")

    def pop(self):
        if not self._titles:
            self._fetch_titles()
        if self._emitted >= self._pages or self._emitted >= len(self._titles):
            self.stop()
            return None
        title = self._titles[self._emitted]
        self._emitted += 1
        url = "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_"))
        return Item[ArticleData](
            {
                "title": title,
                "url": url,
                "extract": "",
                "word_count": 0,
                "top_terms": [],
                "vector": [],
                "outcome": OUTCOME_SUCCESS,
            }
        )


class PageFetcher(Stage[ArticleData]):
    """Fetch the REST summary of a page, one HTTP call per item."""

    def __init__(self, timeout: float = 10.0, transport: Optional[HttpGet] = None):
        self._timeout = timeout
        self._transport = transport
        self._local = threading.local()
        self._sessions: list[requests.Session] = []
        self._sessions_lock = threading.Lock()

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(
                {
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                }
            )
            self._local.session = session
            with self._sessions_lock:
                self._sessions.append(session)
        return session

    def _get(self, url: str) -> requests.Response:
        # The injected transport replaces the per-thread session, which keeps
        # tests free of real sockets while production uses real sessions.
        if self._transport is not None:
            return self._transport(url, timeout=self._timeout)
        return self._session().get(url, timeout=self._timeout)

    def process(self, item: Item[ArticleData]) -> Item[ArticleData]:
        title = item.data["title"]
        response = self._get(
            WIKI_SUMMARY + quote(title.replace(" ", "_"), safe="")
        )
        if response.status_code in TRANSIENT_STATUS_CODES:
            raise TransientHTTPError(f"HTTP {response.status_code} for {title}")
        if response.status_code == 404:
            raise SoftError(f"no Wikipedia summary for {title}")
        response.raise_for_status()
        payload = response.json()
        extract = (payload.get("extract") or "").strip()
        if not extract:
            raise SoftError(f"empty extract for {title}")
        item.data["extract"] = extract
        item.data["url"] = (
            payload.get("content_urls", {}).get("desktop", {}).get("page")
            or item.data["url"]
        )
        return item

    def on_end(self) -> None:
        with self._sessions_lock:
            for session in self._sessions:
                session.close()
            self._sessions.clear()


class OutcomeClassifier(Stage[ArticleData]):
    """Give every item an explicit outcome based on its fetch-stage errors.

    It runs right after the fetch stage: a :class:`~smartpipeline.error.exceptions.RetryError`
    means the retry strategy was exhausted, any other soft error means the page
    has no usable content, and an item without errors is a successful summary.
    Downstream stages only read the outcome and never invent data for failures.
    """

    def process(self, item: Item[ArticleData]) -> Item[ArticleData]:
        if is_retry_exhausted(item):
            item.data["outcome"] = OUTCOME_RETRY_EXHAUSTED
        elif item.has_soft_errors():
            item.data["outcome"] = OUTCOME_NO_CONTENT
        else:
            item.data["outcome"] = OUTCOME_SUCCESS
        return item


class CleanText(Stage[ArticleData]):
    """Normalize the extract and keep a few readable statistics."""

    _spaces = re.compile(r"\s+")
    _brackets = re.compile(r"\[\d+\]")

    def process(self, item: Item[ArticleData]) -> Item[ArticleData]:
        if item.data["outcome"] != OUTCOME_SUCCESS:
            # The fetch failed or the page had no content: leave the placeholder
            # values untouched instead of fabricating statistics.
            return item
        text = self._brackets.sub("", item.data["extract"])
        text = self._spaces.sub(" ", text).strip()
        item.data["extract"] = text
        item.data["word_count"] = len(text.split())
        counts = Counter(
            token for token in TOKEN_RE.findall(text.lower()) if token not in TERM_WORDS
        )
        item.data["top_terms"] = counts.most_common(8)
        return item


class HashEmbedding(BatchStage[ArticleData]):
    """A cheap, deterministic text embedding: signed feature hashing."""

    def __init__(self, size: int = 16, timeout: float = 0.5):
        super().__init__(size, timeout)

    def process_batch(
        self, items: Sequence[Item[ArticleData]]
    ) -> Sequence[Item[ArticleData]]:
        for item in items:
            if item.data["outcome"] != OUTCOME_SUCCESS:
                # Failed items must never carry a vector, even when a batch
                # mixes successful and failed articles.
                item.data["vector"] = []
                continue
            vector = [0.0] * VECTOR_DIM
            for token in TOKEN_RE.findall(item.data["extract"].lower()):
                digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
                value = int.from_bytes(digest, "big")
                index = value % VECTOR_DIM
                vector[index] += 1.0 if (value >> 63) & 1 else -1.0
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            item.data["vector"] = [round(value / norm, 4) for value in vector]
        return items


class DigestWriter(BatchStage[ArticleData]):
    """Write one JSONL row per article and keep the ten longest pages."""

    def __init__(self, path: Path, size: int = 25, timeout: float = 0.5):
        super().__init__(size, timeout)
        self._path = path
        self._file = None
        self._rows = 0
        self._words = 0
        self._with_vector = 0
        self._outcome_counts: Dict[str, int] = {outcome: 0 for outcome in OUTCOMES}
        self._longest: List[tuple[int, str]] = []

    @property
    def outcome_counts(self) -> Dict[str, int]:
        """Number of rows written for each of the three outcomes."""
        return dict(self._outcome_counts)

    def on_start(self) -> None:
        self._file = self._path.open("w", encoding="utf-8")

    def process_batch(
        self, items: Sequence[Item[ArticleData]]
    ) -> Sequence[Item[ArticleData]]:
        assert self._file is not None
        for item in items:
            data = item.data
            outcome = data["outcome"]
            row: Dict[str, Any] = {
                "title": data["title"],
                "url": data["url"],
                "words": data["word_count"],
                "top_terms": data["top_terms"],
                "vector": data["vector"],
                "outcome": outcome,
                "errors": [str(error) for error in item.soft_errors()],
            }
            self._file.write(json.dumps(row) + "\n")
            self._rows += 1
            self._outcome_counts[outcome] = self._outcome_counts.get(outcome, 0) + 1
            self._words += data["word_count"]
            if data["vector"]:
                self._with_vector += 1
            if data["word_count"]:
                self._longest.append((data["word_count"], data["title"]))
                self._longest = sorted(self._longest, reverse=True)[:10]
        self._file.flush()
        return items

    def on_end(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


def build_pipeline(
    args: argparse.Namespace,
    output: Path,
    *,
    source: Optional[RandomWikipediaSource] = None,
    fetcher: Optional[PageFetcher] = None,
    writer: Optional[DigestWriter] = None,
    max_retries: int = 2,
    backoff: float = 0.3,
) -> Pipeline[ArticleData]:
    return (
        Pipeline[ArticleData](max_queues_size=args.queue_size)
        .set_source(source or RandomWikipediaSource(args.items))
        .append(
            "fetch",
            fetcher or PageFetcher(),
            concurrency=max(1, args.workers),
            retryable_errors=(TransientHTTPError, requests.RequestException),
            max_retries=max_retries,
            backoff=backoff,
        )
        .append("classify", OutcomeClassifier())
        .append("clean", CleanText())
        .append("embed", HashEmbedding(size=16), concurrency=2)
        .append("digest", writer or DigestWriter(output))
        .build()
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_scale_args(parser, items=120, item_label="Wikipedia pages", workers=8)
    args = parser.parse_args()
    configure_logging()

    with tempfile.TemporaryDirectory(prefix="smartpipeline-news-") as tmp:
        output = Path(tmp) / "digest.jsonl"
        pipeline = build_pipeline(args, output)
        metrics = RunMetrics()
        started = time.perf_counter()
        elapsed = consume(pipeline, metrics)
        setup_and_run = time.perf_counter() - started

        writer = pipeline.get_stage("digest")
        assert isinstance(writer, DigestWriter)
        longest = ", ".join(title for _, title in writer._longest[:3])
        outcome_counts = writer.outcome_counts
        metrics.report(
            "Wikipedia digest",
            elapsed,
            extra=[
                f"jsonl_rows={writer._rows} total_words={writer._words} "
                f"with_vector={writer._with_vector}",
                "outcomes: "
                + " ".join(
                    f"{outcome}={outcome_counts[outcome]}" for outcome in OUTCOMES
                ),
                f"output_bytes={output.stat().st_size if output.exists() else 0}",
                f"longest_pages={longest}",
                f"run_plus_setup={setup_and_run:.2f}s",
            ],
        )


if __name__ == "__main__":
    main()
