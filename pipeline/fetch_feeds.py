"""
fetch_feeds.py

Step 1 of the pipeline:
- Parse feeds.yaml for the list of RSS URLs
- Fetch each feed with feedparser (up to ENTRIES_PER_FEED recent entries)
- Skip URLs already in state/processed_urls.json
- Run defuddle on new article URLs to extract clean markdown content
- Write state/staged_articles.json for classify.py to consume
- Update state/processed_urls.json (all seen URLs, including failed extractions)
- Write state/fetch_outcomes.json: an auditable per-URL manifest of what happened
  to every candidate in this run (staged, too short, extraction failure with the
  specific error class, skipped as already processed, or dropped by the
  MAX_ARTICLES cap). This manifest is recorded live during the run because
  processed_urls marks every candidate as seen at collection time, so the
  outcome of a URL cannot be reconstructed afterwards from the dedup table.
"""

import calendar
import json
import os
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import feedparser
import yaml
from config import (
    FEEDS_FILE,
    FETCH_REPORT_FILE,
    PROCESSED_URLS_FILE,
    STAGING_FILE,
    URL_LOG_FILE,
)

MAX_ARTICLES = int(os.environ.get("MAX_ARTICLES", 100))
ENTRIES_PER_FEED = int(os.environ.get("ENTRIES_PER_FEED", 5))
MAX_ARTICLE_AGE_DAYS = int(os.environ.get("MAX_ARTICLE_AGE_DAYS", 7))
FEED_FETCH_WORKERS = 30
DEFUDDLE_TIMEOUT = 30
MIN_CONTENT_LENGTH = 200
MAX_CONTENT_LENGTH = 8000
MAX_ERROR_DETAIL_LENGTH = 300

# --- Outcome labels written to state/fetch_outcomes.json ---
OUTCOME_STAGED = "staged"
OUTCOME_TOO_SHORT = "content_too_short"
OUTCOME_TIMEOUT = "extraction_timeout"
OUTCOME_COMMAND_MISSING = "extraction_command_missing"
OUTCOME_EXTRACTION_ERROR = "extraction_error"
OUTCOME_ALREADY_PROCESSED = "already_processed"
OUTCOME_CAPPED = "capped"


class ExtractionError(Exception):
    """Base class for defuddle extraction failures with a manifest outcome."""

    outcome = OUTCOME_EXTRACTION_ERROR


class ExtractionTimeout(ExtractionError):
    """defuddle did not finish within DEFUDDLE_TIMEOUT seconds."""

    outcome = OUTCOME_TIMEOUT


class ExtractionCommandMissing(ExtractionError):
    """The defuddle executable is not installed / not on PATH."""

    outcome = OUTCOME_COMMAND_MISSING


@dataclass
class FetchReport:
    """Accumulates the per-URL outcome records for one pipeline run."""

    run_at: str
    records: list[dict]

    def add(self, candidate: dict, outcome: str, detail: str | None = None) -> None:
        record = {
            "url": candidate["url"],
            "title": candidate["title"],
            "feed": candidate["feed"],
            "outcome": outcome,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        if detail is not None:
            record["detail"] = detail[:MAX_ERROR_DETAIL_LENGTH]
        self.records.append(record)

    def urls_by_outcome(self, outcome: str) -> set[str]:
        return {r["url"] for r in self.records if r["outcome"] == outcome}

    def to_payload(self) -> dict:
        counts = Counter(r["outcome"] for r in self.records)
        return {
            "run_at": self.run_at,
            "total_candidates": len(self.records),
            "summary": dict(sorted(counts.items())),
            "outcomes": self.records,
        }


def load_state() -> dict:
    if not PROCESSED_URLS_FILE.exists():
        return {"processed_urls": {}, "last_updated": None}
    data = json.loads(PROCESSED_URLS_FILE.read_text())
    urls = data.get("processed_urls", {})
    # Migrate from old list format
    if isinstance(urls, list):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        urls = {url: today for url in urls}
    data["processed_urls"] = urls
    return data


def save_state(state: dict) -> None:
    PROCESSED_URLS_FILE.write_text(json.dumps(state, indent=2))


def load_feed_urls() -> list[str]:
    data = yaml.safe_load(FEEDS_FILE.read_text())
    return [
        url
        for url in data.get("feeds", [])
        if isinstance(url, str) and url.startswith("http")
    ]


def _parse_entry_date(entry) -> datetime | None:
    """Extract published/updated date from a feedparser entry."""
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            try:
                return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
            except (ValueError, OverflowError, OSError):
                continue
    return None


def fetch_feed(feed_url: str, cutoff: datetime) -> list[dict]:
    try:
        d = feedparser.parse(
            feed_url,
            request_headers={"User-Agent": "Mozilla/5.0 (security-feed-bot)"},
        )
        entries = []
        for entry in d.entries:
            link = entry.get("link", "").strip()
            title = entry.get("title", "").strip()
            if not (link and title and link.startswith("http")):
                continue
            published_at = _parse_entry_date(entry)
            if published_at and published_at < cutoff:
                continue
            entries.append(
                {
                    "url": link,
                    "title": title,
                    "feed": feed_url,
                    "published_at": published_at.isoformat() if published_at else None,
                }
            )
            if len(entries) >= ENTRIES_PER_FEED:
                break
        return entries
    except Exception as e:
        print(f"  [feed error] {feed_url}: {e}", file=sys.stderr)
        return []


def extract_with_defuddle(url: str) -> str:
    """
    Extract article markdown via the defuddle CLI.

    Returns the extracted content on success (including an empty string when
    defuddle itself returns no content). Raises a typed ExtractionError
    subclass for transport/tool failures so the caller can distinguish
    timeouts, a missing defuddle binary, and other subprocess errors.
    """
    try:
        result = subprocess.run(
            ["defuddle", "parse", "--json", "--md", url],
            capture_output=True,
            text=True,
            timeout=DEFUDDLE_TIMEOUT,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise ExtractionError(
                f"defuddle exited with code {result.returncode}: {stderr}"
            )
        data = json.loads(result.stdout)
        # defuddle --json returns {content, title, description, ...}
        return data.get("content", "") or data.get("markdown", "") or ""
    except ExtractionError:
        raise
    except subprocess.TimeoutExpired as e:
        raise ExtractionTimeout(
            f"defuddle timed out after {DEFUDDLE_TIMEOUT}s for {url}"
        ) from e
    except FileNotFoundError as e:
        raise ExtractionCommandMissing(
            f"defuddle executable not found on PATH while processing {url}"
        ) from e
    except json.JSONDecodeError as e:
        raise ExtractionError(f"defuddle returned invalid JSON for {url}: {e}") from e
    except Exception as e:  # defensive: never let one URL kill the whole run
        raise ExtractionError(f"unexpected extraction error for {url}: {e}") from e


def collect_candidates(
    feed_urls: list[str],
    seen_urls: dict[str, str],
    today: str,
    cutoff: datetime,
    report: FetchReport,
) -> list[dict]:
    """
    Fetch every feed concurrently and partition entries into:
    - candidates: URLs not seen before this run (deduped across feeds), and
    - already_processed manifest records for URLs in the dedup table or
      duplicates of a URL already seen earlier in this same run.
    """
    candidates: list[dict] = []
    with ThreadPoolExecutor(max_workers=FEED_FETCH_WORKERS) as executor:
        futures = {executor.submit(fetch_feed, url, cutoff): url for url in feed_urls}
        for future in as_completed(futures):
            entries = future.result()
            for entry in entries:
                if entry["url"] in seen_urls:
                    report.add(entry, OUTCOME_ALREADY_PROCESSED)
                    continue
                candidates.append(entry)
                # Mark seen at collection time: prevents cross-feed duplicates
                # and retry loops on dead URLs (matches processed_urls.json).
                seen_urls[entry["url"]] = today
    return candidates


def process_candidates(
    candidates: list[dict],
    seen_urls: dict[str, str],
    today: str,
    report: FetchReport,
) -> list[dict]:
    """Run defuddle for each candidate and record its real outcome."""
    articles: list[dict] = []
    for i, candidate in enumerate(candidates, 1):
        print(f"[{i}/{len(candidates)}] {candidate['title'][:70]}")

        # Always mark as seen to avoid retry loops on dead URLs
        seen_urls.setdefault(candidate["url"], today)

        try:
            content = extract_with_defuddle(candidate["url"])
        except ExtractionError as e:
            print(f"  ✗ extraction failed ({e.outcome}): {e}", file=sys.stderr)
            report.add(candidate, e.outcome, str(e))
            continue
        except Exception as e:  # noqa: BLE001 - last-resort guard: one bad URL must not abort the run
            print(f"  ✗ extraction failed: {e}", file=sys.stderr)
            report.add(candidate, OUTCOME_EXTRACTION_ERROR, str(e))
            continue

        if len(content) >= MIN_CONTENT_LENGTH:
            articles.append(
                {
                    "url": candidate["url"],
                    "title": candidate["title"],
                    "feed": candidate["feed"],
                    "published_at": candidate.get("published_at"),
                    "content": content[:MAX_CONTENT_LENGTH],
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            report.add(
                candidate,
                OUTCOME_STAGED,
                f"extracted {len(content)} chars",
            )
            print(f"  ✓ {len(content)} chars extracted")
        else:
            report.add(
                candidate,
                OUTCOME_TOO_SHORT,
                f"content length {len(content)} < minimum {MIN_CONTENT_LENGTH}",
            )
            print(f"  ✗ content too short ({len(content)} chars), skipping")

    return articles


def write_fetch_report(report: FetchReport, articles: list[dict]) -> None:
    """
    Persist the outcome manifest and verify the core invariant before writing:
    the URLs marked 'staged' in the manifest must be exactly the URLs written
    to staged_articles.json — skipped/failed URLs must never count as staged.

    Raises on any mismatch or filesystem error so the main flow fails loudly
    instead of silently reporting success without an auditable manifest.
    """
    staged_in_report = report.urls_by_outcome(OUTCOME_STAGED)
    staged_articles = {article["url"] for article in articles}
    if staged_in_report != staged_articles:
        missing = staged_articles - staged_in_report
        extra = staged_in_report - staged_articles
        raise RuntimeError(
            "fetch report/staging mismatch "
            f"(not in report: {sorted(missing)}, not in staging: {sorted(extra)})"
        )

    FETCH_REPORT_FILE.parent.mkdir(exist_ok=True)
    FETCH_REPORT_FILE.write_text(json.dumps(report.to_payload(), indent=2))


def main() -> None:
    FETCH_REPORT_FILE.parent.mkdir(exist_ok=True)
    state = load_state()
    seen_urls: dict[str, str] = state["processed_urls"]  # {url: date_seen}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    feed_urls = load_feed_urls()
    report = FetchReport(run_at=datetime.now(timezone.utc).isoformat(), records=[])

    print(f"Loaded {len(feed_urls)} feeds | {len(seen_urls)} already-seen URLs")

    # --- Step 1: Fetch all feeds concurrently ---
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_ARTICLE_AGE_DAYS)
    candidates = collect_candidates(feed_urls, seen_urls, today, cutoff, report)

    print(f"Found {len(candidates)} new candidate articles (cap: {MAX_ARTICLES})")
    if len(candidates) > MAX_ARTICLES:
        for dropped in candidates[MAX_ARTICLES:]:
            report.add(
                dropped,
                OUTCOME_CAPPED,
                f"dropped by MAX_ARTICLES cap of {MAX_ARTICLES}",
            )
    candidates = candidates[:MAX_ARTICLES]

    # --- Step 2: Extract content via defuddle ---
    articles = process_candidates(candidates, seen_urls, today, report)

    # --- Step 3: Append processed URLs to log file ---
    if articles:
        URL_LOG_FILE.parent.mkdir(exist_ok=True)
        with URL_LOG_FILE.open("a") as f:
            for article in articles:
                f.write(f"[{today}] {article['url']}\n")

    # --- Step 4: Write staging file ---
    STAGING_FILE.write_text(json.dumps(articles, indent=2))

    # --- Step 5: Persist updated state ---
    state["processed_urls"] = seen_urls
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    save_state(state)

    # --- Step 6: Write the auditable outcome manifest (fail loudly on error) ---
    try:
        write_fetch_report(report, articles)
    except Exception as e:  # noqa: BLE001 - any manifest failure must make the step fail
        print(
            f"FATAL: failed to write fetch outcome manifest {FETCH_REPORT_FILE}: {e}",
            file=sys.stderr,
        )
        sys.exit(1)

    counts = Counter(r["outcome"] for r in report.records)
    print(
        f"\nDone — {len(articles)} articles staged for classification | "
        f"outcome manifest: {FETCH_REPORT_FILE} "
        f"({dict(sorted(counts.items()))})"
    )


if __name__ == "__main__":
    main()
