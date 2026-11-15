"""
Unit tests for pipeline/fetch_feeds.py.

Everything is built from local fake data — no network access and no real
defuddle subprocess:
- fetch_feeds.fetch_feed is replaced with an in-memory feed source
- fetch_feeds.extract_with_defuddle is replaced with a URL-keyed fake that
  returns canned content or raises the same typed errors as the real code

Each test runs against isolated state files under tmp_path by monkeypatching
the path constants imported from config.
"""

import json

import config
import fetch_feeds as ff
import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_paths(tmp_path, monkeypatch):
    """
    Redirect every state/log/feed path used by fetch_feeds into tmp_path.

    fetch_feeds.py binds the constants with `from config import ...`, so the
    names must be patched in BOTH the config module and the fetch_feeds
    namespace (the latter is what the code under test actually looks up).
    """
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()

    redirects = {
        "STATE_DIR": state_dir,
        "PROCESSED_URLS_FILE": state_dir / "processed_urls.json",
        "STAGING_FILE": state_dir / "staged_articles.json",
        "FETCH_REPORT_FILE": state_dir / "fetch_outcomes.json",
        "URL_LOG_FILE": logs_dir / "urls.txt",
        "FEEDS_FILE": tmp_path / "feeds.yaml",
    }
    for name, value in redirects.items():
        monkeypatch.setattr(config, name, value)
        if hasattr(ff, name):  # only constants actually imported by fetch_feeds
            monkeypatch.setattr(ff, name, value)

    (tmp_path / "feeds.yaml").write_text(
        "feeds:\n  - https://feed.example/a.xml\n  - https://feed.example/b.xml\n"
    )

    return tmp_path


@pytest.fixture
def patch_fetcher(monkeypatch):
    """Return a setter for the in-memory feed entries (feed_url -> entries)."""
    feed_data: dict[str, list[dict]] = {}

    def fake_fetch_feed(feed_url, cutoff):
        return list(feed_data.get(feed_url, []))

    monkeypatch.setattr(ff, "fetch_feed", fake_fetch_feed)

    def set_feed_data(data):
        feed_data.clear()
        feed_data.update(data)

    return set_feed_data


@pytest.fixture
def patch_extractor(monkeypatch):
    """Return a setter mapping URL -> content string or Exception instance."""
    extraction_map: dict[str, object] = {}

    def fake_extract(url):
        result = extraction_map[url]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(ff, "extract_with_defuddle", fake_extract)

    def set_extraction_map(mapping):
        extraction_map.clear()
        extraction_map.update(mapping)

    return set_extraction_map


def entry(url, title="Title", feed="https://feed.example/a.xml", published_at=None):
    return {
        "url": url,
        "title": title,
        "feed": feed,
        "published_at": published_at,
    }


LONG_CONTENT = "x" * ff.MIN_CONTENT_LENGTH
SHORT_CONTENT = "y" * (ff.MIN_CONTENT_LENGTH - 1)


def read_report():
    return json.loads(config.FETCH_REPORT_FILE.read_text())


def read_staging():
    return json.loads(config.STAGING_FILE.read_text())


def outcomes_by_url(report):
    return {r["url"]: r["outcome"] for r in report["outcomes"]}


# ---------------------------------------------------------------------------
# Required path 1: normal happy path
# ---------------------------------------------------------------------------


def test_normal_run_stages_articles_and_writes_manifest(
    fake_paths, patch_fetcher, patch_extractor
):
    patch_fetcher(
        {
            "https://feed.example/a.xml": [
                entry("https://example.com/good-1", "Good One"),
                entry("https://example.com/good-2", "Good Two"),
            ]
        }
    )
    patch_extractor(
        {
            "https://example.com/good-1": LONG_CONTENT,
            "https://example.com/good-2": LONG_CONTENT + " extra",
        }
    )

    ff.main()

    # Staging file contains exactly the two good articles
    staged = read_staging()
    assert [a["url"] for a in staged] == [
        "https://example.com/good-1",
        "https://example.com/good-2",
    ]
    assert all(a["content"] for a in staged)

    report = read_report()
    outcomes = outcomes_by_url(report)
    assert outcomes == {
        "https://example.com/good-1": ff.OUTCOME_STAGED,
        "https://example.com/good-2": ff.OUTCOME_STAGED,
    }

    # One-to-one correspondence: manifest staged URLs == staged_articles URLs
    staged_in_report = {
        r["url"] for r in report["outcomes"] if r["outcome"] == ff.OUTCOME_STAGED
    }
    assert staged_in_report == {a["url"] for a in staged}
    assert report["total_candidates"] == 2
    assert report["summary"] == {ff.OUTCOME_STAGED: 2}

    # Every record carries the required audit fields
    for record in report["outcomes"]:
        assert {"url", "title", "feed", "outcome", "recorded_at"}.issubset(
            record.keys()
        )

    # Candidates were registered in the dedup table during collection
    state = json.loads(config.PROCESSED_URLS_FILE.read_text())
    assert set(state["processed_urls"]) == {
        "https://example.com/good-1",
        "https://example.com/good-2",
    }


# ---------------------------------------------------------------------------
# Required path 2: content too short
# ---------------------------------------------------------------------------


def test_too_short_article_is_recorded_but_not_staged(
    fake_paths, patch_fetcher, patch_extractor
):
    patch_fetcher(
        {
            "https://feed.example/a.xml": [
                entry("https://example.com/long", "Long"),
                entry("https://example.com/short", "Short"),
            ]
        }
    )
    patch_extractor(
        {
            "https://example.com/long": LONG_CONTENT,
            "https://example.com/short": SHORT_CONTENT,
        }
    )

    ff.main()

    staged = read_staging()
    assert [a["url"] for a in staged] == ["https://example.com/long"]

    report = read_report()
    outcomes = outcomes_by_url(report)
    assert outcomes == {
        "https://example.com/long": ff.OUTCOME_STAGED,
        "https://example.com/short": ff.OUTCOME_TOO_SHORT,
    }

    short_record = next(
        r for r in report["outcomes"] if r["url"] == "https://example.com/short"
    )
    assert "200" in short_record["detail"]
    # The short article is still marked seen (no retry loop), but not staged
    state = json.loads(config.PROCESSED_URLS_FILE.read_text())
    assert "https://example.com/short" in state["processed_urls"]
    assert report["summary"][ff.OUTCOME_TOO_SHORT] == 1
    assert report["summary"][ff.OUTCOME_STAGED] == 1


def test_empty_defuddle_content_counts_as_too_short(
    fake_paths, patch_fetcher, patch_extractor
):
    """Empty content (defuddle returned nothing) is a too-short outcome."""
    patch_fetcher(
        {"https://feed.example/a.xml": [entry("https://example.com/empty", "Empty")]}
    )
    patch_extractor({"https://example.com/empty": ""})

    ff.main()

    report = read_report()
    assert outcomes_by_url(report) == {
        "https://example.com/empty": ff.OUTCOME_TOO_SHORT
    }
    assert read_staging() == []


# ---------------------------------------------------------------------------
# Required path 3: extraction exceptions (timeout, missing binary, other)
# ---------------------------------------------------------------------------


def test_extraction_timeout_does_not_abort_run(
    fake_paths, patch_fetcher, patch_extractor
):
    patch_fetcher(
        {
            "https://feed.example/a.xml": [
                entry("https://example.com/slow", "Slow Page"),
                entry("https://example.com/fine", "Fine Page"),
            ]
        }
    )
    patch_extractor(
        {
            "https://example.com/slow": ff.ExtractionTimeout("timed out after 30s"),
            "https://example.com/fine": LONG_CONTENT,
        }
    )

    ff.main()  # must not raise

    report = read_report()
    outcomes = outcomes_by_url(report)
    assert outcomes == {
        "https://example.com/slow": ff.OUTCOME_TIMEOUT,
        "https://example.com/fine": ff.OUTCOME_STAGED,
    }
    slow_record = next(
        r for r in report["outcomes"] if r["url"] == "https://example.com/slow"
    )
    assert "timed out" in slow_record["detail"]
    # Failed extraction must never appear in staging
    assert [a["url"] for a in read_staging()] == ["https://example.com/fine"]
    # ...but it is still marked seen
    state = json.loads(config.PROCESSED_URLS_FILE.read_text())
    assert "https://example.com/slow" in state["processed_urls"]


def test_missing_defuddle_binary_distinguished_from_timeout(
    fake_paths, patch_fetcher, patch_extractor
):
    patch_fetcher(
        {
            "https://feed.example/a.xml": [
                entry("https://example.com/no-binary", "No Binary"),
            ]
        }
    )
    patch_extractor(
        {
            "https://example.com/no-binary": ff.ExtractionCommandMissing(
                "defuddle executable not found on PATH"
            ),
        }
    )

    ff.main()

    report = read_report()
    assert outcomes_by_url(report) == {
        "https://example.com/no-binary": ff.OUTCOME_COMMAND_MISSING
    }
    assert read_staging() == []


def test_generic_extraction_error_is_classified_separately(
    fake_paths, patch_fetcher, patch_extractor
):
    patch_fetcher(
        {
            "https://feed.example/a.xml": [
                entry("https://example.com/bad-json", "Bad JSON"),
            ]
        }
    )
    patch_extractor(
        {
            "https://example.com/bad-json": ff.ExtractionError(
                "defuddle returned invalid JSON"
            ),
        }
    )

    ff.main()

    report = read_report()
    assert outcomes_by_url(report) == {
        "https://example.com/bad-json": ff.OUTCOME_EXTRACTION_ERROR
    }
    assert report["summary"] == {ff.OUTCOME_EXTRACTION_ERROR: 1}


def test_unexpected_exception_in_extraction_does_not_abort_run(
    fake_paths, patch_fetcher, patch_extractor
):
    """The defensive catch-all keeps the whole run alive for one bad URL."""
    patch_fetcher(
        {
            "https://feed.example/a.xml": [
                entry("https://example.com/boom", "Boom"),
                entry("https://example.com/ok", "OK"),
            ]
        }
    )
    patch_extractor(
        {
            "https://example.com/boom": RuntimeError("something completely unexpected"),
            "https://example.com/ok": LONG_CONTENT,
        }
    )

    ff.main()

    report = read_report()
    assert outcomes_by_url(report) == {
        "https://example.com/boom": ff.OUTCOME_EXTRACTION_ERROR,
        "https://example.com/ok": ff.OUTCOME_STAGED,
    }
    assert [a["url"] for a in read_staging()] == ["https://example.com/ok"]


# ---------------------------------------------------------------------------
# Already-processed skipping (must be recorded live, not inferred afterwards)
# ---------------------------------------------------------------------------


def test_already_processed_urls_are_skipped_and_recorded(
    fake_paths, patch_fetcher, patch_extractor
):
    config.PROCESSED_URLS_FILE.write_text(
        json.dumps(
            {
                "processed_urls": {
                    "https://example.com/seen-before": "2026-11-14",
                },
                "last_updated": None,
            }
        )
    )
    patch_fetcher(
        {
            "https://feed.example/a.xml": [
                entry("https://example.com/seen-before", "Seen Before"),
                entry("https://example.com/fresh", "Fresh"),
            ]
        }
    )
    patch_extractor({"https://example.com/fresh": LONG_CONTENT})

    ff.main()

    report = read_report()
    outcomes = outcomes_by_url(report)
    assert outcomes == {
        "https://example.com/seen-before": ff.OUTCOME_ALREADY_PROCESSED,
        "https://example.com/fresh": ff.OUTCOME_STAGED,
    }
    # Skipped URL is not extracted and not staged
    assert [a["url"] for a in read_staging()] == ["https://example.com/fresh"]


def test_duplicate_url_across_feeds_is_recorded_once_as_candidate(
    fake_paths, patch_fetcher, patch_extractor
):
    shared = entry("https://example.com/shared", "Shared")
    patch_fetcher(
        {
            "https://feed.example/a.xml": [shared],
            "https://feed.example/b.xml": [
                entry(
                    "https://example.com/shared",
                    "Shared Dupe",
                    feed="https://feed.example/b.xml",
                )
            ],
        }
    )
    patch_extractor({"https://example.com/shared": LONG_CONTENT})

    ff.main()

    report = read_report()
    outcomes = outcomes_by_url(report)
    # First occurrence becomes a candidate; second is recorded as skipped
    assert outcomes["https://example.com/shared"] in (
        ff.OUTCOME_STAGED,
        ff.OUTCOME_ALREADY_PROCESSED,
    )
    assert report["total_candidates"] == 2
    assert (
        len([r for r in report["outcomes"] if r["outcome"] == ff.OUTCOME_STAGED]) == 1
    )
    assert (
        len(
            [
                r
                for r in report["outcomes"]
                if r["outcome"] == ff.OUTCOME_ALREADY_PROCESSED
            ]
        )
        == 1
    )
    assert len(read_staging()) == 1


# ---------------------------------------------------------------------------
# MAX_ARTICLES cap
# ---------------------------------------------------------------------------


def test_capped_candidates_are_recorded_not_staged(
    fake_paths, patch_fetcher, patch_extractor, monkeypatch
):
    monkeypatch.setattr(ff, "MAX_ARTICLES", 1)
    patch_fetcher(
        {
            "https://feed.example/a.xml": [
                entry("https://example.com/one", "One"),
                entry("https://example.com/two", "Two"),
            ]
        }
    )
    patch_extractor(
        {
            "https://example.com/one": LONG_CONTENT,
            "https://example.com/two": LONG_CONTENT,
        }
    )

    ff.main()

    report = read_report()
    outcomes = outcomes_by_url(report)
    assert outcomes == {
        "https://example.com/one": ff.OUTCOME_STAGED,
        "https://example.com/two": ff.OUTCOME_CAPPED,
    }
    assert len(read_staging()) == 1


def test_empty_run_writes_empty_manifest_and_staging(
    fake_paths, patch_fetcher, patch_extractor
):
    """A run with no new URLs still produces a valid, consistent manifest."""
    patch_fetcher({"https://feed.example/a.xml": []})

    ff.main()

    report = read_report()
    assert report["total_candidates"] == 0
    assert report["summary"] == {}
    assert report["outcomes"] == []
    assert read_staging() == []


# ---------------------------------------------------------------------------
# Manifest write failure must surface as an explicit error
# ---------------------------------------------------------------------------


def test_report_write_failure_exits_nonzero(
    fake_paths, patch_fetcher, patch_extractor, monkeypatch
):
    patch_fetcher(
        {"https://feed.example/a.xml": [entry("https://example.com/good", "Good")]}
    )
    patch_extractor({"https://example.com/good": LONG_CONTENT})

    # Make the manifest path itself a directory: its parent mkdir succeeds,
    # but writing a file over a directory raises OSError, simulating a
    # disk/permission failure inside the guarded report-write block.
    blocked_report = ff.STAGING_FILE.parent / "fetch_outcomes.json"
    blocked_report.mkdir()
    monkeypatch.setattr(config, "FETCH_REPORT_FILE", blocked_report)
    monkeypatch.setattr(ff, "FETCH_REPORT_FILE", blocked_report)

    with pytest.raises(SystemExit) as exc_info:
        ff.main()
    assert exc_info.value.code != 0

    # Staging and state were still written, only the manifest failed;
    # the blocked path remains a directory (no manifest file was produced).
    assert len(read_staging()) == 1
    assert blocked_report.is_dir()
    assert not blocked_report.is_file()


def test_invariant_guard_detects_staging_manifest_mismatch(
    fake_paths, patch_fetcher, patch_extractor
):
    """Direct test of the staged↔manifest invariant used by the writer."""
    patch_fetcher(
        {"https://feed.example/a.xml": [entry("https://example.com/good", "Good")]}
    )
    patch_extractor({"https://example.com/good": LONG_CONTENT})

    report = ff.FetchReport(run_at="2026-11-15T00:00:00+00:00", records=[])
    candidate = entry("https://example.com/good", "Good")
    report.add(candidate, ff.OUTCOME_TOO_SHORT, "tampered record")
    articles = [{"url": "https://example.com/good"}]

    with pytest.raises(RuntimeError, match="mismatch"):
        ff.write_fetch_report(report, articles)
    assert not config.FETCH_REPORT_FILE.exists()
