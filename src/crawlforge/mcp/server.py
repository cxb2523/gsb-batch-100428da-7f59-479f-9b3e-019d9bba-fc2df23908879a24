"""crawlforge MCP server — exposes crawls to Claude/Codex/Cursor.

Tools:
- crawl_run(url, max_urls, max_depth) -> path of saved DuckDB
- list_crawls() -> list of recent crawls
- query_urls(db_path, sql) -> rows
- list_issues(db_path, severity?, rule_id?, limit) -> issues
- find_redirect_chains(db_path, min_hops) -> chains
- find_orphans(db_path) -> orphan URLs
- audit_summary(db_path) -> condensed audit report
- list_rules() -> all 269 rules with metadata

Run: python -m crawlforge.mcp
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import duckdb
from mcp.server.fastmcp import FastMCP

from crawlforge.engine.crawler import CrawlConfig
from crawlforge.engine.runner import run_crawl_sync
from crawlforge.rules._registry import discover_rules

mcp = FastMCP("crawlforge")

DEFAULT_CRAWL_DIR = Path(os.getenv("CRAWLFORGE_CRAWL_DIR", str(Path.home() / ".crawlforge" / "crawls")))
DEFAULT_CRAWL_DIR.mkdir(parents=True, exist_ok=True)


def _resolve_db_path(db_path: str) -> Path:
    p = Path(db_path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"Crawl database not found: {p}")
    return p


def _connect_readonly(db_path: str) -> duckdb.DuckDBPyConnection:
    p = _resolve_db_path(db_path)
    return duckdb.connect(str(p), read_only=True)


@mcp.tool()
def crawl_run(
    url: str,
    max_urls: int = 500,
    max_depth: int = 10,
    concurrency: int = 8,
    same_host: bool = True,
    persist_html: bool = False,
) -> str:
    """Crawl a website and save results to DuckDB.

    Returns the path of the saved .duckdb file. Use list_issues, query_urls
    and audit_summary against that path to inspect results.
    """
    safe_label = url.replace("https://", "").replace("http://", "").split("/")[0]
    safe_label = "".join(c if c.isalnum() or c in "-." else "-" for c in safe_label)
    from datetime import datetime
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    db_path = DEFAULT_CRAWL_DIR / f"{safe_label}-{stamp}.duckdb"

    config = CrawlConfig(
        start_url=url,
        max_urls=max_urls,
        max_depth=max_depth,
        concurrency=concurrency,
        same_host_only=same_host,
        persist_html=persist_html,
    )
    _, summary = run_crawl_sync(config, db_path=db_path)

    return json.dumps({
        "db_path": str(db_path),
        **summary,
    }, indent=2, default=str)


@mcp.tool()
def list_crawls() -> str:
    """List recent crawls saved by crawlforge in the default crawl dir."""
    crawls = []
    for f in sorted(DEFAULT_CRAWL_DIR.glob("*.duckdb"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            con = duckdb.connect(str(f), read_only=True)
            meta = dict(con.execute("SELECT key, value FROM crawl_meta").fetchall())
            con.close()
            crawls.append({
                "path": str(f),
                "size_mb": round(f.stat().st_size / 1024 / 1024, 2),
                "modified": f.stat().st_mtime,
                "meta": meta,
            })
        except Exception:
            continue
    return json.dumps(crawls, indent=2, default=str)


@mcp.tool()
def query_urls(db_path: str, sql: str, limit: int = 100) -> str:
    """Run a read-only SQL query against a crawl database.

    Schema reference: tables `urls`, `links`, `redirects`, `hreflang`,
    `structured_data`, `resources`, `issues`, `crawl_meta`. See docs/SCHEMA.md
    for column details.
    """
    if any(kw in sql.upper() for kw in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ", "CREATE ", "ATTACH ")):
        return "ERROR: only read-only queries allowed"
    con = _connect_readonly(db_path)
    try:
        bounded = sql if "limit" in sql.lower() else f"{sql} LIMIT {limit}"
        rows = con.execute(bounded).fetchall()
        cols = [d[0] for d in con.description] if con.description else []
        return json.dumps({
            "columns": cols,
            "rows": [list(r) for r in rows],
            "count": len(rows),
        }, indent=2, default=str)
    finally:
        con.close()


@mcp.tool()
def list_issues(
    db_path: str,
    severity: str | None = None,
    rule_id: str | None = None,
    category: str | None = None,
    limit: int = 50,
) -> str:
    """List issues from a crawl, optionally filtered by severity/rule_id/category."""
    con = _connect_readonly(db_path)
    try:
        conditions = []
        params: list = []
        if severity:
            conditions.append("severity = ?")
            params.append(severity)
        if rule_id:
            conditions.append("rule_id = ?")
            params.append(rule_id)
        if category:
            conditions.append("category = ?")
            params.append(category)
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        sql = f"""
            SELECT i.rule_id, i.severity, i.category, i.message, u.url, i.evidence
            FROM issues i
            LEFT JOIN urls u ON u.url_id = i.url_id
            {where}
            ORDER BY CASE i.severity
                WHEN 'critical' THEN 0
                WHEN 'warning' THEN 1
                WHEN 'info' THEN 2
            END, i.rule_id
            LIMIT {limit}
        """
        rows = con.execute(sql, params).fetchall()
        return json.dumps([
            {
                "rule_id": r[0],
                "severity": r[1],
                "category": r[2],
                "message": r[3],
                "url": r[4],
                "evidence": json.loads(r[5]) if r[5] else {},
            }
            for r in rows
        ], indent=2, default=str)
    finally:
        con.close()


@mcp.tool()
def find_redirect_chains(db_path: str, min_hops: int = 1) -> str:
    """Find redirect chains with at least min_hops hops."""
    con = _connect_readonly(db_path)
    try:
        rows = con.execute(
            """
            SELECT chain_id,
                   list(from_url ORDER BY hop) AS chain,
                   max(hop) AS hops,
                   bool_or(is_loop) AS has_loop
            FROM redirects
            GROUP BY chain_id
            HAVING max(hop) >= ?
            ORDER BY hops DESC
            """,
            [min_hops],
        ).fetchall()
        return json.dumps([
            {"chain_id": r[0], "chain": list(r[1]), "hops": r[2], "loop": r[3]}
            for r in rows
        ], indent=2, default=str)
    finally:
        con.close()


@mcp.tool()
def find_orphans(db_path: str) -> str:
    """Find URLs that have no internal incoming links (orphan pages)."""
    con = _connect_readonly(db_path)
    try:
        rows = con.execute(
            """
            SELECT u.url, u.status_code, u.is_indexable
            FROM urls u
            LEFT JOIN links l ON l.target_url_id = u.url_id
            WHERE l.target_url_id IS NULL AND COALESCE(u.depth, 0) > 0
            ORDER BY u.url
            """
        ).fetchall()
        return json.dumps([
            {"url": r[0], "status": r[1], "indexable": r[2]} for r in rows
        ], indent=2, default=str)
    finally:
        con.close()


@mcp.tool()
def audit_summary(db_path: str) -> str:
    """Return a condensed SEO audit summary: counts by severity, top rules, indexability."""
    con = _connect_readonly(db_path)
    try:
        meta = dict(con.execute("SELECT key, value FROM crawl_meta").fetchall())
        url_stats = con.execute("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE status_code = 200) AS ok,
                COUNT(*) FILTER (WHERE status_code BETWEEN 300 AND 399) AS redirects,
                COUNT(*) FILTER (WHERE status_code BETWEEN 400 AND 499) AS errors_4xx,
                COUNT(*) FILTER (WHERE status_code BETWEEN 500 AND 599) AS errors_5xx,
                COUNT(*) FILTER (WHERE is_indexable = TRUE) AS indexable,
                COUNT(*) FILTER (WHERE from_robots = TRUE) AS blocked_robots
            FROM urls
        """).fetchone()
        sev = dict(con.execute(
            "SELECT severity, COUNT(*) FROM issues GROUP BY severity"
        ).fetchall())
        top_rules = con.execute(
            "SELECT rule_id, severity, COUNT(*) AS n FROM issues "
            "GROUP BY rule_id, severity ORDER BY n DESC LIMIT 20"
        ).fetchall()
        top_categories = dict(con.execute(
            "SELECT category, COUNT(*) FROM issues GROUP BY category ORDER BY 2 DESC"
        ).fetchall())
        return json.dumps({
            "meta": meta,
            "urls": {
                "total": url_stats[0],
                "200": url_stats[1],
                "3xx": url_stats[2],
                "4xx": url_stats[3],
                "5xx": url_stats[4],
                "indexable": url_stats[5],
                "blocked_by_robots": url_stats[6],
            },
            "issues_by_severity": sev,
            "issues_by_category": top_categories,
            "top_rules": [
                {"rule_id": r[0], "severity": r[1], "count": r[2]} for r in top_rules
            ],
        }, indent=2, default=str)
    finally:
        con.close()


@mcp.tool()
def list_rules(category: str | None = None, severity: str | None = None) -> str:
    """List all 269 SEO rules implemented in crawlforge with their metadata."""
    rules = discover_rules()
    out = []
    for rule_cls in sorted(rules.values(), key=lambda c: (c.category, c.id)):
        if category and rule_cls.category != category:
            continue
        if severity and rule_cls.severity != severity:
            continue
        out.append(rule_cls.metadata())
    return json.dumps({"count": len(out), "rules": out}, indent=2, default=str)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
