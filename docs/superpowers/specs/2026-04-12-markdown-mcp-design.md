# Markdown Output + MCP Server -- Design Spec

> Convert crawled HTML to clean, token-efficient markdown and expose Phantombot as an MCP server so any AI agent can use it as a tool.

## Problem Statement

Phantombot crawls the web, extracts entities, and detects schemas -- but it stores and serves **raw HTML**. This creates two critical gaps:

1. **No markdown output.** Every competing AI crawler (Firecrawl, Crawl4AI, Jina Reader) converts HTML to clean markdown. Raw HTML wastes 4-5x more LLM tokens than equivalent markdown. AI agents consuming Phantombot data must do their own HTML cleanup, which defeats the purpose of an "AI-era crawler." This is the single most requested capability gap.

2. **No MCP integration.** The Model Context Protocol (MCP) is now the standard way AI agents (Claude, Cursor, Windsurf, OpenAI Agents SDK) discover and use external tools. Phantombot has a FastAPI with `/crawl`, `/query`, `/search`, and `/entity/{id}` -- but no MCP server. Agents cannot discover or invoke Phantombot without custom integration code.

These two features together transform Phantombot from "a crawler with an API" to "a tool any AI agent can pick up and use, getting clean markdown back."

---

## Feature 1: Markdown Output Pipeline

### Why This Matters

- **Token efficiency**: HTML-to-markdown conversion yields ~80% token reduction (benchmarked: a typical blog post goes from ~16,000 tokens in HTML to ~3,000 in markdown).
- **RAG accuracy**: Clean markdown with preserved headings enables precise chunking. Studies show up to 35% improvement in RAG retrieval accuracy with markdown vs. raw HTML.
- **Industry standard**: Firecrawl, Crawl4AI, Jina Reader, and Scrape.do all return markdown. Phantombot without markdown is not competitive.
- **Better embeddings**: The intelligence layer currently strips HTML to plaintext for entity extraction (`EntityPipeline._html_to_text`). Markdown preserves structure (headings, lists, code blocks) that improves both embedding quality and downstream LLM comprehension.

### Library Selection

After evaluating the major Python HTML-to-markdown libraries, the recommendation is a **two-stage pipeline** using **trafilatura** for content extraction and **markdownify** for structural conversion.

| Library | Strengths | Weaknesses | Verdict |
|---|---|---|---|
| **trafilatura** | Best-in-class content extraction (F1=0.958 on ScrapingHub benchmark). Strips nav, ads, boilerplate, cookie banners automatically. Built-in markdown output mode. | Markdown output is functional but not always structurally precise (tables, nested lists). | **Use for content extraction** |
| **markdownify** | Excellent structural fidelity -- headings, tables, code blocks, nested lists. Highly customizable via subclassing. | No content extraction -- converts ALL HTML including nav/ads/boilerplate. | **Use for structural conversion** |
| html2text | Simple, zero dependencies, decent output. | Limited table support, no content extraction, less actively maintained. | Skip |
| html-to-markdown | Type-safe, good HTML5 support. | Newer library, smaller community, Python port of Go library. | Future consideration |
| readability-lxml | Good at finding "main content" like Firefox Reader Mode. | Outputs HTML (not markdown), would need a second pass anyway. | Skip (trafilatura is better) |

**Recommended approach**: Use trafilatura with `output_format="markdown"` as the primary path. This handles the common case (articles, blog posts, documentation) with a single call. For pages where trafilatura's markdown output lacks structural fidelity (detected by heuristics like missing tables/code blocks that exist in the HTML), fall back to: trafilatura content extraction (HTML output) piped through markdownify.

**Why not just markdownify alone?** Because markdownify converts ALL HTML faithfully -- including navigation bars, ad blocks, cookie consent dialogs, and footer link farms. You would get structurally perfect markdown of garbage content. The content extraction step is essential.

### Pipeline Design

A new `MarkdownPipeline` class following the existing `BasePipeline` interface.

**File**: `intelligence/intelligence/pipelines/markdown.py`

```python
from intelligence.pipelines.base import BasePipeline
from intelligence.models import MarkdownResult

class MarkdownPipeline(BasePipeline):
    """Converts raw HTML to clean, token-efficient markdown."""

    def extract(self, html: str, source_url: str) -> list[MarkdownResult]:
        """Extract clean markdown from HTML.

        Strategy:
        1. Use trafilatura to extract main content as markdown
        2. If trafilatura returns empty or low-quality result, fall back
           to trafilatura HTML extraction + markdownify
        3. Post-process: normalize whitespace, fix broken links,
           ensure heading hierarchy
        """
        ...
```

**Pipeline stages**:

1. **Primary extraction** (`trafilatura.extract` with `output_format="markdown"`):
   - Automatically removes nav, ads, boilerplate, scripts, styles
   - Preserves headings, paragraphs, lists, links, emphasis
   - Returns clean markdown or `None` on failure

2. **Fallback extraction** (trafilatura HTML extraction + markdownify):
   - `trafilatura.extract(html, output_format="html")` to get clean content HTML
   - `markdownify.markdownify(clean_html, ...)` for precise structural conversion
   - Configured with: `heading_style="ATX"`, `bullets="-"`, `code_language_callback` for fenced code blocks

3. **Post-processing**:
   - Normalize excessive blank lines (max 2 consecutive)
   - Fix relative URLs to absolute using `source_url`
   - Ensure heading hierarchy starts at `#` or `##`
   - Strip any remaining HTML tags
   - Compute metadata: `word_count`, `has_code`, `has_tables`, `has_images`

4. **Quality check**:
   - If markdown output is less than 50 characters, mark as low-confidence
   - If markdown is more than 95% shorter than the plaintext extraction, something was lost -- flag it
   - Return confidence score based on extraction quality heuristics

**Return type**: The pipeline returns a `list[MarkdownResult]` (always 0 or 1 items) to match the `BasePipeline.extract` interface.

### Storage Changes

**New migration**: `store/migrations/002_add_markdown.sql`

```sql
-- 002_add_markdown.sql
-- Add markdown column to crawled_pages

ALTER TABLE crawled_pages ADD COLUMN IF NOT EXISTS markdown TEXT;
ALTER TABLE crawled_pages ADD COLUMN IF NOT EXISTS markdown_meta JSONB;

-- Index for pages that have markdown (for stats queries)
CREATE INDEX IF NOT EXISTS idx_crawled_pages_has_markdown
    ON crawled_pages ((markdown IS NOT NULL));

COMMENT ON COLUMN crawled_pages.markdown IS
    'Clean markdown extracted from HTML, suitable for LLM consumption';
COMMENT ON COLUMN crawled_pages.markdown_meta IS
    'Metadata about the markdown extraction: word_count, has_code, has_tables, pipeline_version, extracted_at';
```

**`markdown_meta` JSONB structure**:
```json
{
  "word_count": 1250,
  "has_code": true,
  "has_tables": false,
  "has_images": true,
  "pipeline_version": "markdown-v1",
  "extraction_method": "trafilatura_direct",
  "confidence": 0.92,
  "extracted_at": "2026-04-12T10:30:00Z"
}
```

**Changes to `KnowledgeStore`**:
- `save_crawled_page` updated to accept and store `markdown` and `markdown_meta`
- New method `get_page_markdown(page_id) -> str | None` for API use
- The `ON CONFLICT` upsert updated to include the new columns

**Changes to `Worker.process_message`**:
- Add `MarkdownPipeline` alongside `EntityPipeline` and `SchemaPipeline`
- Run markdown extraction first (other pipelines can optionally use the markdown instead of raw HTML)
- Pass markdown result to `save_crawled_page` or save via a new `save_markdown` method

### API Changes

**Option A (recommended): Add `markdown` field to existing responses**

Modify the query and search endpoints to optionally include markdown content. Add `include_markdown: bool = False` parameter to `QueryRequest` and `SearchRequest`. When true, join against `crawled_pages.markdown` and include it in results.

**New endpoint**: `GET /page/{page_id}/markdown`

Returns just the markdown for a specific crawled page. This is the primary endpoint for agents that want clean content.

```python
@router.get("/page/{page_id}/markdown")
def get_page_markdown(page_id: str):
    """Return clean markdown for a crawled page.

    Response:
    {
      "data": {
        "url": "https://example.com/article",
        "markdown": "# Article Title\n\nContent here...",
        "meta": { "word_count": 1250, ... }
      },
      "sources": ["https://example.com/article"],
      "confidence": 0.92,
      "freshness": "2026-04-12T10:30:00Z"
    }
    """
```

**New endpoint**: `GET /page/{page_id}` (general page detail)

Returns the full page record including HTML, markdown, entities, and schemas. The `format` query parameter controls what content is included:
- `?format=markdown` -- markdown only (default)
- `?format=html` -- raw HTML only
- `?format=full` -- both HTML and markdown

**Modify `CrawlResponse`**: After a crawl completes, the response should include a `markdown_available: bool` field so callers know the content has been processed.

**Model changes** (`api/api/models.py`):

```python
class PageContent(BaseModel):
    url: str
    markdown: str | None = None
    html: str | None = None
    markdown_meta: dict | None = None

class QueryRequest(BaseModel):
    query: str
    limit: int = Field(default=10, ge=1, le=100)
    include_markdown: bool = False
```

### Testing Strategy

1. **Unit tests for `MarkdownPipeline`**:
   - Test with sample HTML pages (article, documentation, e-commerce product page)
   - Verify nav/footer/ads are stripped
   - Verify headings, lists, tables, code blocks are preserved
   - Verify relative URLs are resolved to absolute
   - Test empty HTML, malformed HTML, non-HTML content
   - Test fallback path (trafilatura failure triggers markdownify path)

2. **Quality benchmarks**:
   - Maintain a fixture set of 10-20 HTML files with expected markdown output
   - Measure token counts (HTML vs. markdown) to verify reduction
   - Track confidence scores across fixture set

3. **Integration tests**:
   - Full pipeline: HTML in Redis Stream -> Worker processes -> markdown in PostgreSQL
   - API test: crawl a URL, then fetch markdown via `/page/{id}/markdown`

4. **Regression tests**:
   - Golden file tests: specific HTML inputs produce specific markdown outputs
   - If a library update changes output, the test fails and we review

---

## Feature 2: MCP Server

### Why This Matters

The Model Context Protocol (MCP) is the emerging standard for AI agent tool integration, adopted by Claude (Anthropic), Cursor, Windsurf, OpenAI Agents SDK, and others. An MCP server lets any compatible agent discover Phantombot's capabilities and invoke them without custom integration code.

Without MCP, every agent integration requires:
- Reading Phantombot's API docs
- Writing custom HTTP client code
- Handling authentication, error formats, pagination

With MCP, an agent simply connects to Phantombot's MCP server and sees available tools with typed schemas. The LLM can invoke them directly.

### MCP Tools

Tools are actions with side effects or computation. Following MCP best practices, tools are designed around **outcomes** (what the agent wants to achieve), not raw API endpoints.

#### `crawl_url`

Trigger a crawl and return the job ID. The agent can poll or use the result later.

```
Tool: crawl_url
Description: Crawl a web page and extract its content as clean markdown.
             Returns a job ID for tracking. The crawled content will be
             available via query_knowledge and search_semantic once processed.

Input Schema:
  url: string (required) - The URL to crawl. Must be http or https.
  max_depth: integer (optional, default=3, range 1-10) - How many links deep to follow.

Output:
  job_id: string - UUID for tracking the crawl job
  url: string - The URL being crawled
  status: string - "pending"
```

#### `query_knowledge`

Search the knowledge base by text match. Returns entities and their sources.

```
Tool: query_knowledge
Description: Search Phantombot's knowledge base for entities matching a text query.
             Returns named entities (people, organizations, locations, products, etc.)
             extracted from crawled pages, with source URLs and confidence scores.

Input Schema:
  query: string (required) - Text to search for in extracted entities.
  limit: integer (optional, default=10, range 1-50) - Maximum results to return.

Output:
  results: array of {entity_text, entity_type, confidence, source_url, fetched_at}
  total: integer
```

#### `search_semantic`

Vector similarity search across the knowledge base.

```
Tool: search_semantic
Description: Perform semantic (vector similarity) search across Phantombot's knowledge base.
             Uses sentence embeddings to find conceptually related entities and content,
             even if the exact words don't match.

Input Schema:
  query: string (required) - Natural language query for semantic search.
  limit: integer (optional, default=10, range 1-50) - Maximum results to return.

Output:
  results: array of {id, score, entity_type, entity_text, confidence, page_id}
  total: integer
```

#### `get_entity`

Look up a specific entity by ID.

```
Tool: get_entity
Description: Retrieve detailed information about a specific entity by its ID.
             Use this after finding entity IDs from query_knowledge or search_semantic.

Input Schema:
  entity_id: string (required) - UUID of the entity to look up.

Output:
  entity_text: string
  entity_type: string
  confidence: float
  source_url: string
  fetched_at: string (ISO 8601)
```

#### `get_page_markdown` (new -- depends on Feature 1)

Retrieve the clean markdown content of a specific crawled page.

```
Tool: get_page_markdown
Description: Get the clean markdown content of a previously crawled page.
             Returns LLM-ready markdown with navigation, ads, and boilerplate removed.
             Use this to read the full content of a page found via search.

Input Schema:
  page_id: string (required) - UUID of the crawled page.

Output:
  url: string
  markdown: string
  meta: {word_count, has_code, has_tables, pipeline_version}
```

### MCP Resources

Resources are read-only data the client can fetch for context. Following MCP best practices, these are kept small and focused to avoid overwhelming the model's context window.

#### `phantombot://stats`

Crawl statistics for the Phantombot instance.

```
Resource: phantombot://stats
Description: Current crawl statistics including total pages crawled, entities extracted,
             and schemas detected.
MIME type: application/json

Content:
{
  "total_pages": 15234,
  "total_entities": 89421,
  "total_schemas": 3201,
  "pages_with_markdown": 14890,
  "domains_crawled": 342,
  "last_crawl_at": "2026-04-12T10:30:00Z",
  "uptime_seconds": 86400
}
```

Implementation: Simple aggregate queries against PostgreSQL.

#### `phantombot://recent`

Recently crawled pages for context about what data is available.

```
Resource: phantombot://recent
Description: The 20 most recently crawled pages, showing what data Phantombot
             currently has available.
MIME type: application/json

Content:
{
  "pages": [
    {
      "id": "uuid",
      "url": "https://example.com/article",
      "domain": "example.com",
      "fetched_at": "2026-04-12T10:30:00Z",
      "has_markdown": true,
      "entity_count": 12,
      "schema_count": 2
    },
    ...
  ]
}
```

Implementation: Query `crawled_pages` ordered by `fetched_at DESC LIMIT 20`, with entity/schema counts via subqueries.

### Architecture

**New package**: `mcp_server/` at the repository root, alongside `api/` and `intelligence/`.

```
mcp_server/
  pyproject.toml
  mcp_server/
    __init__.py
    server.py          # MCP server definition, tool/resource registration
    tools.py           # Tool handler implementations
    resources.py       # Resource handler implementations
    db.py              # Database access (shared patterns with api/dependencies)
    config.py          # Environment-based config
```

**SDK choice**: Use the official `mcp` Python SDK (PyPI: `mcp`), which includes the FastMCP high-level API. FastMCP is the standard way to build MCP servers in Python -- it handles protocol negotiation, transport, and message serialization. It is now part of the official SDK (merged from the standalone `fastmcp` project).

**Server implementation** (`mcp_server/mcp_server/server.py`):

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "Phantombot",
    description="Open-source web crawler for the AI agentic era. "
                "Crawl URLs, extract entities, detect schemas, "
                "and get clean markdown content.",
)

@mcp.tool()
def crawl_url(url: str, max_depth: int = 3) -> dict:
    """Crawl a web page and extract its content.

    Args:
        url: The URL to crawl (http or https)
        max_depth: How many links deep to follow (1-10, default 3)
    """
    ...

@mcp.tool()
def query_knowledge(query: str, limit: int = 10) -> dict:
    """Search the knowledge base for entities matching a text query.

    Args:
        query: Text to search for
        limit: Maximum results (1-50, default 10)
    """
    ...

@mcp.tool()
def search_semantic(query: str, limit: int = 10) -> dict:
    """Semantic vector search across the knowledge base.

    Args:
        query: Natural language search query
        limit: Maximum results (1-50, default 10)
    """
    ...

@mcp.tool()
def get_entity(entity_id: str) -> dict:
    """Look up a specific entity by ID.

    Args:
        entity_id: UUID of the entity
    """
    ...

@mcp.tool()
def get_page_markdown(page_id: str) -> dict:
    """Get clean markdown content of a crawled page.

    Args:
        page_id: UUID of the crawled page
    """
    ...

@mcp.resource("phantombot://stats")
def get_stats() -> str:
    """Current crawl statistics."""
    ...

@mcp.resource("phantombot://recent")
def get_recent() -> str:
    """Recently crawled pages."""
    ...
```

**Transport**: Support both `stdio` (for local agent integrations like Claude Desktop, Cursor) and `streamable-http` (for remote/networked agents). The `stdio` transport is the default for local development; `streamable-http` is configured via environment variable for production.

**Database access**: The MCP server connects directly to PostgreSQL, Redis, and Qdrant using the same connection patterns as the API layer. This avoids an HTTP hop through the FastAPI server. The `db.py` module mirrors `api/dependencies.py` with connection pooling.

**Relationship to existing API**: The MCP server is a **separate process** from the FastAPI server. Both read from the same PostgreSQL/Qdrant/Redis backends. This is intentional:
- The FastAPI server serves HTTP clients (webhooks, dashboards, direct API consumers)
- The MCP server serves AI agents via the MCP protocol
- Both are stateless readers of the same data stores
- The Go crawler and Python intelligence worker are the only writers

### Configuration

All configuration via environment variables, consistent with existing services:

```
# MCP Server Config
MCP_TRANSPORT=stdio           # or "streamable-http"
MCP_HTTP_HOST=0.0.0.0         # only for streamable-http transport
MCP_HTTP_PORT=8001            # only for streamable-http transport

# Shared infrastructure (same as intelligence/api)
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=phantombot
POSTGRES_USER=phantombot
POSTGRES_PASSWORD=localdev
REDIS_URL=redis://localhost:6379
QDRANT_HOST=localhost
QDRANT_PORT=6335
```

**Claude Desktop integration** (`claude_desktop_config.json` example for documentation):

```json
{
  "mcpServers": {
    "phantombot": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/phantombot/mcp_server", "mcp_server"],
      "env": {
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": "5432",
        "POSTGRES_DB": "phantombot",
        "POSTGRES_USER": "phantombot",
        "POSTGRES_PASSWORD": "localdev",
        "REDIS_URL": "redis://localhost:6379",
        "QDRANT_HOST": "localhost",
        "QDRANT_PORT": "6335"
      }
    }
  }
}
```

**Docker Compose addition** (for streamable-http mode):

```yaml
mcp-server:
  build: ./mcp_server
  ports:
    - "8001:8001"
  environment:
    MCP_TRANSPORT: streamable-http
    MCP_HTTP_PORT: "8001"
    POSTGRES_HOST: postgres
    REDIS_URL: redis://redis:6379
    QDRANT_HOST: qdrant
    QDRANT_PORT: "6333"
  depends_on:
    postgres:
      condition: service_healthy
    redis:
      condition: service_healthy
    qdrant:
      condition: service_healthy
```

### Testing Strategy

1. **Unit tests for tool handlers**:
   - Mock database connections
   - Verify each tool returns the expected schema
   - Test input validation (invalid URLs, out-of-range limits, nonexistent entity IDs)
   - Test error handling (database down, empty results)

2. **Protocol conformance tests**:
   - Use the MCP SDK's test client to verify protocol compliance
   - Test tool listing (`tools/list`), tool invocation (`tools/call`)
   - Test resource listing (`resources/list`), resource reading (`resources/read`)
   - Verify JSON schema correctness for all tool inputs/outputs

3. **Integration tests**:
   - Start MCP server against test database
   - Invoke `crawl_url` -> verify job created in PostgreSQL
   - Invoke `query_knowledge` -> verify results match test data
   - Read `phantombot://stats` -> verify counts match

4. **Agent smoke tests**:
   - Document manual test: connect via Claude Desktop, invoke each tool
   - Document manual test: connect via Cursor, verify tool discovery

---

## v1 Scope

The first implementation targets a working end-to-end pipeline. Explicitly in and out of scope:

**In scope (v1)**:
- `MarkdownPipeline` using trafilatura (primary) with markdownify fallback
- `markdown` and `markdown_meta` columns on `crawled_pages`
- `GET /page/{page_id}/markdown` API endpoint
- `include_markdown` parameter on existing query/search endpoints
- MCP server with all 5 tools and 2 resources
- `stdio` and `streamable-http` transport support
- Docker Compose service for MCP server
- Unit and integration tests for both features

**Out of scope (v2+)**:
- Backfilling markdown for already-crawled pages (migration script -- v2)
- Streaming markdown as pages are crawled (SSE/WebSocket -- v2)
- MCP prompts (pre-built prompt templates -- v2)
- MCP sampling (letting the server request LLM completions -- v2)
- Custom markdown post-processing rules per domain (v2)
- Markdown diff/change detection between crawls (v2)
- Rate limiting on MCP tools (v2)
- MCP authentication beyond environment-level config (v2)

**Implementation order**:
1. Feature 1 first (markdown pipeline) -- the MCP server's `get_page_markdown` tool depends on it
2. Feature 2 second (MCP server) -- can be developed in parallel once the DB schema is settled
3. Estimated effort: ~3-4 days for markdown pipeline, ~2-3 days for MCP server

## Tech Stack Additions

| Dependency | Version | Purpose | Package |
|---|---|---|---|
| `trafilatura` | >=1.12.0 | Content extraction + markdown conversion | `intelligence` |
| `markdownify` | >=0.14.0 | Fallback HTML-to-markdown structural conversion | `intelligence` |
| `mcp` | >=1.25.0 | Official MCP Python SDK (includes FastMCP) | `mcp_server` |

No changes to Go crawler, PostgreSQL, Redis, or Qdrant versions required.

**New `mcp_server/pyproject.toml`**:

```toml
[project]
name = "phantombot-mcp"
version = "0.1.0"
description = "Phantombot MCP server -- AI agent tool integration"
requires-python = ">=3.12"
dependencies = [
    "mcp>=1.25.0",
    "psycopg[binary]>=3.1.0",
    "redis>=5.0.0",
    "qdrant-client>=1.9.0",
    "sentence-transformers>=3.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
]

[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"
```

**Updated `intelligence/pyproject.toml`** (add trafilatura and markdownify):

```toml
dependencies = [
    "redis>=5.0.0",
    "psycopg[binary]>=3.1.0",
    "qdrant-client>=1.9.0",
    "spacy>=3.7.0",
    "beautifulsoup4>=4.12.0",
    "pydantic>=2.0.0",
    "sentence-transformers>=3.0.0",
    "trafilatura>=1.12.0",
    "markdownify>=0.14.0",
]
```
