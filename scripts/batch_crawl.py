#!/usr/bin/env -S PYTHONDONTWRITEBYTECODE=1 uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["crawl4ai>=0.8.9"]
# ///
# SPDX-License-Identifier: MIT OR Apache-2.0
"""
Crawl a list of URLs concurrently and dump each as a markdown file. Use when
you have a known URL list and want markdown out fast. For schema-based field
extraction across many URLs, use `batch_extract.py` instead.

A page that times out or fails to parse does not abort the batch: every input
URL gets exactly one entry in the summary, with its outcome (`success`,
`fetch_failed`, or `write_failed`), the failure reason, and the markdown path
when one was written. Markdown from successful pages is always written, even
when other pages in the same batch fail.

Usage:
  ./batch_crawl.py <urls.txt | url1,url2,...> [--max-concurrent N] [--out DIR]

Example:
  ./batch_crawl.py urls.txt --max-concurrent 5 --out batch_markdown

Exit status: 0 when at least one page was crawled and written, 1 otherwise.
The summary is written to <out>/_summary.json either way.
"""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

STATUS_SUCCESS = "success"
STATUS_FETCH_FAILED = "fetch_failed"
STATUS_WRITE_FAILED = "write_failed"


def load_urls(source: str) -> List[str]:
    p = Path(source)
    if p.exists():
        out = []
        for line in p.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                out.append(stripped)
        return out
    # A bare `raw:` source is one inline HTML document; splitting it on commas
    # would tear any page whose markup contains a comma.
    if source.startswith("raw:"):
        return [source]
    return [u.strip() for u in source.split(",") if u.strip()]


def safe_filename(url: str, max_len: int = 100) -> str:
    bare = url.replace("https://", "").replace("http://", "")
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in bare)[:max_len]


def display_url(url: str, limit: int = 120) -> str:
    """Short, single-line label for console output and markdown headers.

    `raw:` inputs carry the whole HTML document, so echoing them verbatim would
    flood the terminal and the generated file. The summary always keeps the
    full input URL.
    """
    if len(url) <= limit:
        return url
    return f"{url[:limit]}... ({len(url)} chars)"


def failure_entry(index: int, url: str, status: str, error: str) -> dict:
    return {
        "index": index,
        "url": url,
        "status": status,
        "error": error,
        "file": None,
        "title": None,
        "final_url": None,
    }


async def crawl_one(index: int, url: str, crawler: AsyncWebCrawler, config: CrawlerRunConfig, out_dir: Path) -> dict:
    """Crawl a single URL and turn any failure into a per-URL summary entry."""
    try:
        result = await crawler.arun(url=url, config=config)
    except asyncio.TimeoutError:
        return failure_entry(index, url, STATUS_FETCH_FAILED, "timed out before the crawl completed")
    except Exception as exc:  # noqa: BLE001 - one bad page must not sink the batch
        return failure_entry(index, url, STATUS_FETCH_FAILED, f"{type(exc).__name__}: {exc}")

    if result is None or not getattr(result, "success", False):
        error = getattr(result, "error_message", None) or "crawl failed without an error message"
        return failure_entry(index, url, STATUS_FETCH_FAILED, str(error))

    metadata = getattr(result, "metadata", None) or {}
    title = metadata.get("title") or display_url(url)
    markdown = str(getattr(result, "markdown", None) or "")
    file_path = out_dir / f"{index:03d}_{safe_filename(url)}.md"

    try:
        file_path.write_text(f"# {title}\n\nURL: {display_url(url)}\n\n{markdown}", encoding="utf-8")
    except OSError as exc:
        return failure_entry(index, url, STATUS_WRITE_FAILED, f"could not write markdown file: {exc}")

    final_url = getattr(result, "redirected_url", None)
    if not final_url and not url.startswith("raw:"):
        final_url = getattr(result, "url", None) or url

    return {
        "index": index,
        "url": url,
        "status": STATUS_SUCCESS,
        "error": None,
        "file": str(file_path),
        "title": str(title),
        "final_url": final_url,
    }


async def batch_crawl(urls: List[str], max_concurrent: int, out_dir: Path) -> dict:
    print(f"Batch crawling {len(urls)} URL(s), max {max_concurrent} concurrent")
    out_dir.mkdir(exist_ok=True, parents=True)

    browser_config = BrowserConfig(headless=True, verbose=False)
    crawler_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        remove_overlay_elements=True,
        wait_until="networkidle",
        page_timeout=30000,
        screenshot=False,
    )

    semaphore = asyncio.Semaphore(max(1, max_concurrent))

    async def worker(index: int, url: str) -> dict:
        async with semaphore:
            return await crawl_one(index, url, crawler, crawler_config, out_dir)

    try:
        async with AsyncWebCrawler(config=browser_config) as crawler:
            raw_entries = await asyncio.gather(
                *(worker(i, url) for i, url in enumerate(urls)), return_exceptions=True
            )
        # A worker that escaped crawl_one's own handling still gets a per-URL entry
        # rather than taking the whole batch down.
        entries = [
            (
                outcome
                if isinstance(outcome, dict)
                else failure_entry(i, url, STATUS_FETCH_FAILED, f"{type(outcome).__name__}: {outcome}")
            )
            for i, (url, outcome) in enumerate(zip(urls, raw_entries))
        ]
    except Exception as exc:  # noqa: BLE001 - browser start-up failure fails every page
        reason = f"crawler could not start: {type(exc).__name__}: {exc}"
        entries = [failure_entry(i, url, STATUS_FETCH_FAILED, reason) for i, url in enumerate(urls)]

    for entry in entries:
        if entry["status"] == STATUS_SUCCESS:
            print(f"OK   {display_url(entry['url'])}")
        else:
            print(f"FAIL {display_url(entry['url'])}: [{entry['status']}] {entry['error']}")

    success = [e for e in entries if e["status"] == STATUS_SUCCESS]
    failed = [e for e in entries if e["status"] != STATUS_SUCCESS]
    status_counts = {STATUS_SUCCESS: 0, STATUS_FETCH_FAILED: 0, STATUS_WRITE_FAILED: 0}
    for entry in entries:
        status_counts[entry["status"]] = status_counts.get(entry["status"], 0) + 1

    summary_path = out_dir / "_summary.json"
    summary = {
        "script": "batch_crawl",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(urls),
        "success_count": len(success),
        "failed_count": len(failed),
        "status_counts": status_counts,
        "out": str(out_dir),
        "summary_file": str(summary_path),
        "results": entries,
        "success": [{"url": e["url"], "title": e["title"], "file": e["file"]} for e in success],
        "failed": [{"url": e["url"], "error": e["error"], "status": e["status"]} for e in failed],
    }
    try:
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        summary["summary_write_error"] = str(exc)
        print(f"FAIL could not write summary {summary_path}: {exc}", file=sys.stderr)

    print(
        f"\nSummary: {len(success)} OK, {len(failed)} failed "
        f"({status_counts[STATUS_FETCH_FAILED]} fetch, {status_counts[STATUS_WRITE_FAILED]} write). "
        f"Markdown in {out_dir}/, summary in {summary_path}"
    )
    return summary


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    urls = load_urls(sys.argv[1])
    if not urls:
        print("FAILED: no URLs found")
        return 1

    max_concurrent = 5
    out_dir = Path("batch_markdown")
    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == "--max-concurrent" and i + 1 < len(args):
            max_concurrent = int(args[i + 1])
            i += 2
        elif args[i] == "--out" and i + 1 < len(args):
            out_dir = Path(args[i + 1])
            i += 2
        else:
            print(f"Unknown arg: {args[i]}")
            return 1

    summary = asyncio.run(batch_crawl(urls, max_concurrent, out_dir))
    return 0 if summary["success_count"] > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
