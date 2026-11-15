#!/usr/bin/env -S PYTHONDONTWRITEBYTECODE=1 uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["crawl4ai>=0.8.9"]
# ///
# SPDX-License-Identifier: MIT OR Apache-2.0
"""
Apply a saved JsonCssExtractionStrategy schema across many URLs concurrently.
For markdown-only batch crawls (no schema), use `batch_crawl.py` instead.

Every input URL gets exactly one entry in the summary. Outcomes are classified
as `success`, `fetch_failed` (the page itself could not be crawled), or
`extract_failed` (the page crawled but the schema matched nothing, matched zero
items, or produced data that does not serialise to JSON). Each entry carries
the failure reason and the output file path when data was written.

Fetching and extraction are separate steps on purpose: the library folds an
extraction-stage exception into `result.success = False`, which would otherwise
make a broken schema look like an unreachable page in the summary.

Per-URL data lands in a `<out-stem>.pages/` directory next to `--out`; the
aggregate list of successful records still lands in `--out` unchanged. The
summary is written to `<out-stem>.summary.json`.

Usage:
  ./batch_extract.py <urls.txt | url1,url2,...> <schema.json> [--max-concurrent N] [--out FILE]

Example:
  ./batch_extract.py shop_urls.txt shop_schema.json --max-concurrent 5 --out products.json

Exit status: 0 when at least one URL yielded extracted items, 1 otherwise.
The summary is written either way.
"""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

from crawl4ai import AsyncWebCrawler, CacheMode, CrawlerRunConfig
from crawl4ai.extraction_strategy import JsonCssExtractionStrategy

STATUS_SUCCESS = "success"
STATUS_FETCH_FAILED = "fetch_failed"
STATUS_EXTRACT_FAILED = "extract_failed"


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
    """Short, single-line label for console output; summary keeps the full URL."""
    if len(url) <= limit:
        return url
    return f"{url[:limit]}... ({len(url)} chars)"


def sidecar_paths(out_file: Path) -> Tuple[Path, Path]:
    """Return (summary_file, per_url_dir) derived from the --out value."""
    stem = out_file.with_suffix("") if out_file.suffix else out_file
    return Path(f"{stem}.summary.json"), Path(f"{stem}.pages")


def items_from(data, schema: dict):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get(schema.get("name", "items"), [])
    return []


def result_entry(index: int, url: str, status: str, error: str | None, file: Path | None, item_count: int = 0) -> dict:
    return {
        "index": index,
        "url": url,
        "status": status,
        "error": error,
        "file": str(file) if file is not None else None,
        "item_count": item_count,
    }


async def fetch_one(
    index: int,
    url: str,
    crawler: AsyncWebCrawler,
    config: CrawlerRunConfig,
) -> Tuple[dict, "object | None"]:
    """Crawl one URL. Returns (summary entry or None, CrawlResult or None).

    A non-None entry means the page itself failed; extraction never ran.
    """
    try:
        result = await crawler.arun(url=url, config=config)
    except asyncio.TimeoutError:
        return result_entry(index, url, STATUS_FETCH_FAILED, "timed out before the crawl completed", None), None
    except Exception as exc:  # noqa: BLE001 - one bad page must not sink the batch
        return result_entry(index, url, STATUS_FETCH_FAILED, f"{type(exc).__name__}: {exc}", None), None

    if result is None or not getattr(result, "success", False):
        error = getattr(result, "error_message", None) or "crawl failed without an error message"
        return result_entry(index, url, STATUS_FETCH_FAILED, str(error), None), None

    return None, result


async def extract_one(
    index: int,
    url: str,
    result,
    strategy: JsonCssExtractionStrategy,
    schema: dict,
    pages_dir: Path,
) -> Tuple[dict, dict | None]:
    """Run the schema against a crawled page. Returns (summary entry, record-or-None)."""
    html = getattr(result, "html", None) or ""
    try:
        # `arun` offloads the synchronous BeautifulSoup pass to a worker thread,
        # so one slow page does not stall the other in-flight pages.
        extracted = await strategy.arun(getattr(result, "url", url) or url, [html])
    except Exception as exc:  # noqa: BLE001 - a bad schema must not sink the batch
        message = f"crawl succeeded but extraction raised {type(exc).__name__}: {exc}"
        return result_entry(index, url, STATUS_EXTRACT_FAILED, message, None), None

    if extracted is None or (isinstance(extracted, (str, list, dict)) and not extracted):
        message = "crawl succeeded but extraction returned no content (schema selectors matched nothing)"
        return result_entry(index, url, STATUS_EXTRACT_FAILED, message, None), None

    if isinstance(extracted, str):
        try:
            data = json.loads(extracted)
        except json.JSONDecodeError as exc:
            message = f"crawl succeeded but extracted content is not valid JSON: {exc}"
            return result_entry(index, url, STATUS_EXTRACT_FAILED, message, None), None
    else:
        data = extracted

    items = items_from(data, schema)
    if not items:
        message = "crawl succeeded but the schema extracted 0 item(s) from this page"
        return result_entry(index, url, STATUS_EXTRACT_FAILED, message, None), None

    file_path = pages_dir / f"{index:03d}_{safe_filename(url)}.json"
    try:
        file_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except (OSError, TypeError) as exc:
        return result_entry(index, url, STATUS_EXTRACT_FAILED, f"could not write extraction file: {exc}", None), None

    return result_entry(index, url, STATUS_SUCCESS, None, file_path, len(items)), {"url": url, "data": data}


async def batch_extract(urls: List[str], schema: dict, max_concurrent: int, out_file: Path) -> dict:
    schema_name = schema.get("name", "items")
    print(f"Batch extracting {len(urls)} URL(s) with schema '{schema_name}', max {max_concurrent} concurrent")

    summary_file, pages_dir = sidecar_paths(out_file)
    pages_dir.mkdir(parents=True, exist_ok=True)
    if out_file.parent and str(out_file.parent) not in ("", "."):
        out_file.parent.mkdir(parents=True, exist_ok=True)

    extraction_strategy = JsonCssExtractionStrategy(schema=schema)
    # Fetch without the strategy attached; extract_one() runs it as a separate
    # async step so an extraction-stage exception is reported as extract_failed
    # rather than as an unreachable page. JsonCssExtractionStrategy forces
    # input_format to "html", so it consumes result.html.
    fetch_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        wait_until="networkidle",
    )

    semaphore = asyncio.Semaphore(max(1, max_concurrent))

    async def worker(index: int, url: str):
        async with semaphore:
            entry, result = await fetch_one(index, url, crawler, fetch_config)
            if entry is not None:
                return entry, None
            return await extract_one(index, url, result, extraction_strategy, schema, pages_dir)

    try:
        async with AsyncWebCrawler() as crawler:
            raw_outcomes = await asyncio.gather(
                *(worker(i, url) for i, url in enumerate(urls)), return_exceptions=True
            )
        # A worker that escaped fetch_one/extract_one's own handling still gets a
        # per-URL entry rather than taking the whole batch down.
        outcomes = [
            (
                outcome
                if isinstance(outcome, tuple)
                else (result_entry(i, url, STATUS_FETCH_FAILED, f"{type(outcome).__name__}: {outcome}", None), None)
            )
            for i, (url, outcome) in enumerate(zip(urls, raw_outcomes))
        ]
    except Exception as exc:  # noqa: BLE001 - browser start-up failure fails every page
        reason = f"crawler could not start: {type(exc).__name__}: {exc}"
        outcomes = [
            (result_entry(i, url, STATUS_FETCH_FAILED, reason, None), None)
            for i, url in enumerate(urls)
        ]

    entries = [entry for entry, _ in outcomes]
    records = [record for _, record in outcomes if record is not None]

    try:
        out_file.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
        aggregate_error = None
    except OSError as exc:
        aggregate_error = f"could not write aggregate output {out_file}: {exc}"

    for entry in entries:
        if entry["status"] == STATUS_SUCCESS:
            print(f"OK   {display_url(entry['url'])} ({entry['item_count']} item(s))")
        else:
            print(f"FAIL {display_url(entry['url'])}: [{entry['status']}] {entry['error']}")
    if aggregate_error:
        print(f"FAIL aggregate output: {aggregate_error}")

    status_counts = {STATUS_SUCCESS: 0, STATUS_FETCH_FAILED: 0, STATUS_EXTRACT_FAILED: 0}
    for entry in entries:
        status_counts[entry["status"]] = status_counts.get(entry["status"], 0) + 1

    summary = {
        "script": "batch_extract",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema": schema_name,
        "total": len(urls),
        "success_count": status_counts[STATUS_SUCCESS],
        "failed_count": len(entries) - status_counts[STATUS_SUCCESS],
        "status_counts": status_counts,
        "out": str(out_file),
        "pages_dir": str(pages_dir),
        "summary_file": str(summary_file),
        "aggregate_error": aggregate_error,
        "results": entries,
    }
    try:
        summary_file.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        summary["summary_write_error"] = str(exc)
        print(f"FAIL could not write summary {summary_file}: {exc}", file=sys.stderr)

    print(
        f"\nSummary: {summary['success_count']} OK, {summary['failed_count']} failed "
        f"({status_counts[STATUS_FETCH_FAILED]} fetch, {status_counts[STATUS_EXTRACT_FAILED]} extract). "
        f"Data in {out_file} and {pages_dir}/, summary in {summary_file}"
    )
    return summary


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1

    urls = load_urls(sys.argv[1])
    if not urls:
        print("FAILED: no URLs found")
        return 1

    schema_path = Path(sys.argv[2])
    if not schema_path.exists():
        print(f"FAILED: schema not found: {schema_path}")
        return 1
    schema = json.loads(schema_path.read_text())

    max_concurrent = 5
    out_file = Path("batch_extracted.json")
    args = sys.argv[3:]
    i = 0
    while i < len(args):
        if args[i] == "--max-concurrent" and i + 1 < len(args):
            max_concurrent = int(args[i + 1])
            i += 2
        elif args[i] == "--out" and i + 1 < len(args):
            out_file = Path(args[i + 1])
            i += 2
        else:
            print(f"Unknown arg: {args[i]}")
            return 1

    summary = asyncio.run(batch_extract(urls, schema, max_concurrent, out_file))
    return 0 if summary["success_count"] > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
