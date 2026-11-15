"""Persist crawl rows to DuckDB."""

from __future__ import annotations

from typing import Any

from duckdb import DuckDBPyConnection

from crawlforge.engine.normalize import url_id

URL_COLUMNS = [
    "url_id", "url", "final_url", "status_code", "redirect_count",
    "content_type", "content_length", "response_time_ms", "depth",
    "crawled_at", "from_sitemap", "from_robots", "rendered",
    "title", "meta_description", "meta_robots", "canonical", "viewport",
    "charset", "lang", "h1", "h2", "h3", "h4", "h5", "h6",
    "is_indexable", "indexability_reason", "x_robots_tag",
    "word_count", "text_to_html_ratio", "content_hash", "simhash",
    "lcp_ms", "cls", "inp_ms",
    "header_csp", "header_hsts", "header_x_frame_options", "header_referrer_policy",
    "raw_html", "rendered_html",
]


def write_url_row(con: DuckDBPyConnection, row: dict[str, Any]) -> None:
    """Insert (or upsert) one URL row + its links/hreflang/resources."""
    values = [row.get(col) for col in URL_COLUMNS]
    placeholders = ",".join(["?"] * len(URL_COLUMNS))
    con.execute(
        f"INSERT OR REPLACE INTO urls ({','.join(URL_COLUMNS)}) VALUES ({placeholders})",
        values,
    )

    source_id = row["url_id"]

    for link in row.get("_links", []):
        con.execute(
            """
            INSERT INTO links (
                source_url_id, target_url, target_url_id, anchor, rel,
                link_type, in_navigation, in_footer, position
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                source_id,
                link["target_url"],
                url_id(link["target_url"]),
                link.get("anchor"),
                link.get("rel"),
                link.get("link_type"),
                link.get("in_navigation"),
                link.get("in_footer"),
                link.get("position"),
            ],
        )

    for hl in row.get("_hreflang", []):
        con.execute(
            """
            INSERT INTO hreflang (
                source_url_id, lang, href, href_url_id,
                from_html, from_header, from_sitemap
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                source_id,
                hl["lang"],
                hl["href"],
                url_id(hl["href"]),
                hl.get("from_html", False),
                hl.get("from_header", False),
                hl.get("from_sitemap", False),
            ],
        )

    for res in row.get("_resources", []):
        con.execute(
            """
            INSERT INTO resources (
                source_url_id, resource_url, resource_type, status,
                size_bytes, alt, width, height, loading_attr, format
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                source_id,
                res["resource_url"],
                res["resource_type"],
                res.get("status"),
                res.get("size_bytes"),
                res.get("alt"),
                res.get("width"),
                res.get("height"),
                res.get("loading_attr"),
                res.get("format"),
            ],
        )


def resolve_link_targets(con: DuckDBPyConnection) -> None:
    """After crawl finishes: backfill links.target_url_id and hreflang.href_url_id
    where the target was actually crawled."""
    con.execute(
        """
        UPDATE links AS l
        SET target_url_id = u.url_id
        FROM urls AS u
        WHERE l.target_url = u.url
        """
    )
    con.execute(
        """
        UPDATE hreflang AS h
        SET href_url_id = u.url_id
        FROM urls AS u
        WHERE h.href = u.url
        """
    )


def write_issues(con: DuckDBPyConnection, issues: list[Any]) -> int:
    n = 0
    for issue in issues:
        row = issue.to_row()
        con.execute(
            """
            INSERT OR REPLACE INTO issues (
                issue_id, rule_id, url_id, severity, category,
                evidence, message, detected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row["issue_id"],
                row["rule_id"],
                row["url_id"],
                row["severity"],
                row["category"],
                row["evidence"],
                row["message"],
                row["detected_at"],
            ],
        )
        n += 1
    return n
