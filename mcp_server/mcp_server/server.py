import json
import uuid
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP

from mcp_server import db
from mcp_server.config import Config

mcp = FastMCP(
    "Phantombot",
    instructions=(
        "Open-source web crawler for the AI agentic era. "
        "Crawl URLs, extract entities, detect schemas, "
        "and get clean markdown content."
    ),
)


@mcp.tool()
def crawl_url(url: str, max_depth: int = 3) -> dict:
    """Crawl a web page and extract its content as clean markdown.

    Returns a job ID for tracking. The crawled content will be
    available via query_knowledge and search_semantic once processed.

    Args:
        url: The URL to crawl (http or https)
        max_depth: How many links deep to follow (1-10, default 3)
    """
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return {"error": "Invalid URL: must include scheme and host"}
    if parsed.scheme not in ("http", "https"):
        return {"error": "Invalid URL: only http and https are supported"}

    max_depth = max(1, min(max_depth, 10))
    job_id = str(uuid.uuid4())

    pg = db.get_pg_connection()
    with pg.cursor() as cur:
        cur.execute(
            "INSERT INTO crawl_jobs (id, url, status) VALUES (%s, %s, 'pending') RETURNING id",
            (job_id, url),
        )
        cur.fetchone()

    rdb = db.get_redis_client()
    rdb.xadd(
        "phantombot:crawl_requests",
        {
            "url": url,
            "max_depth": str(max_depth),
            "job_id": job_id,
        },
    )

    return {"job_id": job_id, "url": url, "status": "pending"}


@mcp.tool()
def query_knowledge(query: str, limit: int = 10) -> dict:
    """Search Phantombot's knowledge base for entities matching a text query.

    Returns named entities (people, organizations, locations, products, etc.)
    extracted from crawled pages, with source URLs and confidence scores.

    Args:
        query: Text to search for in extracted entities
        limit: Maximum results to return (1-50, default 10)
    """
    limit = max(1, min(limit, 50))

    pg = db.get_pg_connection()
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT e.entity_text, e.entity_type, e.confidence,
                   p.url as source_url, p.fetched_at
            FROM entities e
            JOIN crawled_pages p ON e.page_id = p.id
            WHERE e.entity_text ILIKE %s
            ORDER BY e.confidence DESC
            LIMIT %s
            """,
            (f"%{query}%", limit),
        )
        columns = [desc[0] for desc in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]

    # Serialize datetime values
    for row in rows:
        for key, val in row.items():
            if hasattr(val, "isoformat"):
                row[key] = val.isoformat()

    return {"results": rows, "total": len(rows)}


@mcp.tool()
def search_semantic(query: str, limit: int = 10) -> dict:
    """Perform semantic (vector similarity) search across Phantombot's knowledge base.

    Uses sentence embeddings to find conceptually related entities and content,
    even if the exact words don't match.

    Args:
        query: Natural language query for semantic search
        limit: Maximum results to return (1-50, default 10)
    """
    limit = max(1, min(limit, 50))

    embedder = db.get_embedder()
    qdrant = db.get_qdrant_client()

    embedding = embedder.encode(query).tolist()
    results = qdrant.query_points(
        collection_name=Config.QDRANT_COLLECTION,
        query=embedding,
        limit=limit,
    )

    data = [
        {
            "id": point.id,
            "score": point.score,
            **point.payload,
        }
        for point in results.points
    ]

    return {"results": data, "total": len(data)}


@mcp.tool()
def get_entity(entity_id: str) -> dict:
    """Retrieve detailed information about a specific entity by its ID.

    Use this after finding entity IDs from query_knowledge or search_semantic.

    Args:
        entity_id: UUID of the entity to look up
    """
    pg = db.get_pg_connection()
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT e.id, e.entity_text, e.entity_type, e.confidence,
                   p.url as source_url, p.fetched_at
            FROM entities e
            JOIN crawled_pages p ON e.page_id = p.id
            WHERE e.id = %s
            """,
            (entity_id,),
        )
        columns = [desc[0] for desc in cur.description]
        row = cur.fetchone()

    if row is None:
        return {"error": f"Entity not found: {entity_id}"}

    data = dict(zip(columns, row))
    for key, val in data.items():
        if hasattr(val, "isoformat"):
            data[key] = val.isoformat()

    return data


@mcp.tool()
def get_page_markdown(page_id: str) -> dict:
    """Get the clean markdown content of a previously crawled page.

    Returns LLM-ready markdown with navigation, ads, and boilerplate removed.
    Use this to read the full content of a page found via search.

    Args:
        page_id: UUID of the crawled page
    """
    pg = db.get_pg_connection()
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT id, url, markdown, markdown_meta, fetched_at
            FROM crawled_pages
            WHERE id = %s
            """,
            (page_id,),
        )
        columns = [desc[0] for desc in cur.description]
        row = cur.fetchone()

    if row is None:
        return {"error": f"Page not found: {page_id}"}

    data = dict(zip(columns, row))

    if data.get("markdown") is None:
        return {"error": f"Markdown not available for page: {page_id}"}

    return {
        "url": data["url"],
        "markdown": data["markdown"],
        "meta": data.get("markdown_meta") or {},
    }


@mcp.tool()
def scrape_url(url: str, include_entities: bool = True) -> dict:
    """Scrape a single URL synchronously and return markdown + entities.

    Unlike crawl_url (which is async), this fetches the page immediately
    and returns clean markdown content, extracted entities, and detected schemas
    in a single response. Best for when you need content right now.

    Args:
        url: The URL to scrape (http or https)
        include_entities: Whether to extract named entities (default True)
    """
    import requests as http_requests
    import sys
    import pathlib

    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return {"error": "Invalid URL: must include scheme and host"}

    try:
        resp = http_requests.get(
            url,
            timeout=30,
            headers={
                "User-Agent": "Phantombot/1.0",
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
    except Exception as e:
        return {"error": f"Fetch failed: {str(e)}"}

    if resp.status_code >= 400:
        return {"error": f"HTTP {resp.status_code}"}

    html = resp.text
    result = {"url": url, "status_code": resp.status_code}

    # Extract markdown
    try:
        intel_dir = str(pathlib.Path(__file__).parent.parent.parent / "intelligence")
        if intel_dir not in sys.path:
            sys.path.insert(0, intel_dir)
        from intelligence.pipelines.markdown import MarkdownPipeline
        md_results = MarkdownPipeline().extract(html, url)
        if md_results:
            result["markdown"] = md_results[0].markdown
            result["word_count"] = md_results[0].word_count
    except Exception:
        result["markdown"] = None

    # Extract entities
    if include_entities:
        try:
            from intelligence.pipelines.entity import EntityPipeline
            from intelligence.pipelines.entity_resolver import EntityResolver
            entities = EntityPipeline().extract(html, url)
            entities = EntityResolver().resolve(entities)
            result["entities"] = [
                {"text": e.entity_text, "type": e.entity_type, "confidence": e.confidence}
                for e in entities[:50]  # Cap at 50 for context window
            ]
        except Exception:
            result["entities"] = []

    return result


@mcp.tool()
def discover_urls(url: str, search: str | None = None, limit: int = 50) -> dict:
    """Discover known URLs for a domain from Phantombot's crawl history.

    Returns URLs that have already been crawled for the given domain.
    Use this to see what data is available before querying.

    Args:
        url: Domain or URL to discover pages for
        search: Optional keyword to filter URLs
        limit: Maximum URLs to return (1-500, default 50)
    """
    parsed = urlparse(url)
    domain = parsed.netloc or parsed.path.split("/")[0]
    if not domain:
        return {"error": "Could not extract domain"}

    limit = max(1, min(limit, 500))
    pg = db.get_pg_connection()
    with pg.cursor() as cur:
        if search:
            cur.execute(
                "SELECT url, fetched_at FROM crawled_pages WHERE domain = %s AND url ILIKE %s ORDER BY fetched_at DESC LIMIT %s",
                (domain, f"%{search}%", limit),
            )
        else:
            cur.execute(
                "SELECT url, fetched_at FROM crawled_pages WHERE domain = %s ORDER BY fetched_at DESC LIMIT %s",
                (domain, limit),
            )
        rows = [{"url": row[0], "fetched_at": row[1].isoformat() if row[1] else None} for row in cur.fetchall()]

    return {"domain": domain, "urls": rows, "total": len(rows)}


@mcp.resource("phantombot://stats")
def get_stats() -> str:
    """Current crawl statistics for the Phantombot instance."""
    pg = db.get_pg_connection()
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM crawled_pages) AS total_pages,
                (SELECT COUNT(*) FROM entities) AS total_entities,
                (SELECT COUNT(*) FROM schemas) AS total_schemas,
                (SELECT COUNT(*) FROM crawled_pages WHERE markdown IS NOT NULL) AS pages_with_markdown,
                (SELECT COUNT(DISTINCT domain) FROM crawled_pages) AS domains_crawled,
                (SELECT MAX(fetched_at) FROM crawled_pages) AS last_crawl_at
            """
        )
        row = cur.fetchone()

    return json.dumps({
        "total_pages": row[0],
        "total_entities": row[1],
        "total_schemas": row[2],
        "pages_with_markdown": row[3],
        "domains_crawled": row[4],
        "last_crawl_at": row[5].isoformat() if row[5] else None,
    })


@mcp.resource("phantombot://recent")
def get_recent() -> str:
    """The 20 most recently crawled pages."""
    pg = db.get_pg_connection()
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT
                p.id,
                p.url,
                p.domain,
                p.fetched_at,
                (p.markdown IS NOT NULL) AS has_markdown,
                (SELECT COUNT(*) FROM entities e WHERE e.page_id = p.id) AS entity_count,
                (SELECT COUNT(*) FROM schemas s WHERE s.page_id = p.id) AS schema_count
            FROM crawled_pages p
            ORDER BY p.fetched_at DESC
            LIMIT 20
            """
        )
        columns = [desc[0] for desc in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]

    # Serialize values
    for row in rows:
        for key, val in row.items():
            if hasattr(val, "isoformat"):
                row[key] = val.isoformat()
            elif isinstance(val, (bytes, memoryview)):
                row[key] = str(val)
        row["id"] = str(row["id"])

    return json.dumps({"pages": rows})


def main():
    """Entry point for the MCP server."""
    if Config.MCP_TRANSPORT == "streamable-http":
        # Configure host/port via settings before running
        mcp.settings.host = Config.MCP_HTTP_HOST
        mcp.settings.port = Config.MCP_HTTP_PORT
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
