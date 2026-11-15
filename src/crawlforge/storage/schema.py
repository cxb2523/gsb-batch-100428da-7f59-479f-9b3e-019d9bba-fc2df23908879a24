"""DuckDB schema — the contract that all 300 rules consume.

See `docs/SCHEMA.md` for column-by-column documentation.

CRITICAL: any change here is a breaking change for Phase B rules.
Bump `SCHEMA_VERSION` and write a migration in `storage/migrations/`.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
from duckdb import DuckDBPyConnection

SCHEMA_VERSION = "1.0.0"

DDL: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS urls (
        url_id            UBIGINT PRIMARY KEY,
        url               VARCHAR NOT NULL,
        final_url         VARCHAR,
        status_code       INTEGER,
        redirect_count    INTEGER DEFAULT 0,
        content_type      VARCHAR,
        content_length    BIGINT,
        response_time_ms  INTEGER,
        depth             INTEGER,
        crawled_at        TIMESTAMP,
        from_sitemap      BOOLEAN DEFAULT FALSE,
        from_robots       BOOLEAN DEFAULT FALSE,
        rendered          BOOLEAN DEFAULT FALSE,

        title             VARCHAR,
        meta_description  VARCHAR,
        meta_robots       VARCHAR,
        canonical         VARCHAR,
        viewport          VARCHAR,
        charset           VARCHAR,
        lang              VARCHAR,

        h1                VARCHAR[],
        h2                VARCHAR[],
        h3                VARCHAR[],
        h4                VARCHAR[],
        h5                VARCHAR[],
        h6                VARCHAR[],

        is_indexable      BOOLEAN,
        indexability_reason VARCHAR,
        x_robots_tag      VARCHAR,

        word_count        INTEGER,
        text_to_html_ratio DOUBLE,
        content_hash      VARCHAR,
        simhash           UBIGINT,

        lcp_ms            INTEGER,
        cls               DOUBLE,
        inp_ms            INTEGER,

        header_csp        VARCHAR,
        header_hsts       VARCHAR,
        header_x_frame_options VARCHAR,
        header_referrer_policy VARCHAR,

        raw_html          BLOB,
        rendered_html     BLOB
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS links (
        source_url_id     UBIGINT NOT NULL,
        target_url        VARCHAR NOT NULL,
        target_url_id     UBIGINT,
        anchor            VARCHAR,
        rel               VARCHAR,
        link_type         VARCHAR,
        in_navigation     BOOLEAN,
        in_footer         BOOLEAN,
        position          INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS redirects (
        chain_id          UBIGINT NOT NULL,
        from_url          VARCHAR NOT NULL,
        to_url            VARCHAR NOT NULL,
        status_code       INTEGER,
        hop               INTEGER,
        is_loop           BOOLEAN DEFAULT FALSE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hreflang (
        source_url_id     UBIGINT NOT NULL,
        lang              VARCHAR NOT NULL,
        href              VARCHAR NOT NULL,
        href_url_id       UBIGINT,
        from_html         BOOLEAN DEFAULT FALSE,
        from_header       BOOLEAN DEFAULT FALSE,
        from_sitemap      BOOLEAN DEFAULT FALSE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS structured_data (
        url_id            UBIGINT NOT NULL,
        syntax            VARCHAR NOT NULL,
        schema_type       VARCHAR,
        data              JSON,
        valid             BOOLEAN,
        validation_errors VARCHAR[]
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS resources (
        source_url_id     UBIGINT NOT NULL,
        resource_url      VARCHAR NOT NULL,
        resource_type     VARCHAR NOT NULL,
        status            INTEGER,
        size_bytes        BIGINT,
        alt               VARCHAR,
        width             INTEGER,
        height            INTEGER,
        loading_attr      VARCHAR,
        format            VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS issues (
        issue_id          UBIGINT PRIMARY KEY,
        rule_id           VARCHAR NOT NULL,
        url_id            UBIGINT,
        severity          VARCHAR NOT NULL,
        category          VARCHAR NOT NULL,
        evidence          JSON,
        message           VARCHAR,
        detected_at       TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS crawl_meta (
        key               VARCHAR PRIMARY KEY,
        value             VARCHAR
    )
    """,
]

INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_urls_status ON urls(status_code)",
    "CREATE INDEX IF NOT EXISTS idx_urls_indexable ON urls(is_indexable)",
    "CREATE INDEX IF NOT EXISTS idx_links_source ON links(source_url_id)",
    "CREATE INDEX IF NOT EXISTS idx_links_target ON links(target_url_id)",
    "CREATE INDEX IF NOT EXISTS idx_redirects_chain ON redirects(chain_id)",
    "CREATE INDEX IF NOT EXISTS idx_hreflang_source ON hreflang(source_url_id)",
    "CREATE INDEX IF NOT EXISTS idx_hreflang_href ON hreflang(href_url_id)",
    "CREATE INDEX IF NOT EXISTS idx_structured_url ON structured_data(url_id)",
    "CREATE INDEX IF NOT EXISTS idx_resources_source ON resources(source_url_id)",
    "CREATE INDEX IF NOT EXISTS idx_issues_rule ON issues(rule_id)",
    "CREATE INDEX IF NOT EXISTS idx_issues_url ON issues(url_id)",
    "CREATE INDEX IF NOT EXISTS idx_issues_severity ON issues(severity)",
]


def init_database(path: str | Path | None = None) -> DuckDBPyConnection:
    """Open a DuckDB connection and ensure schema exists.

    `path=None` opens an in-memory database (useful for tests).
    """
    target = ":memory:" if path is None else str(path)
    con = duckdb.connect(target)
    for stmt in DDL:
        con.execute(stmt)
    for stmt in INDEXES:
        con.execute(stmt)
    con.execute(
        "INSERT OR REPLACE INTO crawl_meta VALUES ('schema_version', ?)",
        [SCHEMA_VERSION],
    )
    return con
