#!/usr/bin/env -S PYTHONDONTWRITEBYTECODE=1 uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["crawl4ai>=0.8.9"]
# ///
# SPDX-License-Identifier: MIT OR Apache-2.0
"""
Per-URL outcome accounting for `scripts/batch_crawl.py` and
`scripts/batch_extract.py`.

What this pins down:

  - Every input URL appears exactly once in the summary, with an outcome of
    `success`, `fetch_failed`, or (batch_extract) `extract_failed`.
  - A failing page does not discard markdown / extraction data from the pages
    that did succeed.
  - A page that crawls but yields nothing from the schema is reported as
    `extract_failed`, distinct from a page that could not be fetched.
  - `--out` and `--max-concurrent` still work; comment and blank lines in the
    URL file are skipped.
  - All-failed runs still write the summary and exit non-zero; any success
    exits 0.

The crawl tests use `raw:` URLs and a closed local port, so no external network
or fixture server is needed. A Playwright browser is still required (as with the
rest of this suite).
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
FIXTURES = REPO_ROOT / "fixtures"
DEAD_URL = "http://127.0.0.1:9/unreachable"

# Windows consoles default to a legacy code page (GBK on this host); the status
# glyphs below are UTF-8. Reconfigure rather than dropping the markers.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

sys.path.insert(0, str(SCRIPTS))
import batch_extract  # noqa: E402  (after sys.path setup)
import batch_crawl  # noqa: E402

# Single-line HTML: a `raw:` URL in a URL *file* occupies exactly one line, so
# embedded newlines would be parsed as separate URLs.
PRODUCT_HTML = (
    '<!DOCTYPE html><html><head><title>Widget Shop</title></head><body><main>'
    '<article class="product-card"><h2 class="title">Widget A</h2><span class="price">$10.00</span>'
    '<a class="link" href="/products/widget-a">Details</a></article>'
    '<article class="product-card"><h2 class="title">Widget B</h2><span class="price">$20.00</span>'
    '<a class="link" href="/products/widget-b">Details</a></article>'
    "</main></body></html>"
)

EMPTY_HTML = (
    '<!DOCTYPE html><html><head><title>Nothing Here</title></head><body>'
    "<p>This page has no product cards.</p></body></html>"
)


def run_script(script: str, args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def write_urls(cwd: Path, urls: list[str]) -> Path:
    """Write a URL file that also exercises comment and blank-line skipping."""
    path = cwd / "urls.txt"
    lines = ["# URLs for the batch test", ""]
    for url in urls:
        lines.extend([url, ""])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def assert_one_entry_per_input(summary: dict, urls: list[str]) -> None:
    entries = summary["results"]
    assert len(entries) == len(urls), f"expected {len(urls)} entries, got {len(entries)}"
    assert [e["url"] for e in entries] == urls, "summary entries must follow input order"
    assert [e["index"] for e in entries] == list(range(len(urls))), "index must match input position"
    assert len({e["url"] for e in entries}) == len(entries), "each input URL must appear exactly once"


def test_batch_crawl_mixed_outcomes() -> None:
    print("Testing batch_crawl.py with a mix of good, empty, and dead pages...")
    with tempfile.TemporaryDirectory() as tmp:
        cwd = Path(tmp)
        urls = ["raw:" + PRODUCT_HTML, DEAD_URL, "raw:" + EMPTY_HTML]
        url_file = write_urls(cwd, urls)
        out_dir = cwd / "crawl_out"

        proc = run_script("batch_crawl.py", [str(url_file), "--max-concurrent", "2", "--out", str(out_dir)], cwd)
        assert proc.returncode == 0, f"expected exit 0 with one success, got {proc.returncode}\n{proc.stderr[-2000:]}"

        summary = json.loads((out_dir / "_summary.json").read_text(encoding="utf-8"))
        assert_one_entry_per_input(summary, urls)
        assert summary["success_count"] == 2
        assert summary["failed_count"] == 1
        assert summary["status_counts"] == {"success": 2, "fetch_failed": 1, "write_failed": 0}

        by_url = {e["url"]: e for e in summary["results"]}
        for good_url in ("raw:" + PRODUCT_HTML, "raw:" + EMPTY_HTML):
            entry = by_url[good_url]
            assert entry["status"] == "success", f"{good_url[:40]} should succeed: {entry}"
            assert entry["file"], "successful entry must record its markdown path"
            md_path = Path(entry["file"])
            assert md_path.exists(), f"markdown file missing: {md_path}"
            assert "Widget Shop" in md_path.read_text(encoding="utf-8") or "Nothing Here" in md_path.read_text(
                encoding="utf-8"
            ), "markdown file should carry the page title"

        dead = by_url[DEAD_URL]
        assert dead["status"] == "fetch_failed"
        assert dead["file"] is None
        assert dead["error"], "failed entry must keep the failure reason"

        # Console output names every URL's outcome.
        assert proc.stdout.count("OK   ") == 2, proc.stdout[-1000:]
        assert "fetch_failed" in proc.stdout
    print("✅ batch_crawl mixed outcomes: 2 written, 1 fetch_failed, summary complete")


def test_batch_crawl_total_failure() -> None:
    print("\nTesting batch_crawl.py when every page fails...")
    with tempfile.TemporaryDirectory() as tmp:
        cwd = Path(tmp)
        urls = [DEAD_URL, "http://127.0.0.1:9/also-unreachable"]
        url_file = write_urls(cwd, urls)
        out_dir = cwd / "crawl_out"

        proc = run_script("batch_crawl.py", [str(url_file), "--out", str(out_dir)], cwd)
        assert proc.returncode != 0, "all-failed run must exit non-zero"

        summary_path = out_dir / "_summary.json"
        assert summary_path.exists(), "summary must be written even when every page fails"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert_one_entry_per_input(summary, urls)
        assert summary["success_count"] == 0
        assert summary["failed_count"] == len(urls)
        assert all(e["status"] == "fetch_failed" and e["error"] for e in summary["results"])
    print("✅ batch_crawl all-failed: summary written, exit non-zero")


def test_batch_extract_mixed_outcomes() -> None:
    print("\nTesting batch_extract.py with a mix of good, empty, and dead pages...")
    with tempfile.TemporaryDirectory() as tmp:
        cwd = Path(tmp)
        schema_path = cwd / "schema.json"
        schema_path.write_text((FIXTURES / "sample-products-schema.json").read_text(encoding="utf-8"), encoding="utf-8")
        # The fixture HTML is multi-line; a URL file is line-delimited, so collapse
        # the whitespace between tags to keep the `raw:` URL on one line.
        fixture_html = " ".join((FIXTURES / "sample-products.html").read_text(encoding="utf-8").split())
        expected = json.loads((FIXTURES / "sample-products-expected.json").read_text(encoding="utf-8"))

        urls = ["raw:" + fixture_html, "raw:" + EMPTY_HTML, DEAD_URL]
        url_file = write_urls(cwd, urls)
        out_file = cwd / "out" / "products.json"

        proc = run_script(
            "batch_extract.py",
            [str(url_file), str(schema_path), "--max-concurrent", "2", "--out", str(out_file)],
            cwd,
        )
        assert proc.returncode == 0, f"expected exit 0 with one success, got {proc.returncode}\n{proc.stderr[-2000:]}"

        summary_file = out_file.with_name("products.summary.json")
        summary = json.loads(summary_file.read_text(encoding="utf-8"))
        assert_one_entry_per_input(summary, urls)
        assert summary["status_counts"] == {"success": 1, "fetch_failed": 1, "extract_failed": 1}
        assert summary["success_count"] == 1

        by_url = {e["url"]: e for e in summary["results"]}

        good = by_url["raw:" + fixture_html]
        assert good["status"] == "success"
        assert good["item_count"] == len(expected)
        assert good["file"] and Path(good["file"]).exists(), "per-URL data file must be written"
        assert json.loads(Path(good["file"]).read_text(encoding="utf-8")) == expected

        # Aggregate output keeps only the successful record, with the original shape.
        aggregate = json.loads(out_file.read_text(encoding="utf-8"))
        assert isinstance(aggregate, list) and len(aggregate) == 1
        assert aggregate[0]["url"] == "raw:" + fixture_html
        assert aggregate[0]["data"] == expected

        empty = by_url["raw:" + EMPTY_HTML]
        assert empty["status"] == "extract_failed", "a page that crawls but matches nothing is extract_failed"
        assert empty["file"] is None
        assert "0 item" in empty["error"] or "no content" in empty["error"], empty["error"]

        dead = by_url[DEAD_URL]
        assert dead["status"] == "fetch_failed"
        assert dead["file"] is None
        assert dead["error"]
    print("✅ batch_extract mixed outcomes: success + extract_failed + fetch_failed, all recorded")


def test_batch_extract_total_failure() -> None:
    print("\nTesting batch_extract.py when every page fails...")
    with tempfile.TemporaryDirectory() as tmp:
        cwd = Path(tmp)
        schema_path = cwd / "schema.json"
        schema_path.write_text((FIXTURES / "sample-products-schema.json").read_text(encoding="utf-8"), encoding="utf-8")
        urls = [DEAD_URL]
        url_file = write_urls(cwd, urls)
        out_file = cwd / "products.json"

        proc = run_script("batch_extract.py", [str(url_file), str(schema_path), "--out", str(out_file)], cwd)
        assert proc.returncode != 0, "all-failed run must exit non-zero"

        summary = json.loads(out_file.with_name("products.summary.json").read_text(encoding="utf-8"))
        assert_one_entry_per_input(summary, urls)
        assert summary["success_count"] == 0
        assert summary["status_counts"]["fetch_failed"] == 1
        assert json.loads(out_file.read_text(encoding="utf-8")) == [], "aggregate file must exist and be empty"
    print("✅ batch_extract all-failed: summary written, exit non-zero")


class _FakeResult:
    def __init__(self, html: str = "<html></html>", url: str = "raw:fake") -> None:
        self.html = html
        self.url = url
        self.success = True
        self.error_message = ""


class _StubCrawler:
    """Stand-in for AsyncWebCrawler that returns or raises on demand."""

    def __init__(self, behavior) -> None:
        self.behavior = behavior
        self.calls = 0

    async def arun(self, url: str, config=None):
        self.calls += 1
        return await self.behavior(url)


def test_crawl_failure_classification() -> None:
    """A timing-out or raising page must become a fetch_failed entry, never an aborted batch."""
    print("\nTesting crawl_one() failure classification (timeout, exception, unsuccessful result)...")
    import asyncio

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)

        async def timeout_behavior(url):
            raise asyncio.TimeoutError()

        async def raise_behavior(url):
            raise RuntimeError("browser crashed")

        async def failure_behavior(url):
            result = _FakeResult()
            result.success = False
            result.error_message = "net::ERR_NAME_NOT_RESOLVED"
            return result

        async def none_behavior(url):
            return None

        cases = {
            "timeout": (timeout_behavior, "timed out"),
            "exception": (raise_behavior, "RuntimeError: browser crashed"),
            "unsuccessful": (failure_behavior, "net::ERR_NAME_NOT_RESOLVED"),
            "none result": (none_behavior, "without an error message"),        }
        for label, (behavior, expected_fragment) in cases.items():
            crawler = _StubCrawler(behavior)
            entry = asyncio.run(batch_crawl.crawl_one(0, "https://x.example/", crawler, None, out_dir))
            assert entry["status"] == batch_crawl.STATUS_FETCH_FAILED, f"{label}: got {entry['status']}"
            assert expected_fragment in entry["error"], f"{label}: reason was {entry['error']!r}"
            assert entry["file"] is None
            assert entry["index"] == 0 and entry["url"] == "https://x.example/"

        # A successful crawl writes its markdown and records the path.
        async def ok_behavior(url):
            return _FakeResult(html="<p>hi</p>", url=url)

        crawler = _StubCrawler(ok_behavior)
        entry = asyncio.run(batch_crawl.crawl_one(2, "https://ok.example/page", crawler, None, out_dir))
        assert entry["status"] == batch_crawl.STATUS_SUCCESS
        assert entry["file"] and Path(entry["file"]).exists()
        assert Path(entry["file"]).name.startswith("002_"), "file name keeps the input index for traceability"

        # An unwritable output location is write_failed, not fetch_failed: the
        # crawl succeeded, only the disk write did not.
        not_a_dir = out_dir / "is_a_file"
        not_a_dir.write_text("x", encoding="utf-8")
        entry = asyncio.run(batch_crawl.crawl_one(3, "https://ok.example/other", crawler, None, not_a_dir))
        assert entry["status"] == batch_crawl.STATUS_WRITE_FAILED, entry
        assert entry["file"] is None
        assert "could not write" in entry["error"], entry["error"]
    print("✅ crawl_one separates timeout / exception / unsuccessful / success / write_failed paths")


class _StubStrategy:
    def __init__(self, behavior) -> None:
        self.behavior = behavior

    async def arun(self, url, sections):
        return self.behavior()


def test_extract_failure_classification() -> None:
    """extract_one() must report bad extraction output as extract_failed, not fetch_failed."""
    import asyncio

    print("\nTesting extract_failed classification for empty / invalid / raising extractors...")
    with tempfile.TemporaryDirectory() as tmp:
        pages_dir = Path(tmp)
        schema = {"name": "products"}

        cases = {
            "empty list": _StubStrategy(lambda: []),
            "invalid json": _StubStrategy(lambda: "{not valid json"),
            "raises": _StubStrategy(lambda: (_ for _ in ()).throw(ValueError("bad selector"))),
            "empty dict": _StubStrategy(lambda: {}),
        }
        for label, strategy in cases.items():
            entry, record = asyncio.run(
                batch_extract.extract_one(0, "raw:fake", _FakeResult(), strategy, schema, pages_dir)
            )
            assert record is None, f"{label}: no record should be kept"
            assert entry["status"] == batch_extract.STATUS_EXTRACT_FAILED, f"{label}: got {entry['status']}"
            assert entry["error"], f"{label}: failure reason must be recorded"
            assert entry["file"] is None

        ok = _StubStrategy(lambda: [{"title": "A"}])
        entry, record = asyncio.run(batch_extract.extract_one(1, "raw:ok", _FakeResult(), ok, schema, pages_dir))
        assert entry["status"] == batch_extract.STATUS_SUCCESS
        assert entry["item_count"] == 1
        assert record == {"url": "raw:ok", "data": [{"title": "A"}]}
        assert Path(entry["file"]).exists()
    print("✅ extract_failed covers empty, invalid JSON, raising, and empty-object extraction")


def test_load_urls_skips_comments_and_blanks() -> None:
    print("\nTesting URL list parsing (comments and blank lines)...")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "urls.txt"
        path.write_text("# leading comment\n\nhttps://a.example/one\n   \n#indented comment\nhttps://b.example/two\n")
        urls = batch_crawl.load_urls(str(path))
        assert urls == ["https://a.example/one", "https://b.example/two"], urls
        assert batch_extract.load_urls(str(path)) == urls, "both scripts must parse the list the same way"
        assert batch_crawl.load_urls("https://x.example/1, https://y.example/2") == [
            "https://x.example/1",
            "https://y.example/2",
        ]
        # An inline raw: document is one URL even when the markup contains commas.
        inline = "raw:<html><body><p>a, b, c</p></body></html>"
        assert batch_crawl.load_urls(inline) == [inline]
        assert batch_extract.load_urls(inline) == [inline]
    print("✅ comments, blank lines, comma-separated input, and inline raw: behave as before")


def test_unexpected_worker_error_still_recorded() -> None:
    """A per-URL error escaping the normal handlers must still yield one entry per URL."""
    import asyncio

    print("\nTesting that a worker-level exception still produces a full summary...")
    original = batch_crawl.crawl_one

    async def exploding_crawl_one(index, url, *args, **kwargs):
        if url == "https://boom.example/":
            raise RuntimeError("unexpected worker failure")
        return await original(index, url, *args, **kwargs)

    batch_crawl.crawl_one = exploding_crawl_one
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "out"

            class _CM:
                async def __aenter__(self):
                    async def ok(url):
                        return _FakeResult(html="<p>ok</p>", url=url)

                    return _StubCrawler(ok)

                async def __aexit__(self, *exc):
                    return False

            original_crawler = batch_crawl.AsyncWebCrawler
            batch_crawl.AsyncWebCrawler = lambda *a, **k: _CM()
            try:
                urls = ["https://boom.example/", "https://fine.example/"]
                summary = asyncio.run(batch_crawl.batch_crawl(urls, 2, out_dir))
            finally:
                batch_crawl.AsyncWebCrawler = original_crawler

            assert summary["total"] == 2
            assert [e["url"] for e in summary["results"]] == urls
            boom, fine = summary["results"]
            assert boom["status"] == batch_crawl.STATUS_FETCH_FAILED
            assert "unexpected worker failure" in boom["error"], boom["error"]
            assert fine["status"] == batch_crawl.STATUS_SUCCESS
            assert Path(fine["file"]).exists(), "the healthy page must still be written"
    finally:
        batch_crawl.crawl_one = original
    print("✅ an escaping worker error is recorded per URL, batch still completes")


def main() -> int:
    test_crawl_failure_classification()
    test_extract_failure_classification()
    test_load_urls_skips_comments_and_blanks()
    test_unexpected_worker_error_still_recorded()
    test_batch_crawl_mixed_outcomes()
    test_batch_crawl_total_failure()
    test_batch_extract_mixed_outcomes()
    test_batch_extract_total_failure()
    print("\n✅ All batch script tests passed - every URL got exactly one recorded outcome.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
