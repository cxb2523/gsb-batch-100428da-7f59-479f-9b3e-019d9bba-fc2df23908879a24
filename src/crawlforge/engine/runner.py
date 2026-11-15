"""High-level orchestration: crawl + persist + run rules."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from duckdb import DuckDBPyConnection

from crawlforge.engine.crawler import CrawlConfig, crawl
from crawlforge.rules._registry import discover_rules
from crawlforge.storage.schema import init_database
from crawlforge.storage.writer import resolve_link_targets, write_issues, write_url_row


async def run_crawl(
    config: CrawlConfig,
    db_path: Path | str | None = None,
    progress_callback: Any = None,
) -> tuple[DuckDBPyConnection, dict[str, Any]]:
    """Run a complete crawl: fetch, persist, run rules. Returns (con, summary)."""
    con = init_database(db_path)
    started = datetime.now(timezone.utc)

    n = 0
    n_blocked = 0
    n_errors = 0
    async for row in crawl(config):
        write_url_row(con, row)
        n += 1
        if row.get("from_robots"):
            n_blocked += 1
        elif not row.get("status_code"):
            n_errors += 1
        if progress_callback:
            progress_callback(n, row)

    resolve_link_targets(con)

    rules = discover_rules()
    all_issues: list[Any] = []
    for rule_cls in rules.values():
        rule = rule_cls()
        if rule.requires_rendered_html and not config.persist_html:
            continue
        try:
            issues = list(rule.check(con))
        except Exception:
            continue
        all_issues.extend(issues)

    written = write_issues(con, all_issues)

    finished = datetime.now(timezone.utc)
    summary = {
        "start_url": config.start_url,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_s": (finished - started).total_seconds(),
        "urls_total": n,
        "urls_failed": n_blocked + n_errors,
        "urls_blocked_by_robots": n_blocked,
        "urls_request_errors": n_errors,
        "rules_run": len(rules),
        "issues_total": written,
    }
    con.execute(
        "INSERT OR REPLACE INTO crawl_meta VALUES ('start_url', ?)",
        [config.start_url],
    )
    con.execute(
        "INSERT OR REPLACE INTO crawl_meta VALUES ('urls_total', ?)",
        [str(n)],
    )
    con.execute(
        "INSERT OR REPLACE INTO crawl_meta VALUES ('urls_failed', ?)",
        [str(n_blocked + n_errors)],
    )
    con.execute(
        "INSERT OR REPLACE INTO crawl_meta VALUES ('urls_blocked_by_robots', ?)",
        [str(n_blocked)],
    )
    con.execute(
        "INSERT OR REPLACE INTO crawl_meta VALUES ('urls_request_errors', ?)",
        [str(n_errors)],
    )
    con.execute(
        "INSERT OR REPLACE INTO crawl_meta VALUES ('issues_total', ?)",
        [str(written)],
    )
    return con, summary


def run_crawl_sync(
    config: CrawlConfig,
    db_path: Path | str | None = None,
    progress_callback: Any = None,
) -> tuple[DuckDBPyConnection, dict[str, Any]]:
    return asyncio.run(run_crawl(config, db_path, progress_callback))
