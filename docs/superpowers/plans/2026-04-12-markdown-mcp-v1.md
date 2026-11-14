# Markdown Output + MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add HTML-to-markdown conversion pipeline and MCP server so AI agents get clean, token-efficient content and can discover/use Phantombot via the Model Context Protocol.

**Architecture:** New MarkdownPipeline in intelligence layer using trafilatura + markdownify. New mcp_server/ package using official MCP Python SDK (FastMCP). Both features share the same PostgreSQL/Redis/Qdrant backends.

**Tech Stack:** Python 3.12+, trafilatura, markdownify, mcp SDK, FastMCP, pytest

---

## Feature 1: Markdown Pipeline

### Task 1: DB migration -- add markdown columns

- [ ] Create `store/migrations/002_add_markdown.sql`

**File:** `/Users/suryakoritala/phantombot/store/migrations/002_add_markdown.sql`

```sql
-- 002_add_markdown.sql
-- Add markdown storage to crawled_pages

ALTER TABLE crawled_pages ADD COLUMN IF NOT EXISTS markdown TEXT;
ALTER TABLE crawled_pages ADD COLUMN IF NOT EXISTS markdown_meta JSONB;

CREATE INDEX IF NOT EXISTS idx_crawled_pages_has_markdown
    ON crawled_pages ((markdown IS NOT NULL));

COMMENT ON COLUMN crawled_pages.markdown IS
    'Clean markdown extracted from HTML, suitable for LLM consumption';
COMMENT ON COLUMN crawled_pages.markdown_meta IS
    'Metadata about the markdown extraction: word_count, has_code, has_tables, pipeline_version, extracted_at';
```

**Verify:**

```bash
cd /Users/suryakoritala/phantombot
# Confirm file exists and SQL is valid syntax
cat store/migrations/002_add_markdown.sql
# If local PG is running:
# psql -U phantombot -d phantombot -f store/migrations/002_add_markdown.sql
```

**Commit:** `feat(store): add markdown and markdown_meta columns to crawled_pages`

---

### Task 2: Add trafilatura + markdownify to intelligence dependencies

- [ ] Update `intelligence/pyproject.toml`

**File:** `/Users/suryakoritala/phantombot/intelligence/pyproject.toml`

Add two lines to the `dependencies` list:

```toml
[project]
name = "phantombot-intelligence"
version = "0.1.0"
description = "Phantombot intelligence layer — extraction pipelines"
requires-python = ">=3.12"
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

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
]

[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"
```

**Verify:**

```bash
cd /Users/suryakoritala/phantombot/intelligence
pip install -e ".[dev]"
python -c "import trafilatura; print(trafilatura.__version__)"
python -c "import markdownify; print('markdownify ok')"
```

**Commit:** `feat(intelligence): add trafilatura and markdownify dependencies`

---

### Task 3: MarkdownResult model

- [ ] Add `MarkdownResult` to `intelligence/intelligence/models.py`

**File:** `/Users/suryakoritala/phantombot/intelligence/intelligence/models.py`

Append after the `DetectedSchema` class:

```python
class MarkdownResult(BaseModel):
    markdown: str
    word_count: int = 0
    has_code: bool = False
    has_tables: bool = False
    has_images: bool = False
    extraction_method: str = "trafilatura_direct"  # or "trafilatura_markdownify"
    confidence: float = Field(ge=0.0, le=1.0, default=0.9)
    pipeline_version: str = "markdown-v1"
```

**Verify:**

```bash
cd /Users/suryakoritala/phantombot/intelligence
python -c "from intelligence.models import MarkdownResult; r = MarkdownResult(markdown='# Hello'); print(r.model_dump())"
```

**Commit:** `feat(intelligence): add MarkdownResult model`

---

### Task 4: Add markdown test fixtures

- [ ] Create `fixtures/article_page.html` -- a realistic blog article with nav, ads, code blocks
- [ ] Create `fixtures/code_doc_page.html` -- documentation page with code blocks and tables

**File:** `/Users/suryakoritala/phantombot/fixtures/article_page.html`

```html
<!DOCTYPE html>
<html>
<head><title>Understanding Web Crawlers</title></head>
<body>
<nav>
  <a href="/">Home</a>
  <a href="/blog">Blog</a>
  <a href="/about">About</a>
</nav>
<div class="ad-banner">
  <p>Buy our premium plan! Click here for 50% off!</p>
</div>
<article>
  <h1>Understanding Web Crawlers</h1>
  <p>Web crawlers are automated programs that browse the internet systematically.
     They are used by search engines like <a href="https://google.com">Google</a>
     to index web content.</p>
  <h2>How Crawlers Work</h2>
  <p>A crawler starts with a list of <strong>seed URLs</strong>. It visits each URL,
     extracts links, and adds them to a queue. This process is called
     <em>breadth-first crawling</em>.</p>
  <h3>Key Components</h3>
  <ul>
    <li>URL frontier - manages the queue of URLs to visit</li>
    <li>Fetcher - downloads web pages</li>
    <li>Parser - extracts links and content</li>
    <li>Storage - saves the crawled data</li>
  </ul>
  <h2>Robots.txt</h2>
  <p>Websites use <code>robots.txt</code> to control crawler access. Always respect
     these rules when building a crawler.</p>
  <p>Here is an example:</p>
  <pre><code>User-agent: *
Disallow: /private/
Allow: /public/</code></pre>
  <h2>Conclusion</h2>
  <p>Web crawlers are essential for the modern internet. They power search engines,
     AI training data collection, and web archiving.</p>
</article>
<footer>
  <p>Copyright 2026 Example Blog</p>
  <a href="/privacy">Privacy Policy</a>
  <a href="/terms">Terms of Service</a>
</footer>
<script>console.log("tracking");</script>
</body>
</html>
```

**File:** `/Users/suryakoritala/phantombot/fixtures/code_doc_page.html`

```html
<!DOCTYPE html>
<html>
<head><title>API Reference - Phantombot</title></head>
<body>
<nav>
  <a href="/docs">Docs</a>
  <a href="/api">API</a>
  <a href="/github">GitHub</a>
</nav>
<main>
  <h1>API Reference</h1>
  <p>Phantombot exposes a RESTful API for crawling and querying web data.</p>
  <h2>Endpoints</h2>
  <table>
    <thead>
      <tr><th>Method</th><th>Path</th><th>Description</th></tr>
    </thead>
    <tbody>
      <tr><td>POST</td><td>/crawl</td><td>Start a new crawl job</td></tr>
      <tr><td>POST</td><td>/query</td><td>Query extracted entities</td></tr>
      <tr><td>POST</td><td>/search</td><td>Semantic vector search</td></tr>
      <tr><td>GET</td><td>/entity/{id}</td><td>Get entity by ID</td></tr>
    </tbody>
  </table>
  <h2>Authentication</h2>
  <p>Pass your API key in the <code>X-API-Key</code> header:</p>
  <pre><code>curl -H "X-API-Key: your-key" https://api.example.com/query</code></pre>
  <h2>Example: Start a Crawl</h2>
  <pre><code class="language-python">import requests

resp = requests.post("https://api.example.com/crawl", json={
    "url": "https://example.com",
    "max_depth": 3,
})
print(resp.json())</code></pre>
  <h2>Rate Limits</h2>
  <p>The API is rate-limited to <strong>100 requests per minute</strong>. If you
     exceed this limit, you will receive a <code>429</code> response.</p>
</main>
<footer>
  <p>Phantombot Docs v0.1.0</p>
</footer>
</body>
</html>
```

**Verify:**

```bash
ls -la /Users/suryakoritala/phantombot/fixtures/article_page.html
ls -la /Users/suryakoritala/phantombot/fixtures/code_doc_page.html
```

**Commit:** `test(fixtures): add article and code doc HTML fixtures for markdown pipeline`

---

### Task 5: Write tests for MarkdownPipeline (TDD -- tests first)

- [ ] Add markdown fixtures to `intelligence/tests/conftest.py`
- [ ] Create `intelligence/tests/test_markdown_pipeline.py`

**File:** `/Users/suryakoritala/phantombot/intelligence/tests/conftest.py`

Replace entire file with:

```python
import pathlib
import pytest

FIXTURES_DIR = pathlib.Path(__file__).parent.parent.parent / "fixtures"


@pytest.fixture
def simple_html() -> str:
    return (FIXTURES_DIR / "simple_page.html").read_text()


@pytest.fixture
def entity_rich_html() -> str:
    return (FIXTURES_DIR / "entity_rich_page.html").read_text()


@pytest.fixture
def jsonld_html() -> str:
    return (FIXTURES_DIR / "jsonld_page.html").read_text()


@pytest.fixture
def openapi_html() -> str:
    return (FIXTURES_DIR / "openapi_page.html").read_text()


@pytest.fixture
def article_html() -> str:
    return (FIXTURES_DIR / "article_page.html").read_text()


@pytest.fixture
def code_doc_html() -> str:
    return (FIXTURES_DIR / "code_doc_page.html").read_text()
```

**File:** `/Users/suryakoritala/phantombot/intelligence/tests/test_markdown_pipeline.py`

```python
import re

import pytest

from intelligence.models import MarkdownResult
from intelligence.pipelines.markdown import MarkdownPipeline

SOURCE_URL = "https://example.com/test"


@pytest.fixture
def pipeline():
    return MarkdownPipeline()


class TestMarkdownPipelineBasic:
    """Test basic extraction and edge cases."""

    def test_empty_html_returns_empty_list(self, pipeline):
        result = pipeline.extract("", SOURCE_URL)
        assert result == []

    def test_whitespace_only_returns_empty_list(self, pipeline):
        result = pipeline.extract("   \n\t  ", SOURCE_URL)
        assert result == []

    def test_none_html_returns_empty_list(self, pipeline):
        result = pipeline.extract(None, SOURCE_URL)
        assert result == []

    def test_returns_list_of_markdown_result(self, pipeline, simple_html):
        results = pipeline.extract(simple_html, SOURCE_URL)
        assert isinstance(results, list)
        assert len(results) <= 1
        if results:
            assert isinstance(results[0], MarkdownResult)

    def test_result_has_markdown_content(self, pipeline, simple_html):
        results = pipeline.extract(simple_html, SOURCE_URL)
        assert len(results) == 1
        assert len(results[0].markdown) > 0

    def test_pipeline_version_set(self, pipeline, simple_html):
        results = pipeline.extract(simple_html, SOURCE_URL)
        assert results[0].pipeline_version == "markdown-v1"


class TestMarkdownPipelineContentExtraction:
    """Test that nav/ads/boilerplate are stripped and content is preserved."""

    def test_strips_nav_elements(self, pipeline, article_html):
        results = pipeline.extract(article_html, SOURCE_URL)
        assert len(results) == 1
        md = results[0].markdown
        # Nav links should be stripped
        assert "/blog" not in md or "Blog" not in md.split("\n")[0:3]

    def test_strips_ad_banners(self, pipeline, article_html):
        results = pipeline.extract(article_html, SOURCE_URL)
        md = results[0].markdown
        assert "50% off" not in md

    def test_strips_footer(self, pipeline, article_html):
        results = pipeline.extract(article_html, SOURCE_URL)
        md = results[0].markdown
        assert "Privacy Policy" not in md
        assert "Terms of Service" not in md

    def test_strips_script_tags(self, pipeline, article_html):
        results = pipeline.extract(article_html, SOURCE_URL)
        md = results[0].markdown
        assert "tracking" not in md
        assert "<script>" not in md

    def test_preserves_article_content(self, pipeline, article_html):
        results = pipeline.extract(article_html, SOURCE_URL)
        md = results[0].markdown
        assert "Web Crawlers" in md or "web crawlers" in md
        assert "seed URLs" in md or "seed" in md

    def test_preserves_headings(self, pipeline, article_html):
        results = pipeline.extract(article_html, SOURCE_URL)
        md = results[0].markdown
        assert "#" in md  # At least one ATX heading

    def test_preserves_list_items(self, pipeline, article_html):
        results = pipeline.extract(article_html, SOURCE_URL)
        md = results[0].markdown
        # Should contain list markers (-, *, or numbered)
        assert "URL frontier" in md or "Fetcher" in md

    def test_preserves_code_blocks(self, pipeline, article_html):
        results = pipeline.extract(article_html, SOURCE_URL)
        md = results[0].markdown
        assert "robots.txt" in md or "User-agent" in md or "Disallow" in md


class TestMarkdownPipelineCodeDocs:
    """Test with documentation page containing tables and code."""

    def test_preserves_table_content(self, pipeline, code_doc_html):
        results = pipeline.extract(code_doc_html, SOURCE_URL)
        md = results[0].markdown
        # Table content should be present (even if table structure varies)
        assert "/crawl" in md
        assert "/query" in md

    def test_preserves_code_snippets(self, pipeline, code_doc_html):
        results = pipeline.extract(code_doc_html, SOURCE_URL)
        md = results[0].markdown
        assert "requests.post" in md or "import requests" in md

    def test_has_code_flag(self, pipeline, code_doc_html):
        results = pipeline.extract(code_doc_html, SOURCE_URL)
        assert results[0].has_code is True

    def test_has_tables_flag(self, pipeline, code_doc_html):
        results = pipeline.extract(code_doc_html, SOURCE_URL)
        assert results[0].has_tables is True


class TestMarkdownPipelineMetadata:
    """Test metadata computation."""

    def test_word_count_positive(self, pipeline, article_html):
        results = pipeline.extract(article_html, SOURCE_URL)
        assert results[0].word_count > 0

    def test_confidence_in_range(self, pipeline, article_html):
        results = pipeline.extract(article_html, SOURCE_URL)
        assert 0.0 <= results[0].confidence <= 1.0

    def test_extraction_method_set(self, pipeline, article_html):
        results = pipeline.extract(article_html, SOURCE_URL)
        assert results[0].extraction_method in (
            "trafilatura_direct",
            "trafilatura_markdownify",
        )


class TestMarkdownPipelinePostProcessing:
    """Test post-processing rules."""

    def test_no_excessive_blank_lines(self, pipeline, article_html):
        results = pipeline.extract(article_html, SOURCE_URL)
        md = results[0].markdown
        assert "\n\n\n\n" not in md  # Max 2 consecutive blank lines (3 newlines)

    def test_no_remaining_html_tags(self, pipeline, article_html):
        results = pipeline.extract(article_html, SOURCE_URL)
        md = results[0].markdown
        # Should not contain HTML tags (except possibly in code blocks)
        lines_outside_code = []
        in_code = False
        for line in md.split("\n"):
            if line.strip().startswith("```"):
                in_code = not in_code
                continue
            if not in_code:
                lines_outside_code.append(line)
        outside_code_text = "\n".join(lines_outside_code)
        # Check for common HTML tags outside code blocks
        assert "<div>" not in outside_code_text
        assert "<span>" not in outside_code_text
        assert "<nav>" not in outside_code_text

    def test_relative_urls_resolved(self, pipeline):
        html = """
        <html><body>
        <article>
        <p>Visit <a href="/about">our about page</a> for more info.</p>
        <p>Also see <a href="https://example.com/full">this full link</a>.</p>
        </article>
        </body></html>
        """
        results = pipeline.extract(html, "https://example.com/blog/post")
        if results:
            md = results[0].markdown
            # If relative URL is present, it should be resolved
            if "/about" in md:
                assert "https://example.com/about" in md or "(https://example.com/about)" in md


class TestMarkdownPipelineLowQuality:
    """Test handling of low-quality or unusual input."""

    def test_tiny_html_low_confidence(self, pipeline):
        html = "<html><body><p>Hi</p></body></html>"
        results = pipeline.extract(html, SOURCE_URL)
        # Either empty or low confidence
        if results:
            assert results[0].confidence < 0.9 or len(results[0].markdown) < 50

    def test_malformed_html_does_not_crash(self, pipeline):
        html = "<html><body><p>Unclosed tag<div>Mixed <b>nesting</p></div>"
        results = pipeline.extract(html, SOURCE_URL)
        assert isinstance(results, list)

    def test_non_html_content(self, pipeline):
        text = "This is just plain text, not HTML at all."
        results = pipeline.extract(text, SOURCE_URL)
        assert isinstance(results, list)
```

**Verify (tests should FAIL -- TDD red phase):**

```bash
cd /Users/suryakoritala/phantombot/intelligence
python -m pytest tests/test_markdown_pipeline.py -x 2>&1 | head -30
# Expected: ImportError or ModuleNotFoundError for intelligence.pipelines.markdown
```

**Commit:** `test(intelligence): add markdown pipeline tests (red phase)`

---

### Task 6: Implement MarkdownPipeline

- [ ] Create `intelligence/intelligence/pipelines/markdown.py`

**File:** `/Users/suryakoritala/phantombot/intelligence/intelligence/pipelines/markdown.py`

```python
import re
from urllib.parse import urljoin, urlparse

import trafilatura
from markdownify import markdownify as md_convert

from intelligence.models import MarkdownResult
from intelligence.pipelines.base import BasePipeline


class MarkdownPipeline(BasePipeline):
    """Converts raw HTML to clean, token-efficient markdown.

    Strategy:
    1. Use trafilatura to extract main content as markdown (primary path)
    2. If trafilatura returns empty/low-quality, fall back to
       trafilatura HTML extraction piped through markdownify
    3. Post-process: normalize whitespace, fix relative URLs, strip leftover HTML
    """

    MIN_CONTENT_LENGTH = 50  # Characters below this = low confidence

    def extract(self, html: str, source_url: str) -> list[MarkdownResult]:
        if not html or not html.strip():
            return []

        # Primary path: trafilatura direct markdown output
        markdown = trafilatura.extract(
            html,
            output_format="markdown",
            include_links=True,
            include_images=True,
            include_tables=True,
        )

        extraction_method = "trafilatura_direct"

        # Fallback: trafilatura HTML extraction + markdownify
        if not markdown or len(markdown.strip()) < self.MIN_CONTENT_LENGTH:
            markdown = self._fallback_extract(html)
            if markdown:
                extraction_method = "trafilatura_markdownify"

        if not markdown or not markdown.strip():
            return []

        # Post-processing
        markdown = self._post_process(markdown, source_url)

        if not markdown.strip():
            return []

        # Compute metadata
        word_count = len(markdown.split())
        has_code = "```" in markdown or "    " in markdown
        has_tables = "|" in markdown and "---" in markdown
        has_images = "![" in markdown

        # Confidence scoring
        confidence = self._compute_confidence(markdown, html)

        return [
            MarkdownResult(
                markdown=markdown,
                word_count=word_count,
                has_code=has_code,
                has_tables=has_tables,
                has_images=has_images,
                extraction_method=extraction_method,
                confidence=confidence,
                pipeline_version="markdown-v1",
            )
        ]

    def _fallback_extract(self, html: str) -> str | None:
        """Trafilatura HTML extraction piped through markdownify."""
        clean_html = trafilatura.extract(
            html,
            output_format="html",
            include_links=True,
            include_images=True,
            include_tables=True,
        )
        if not clean_html:
            return None

        return md_convert(
            clean_html,
            heading_style="ATX",
            bullets="-",
            strip=["script", "style", "nav", "footer"],
        )

    def _post_process(self, markdown: str, source_url: str) -> str:
        """Clean up markdown output."""
        # Normalize excessive blank lines (max 2 consecutive newlines)
        markdown = re.sub(r"\n{4,}", "\n\n\n", markdown)

        # Resolve relative URLs
        markdown = self._resolve_relative_urls(markdown, source_url)

        # Strip any remaining HTML tags (outside of code blocks)
        markdown = self._strip_html_outside_code(markdown)

        # Trim leading/trailing whitespace
        markdown = markdown.strip()

        return markdown

    def _resolve_relative_urls(self, markdown: str, source_url: str) -> str:
        """Convert relative URLs in markdown links/images to absolute."""
        base_url = source_url

        def replace_url(match: re.Match) -> str:
            prefix = match.group(1)  # ]( or ](
            url = match.group(2)
            suffix = match.group(3)  # ) or "title")
            parsed = urlparse(url)
            if not parsed.scheme and not parsed.netloc and url.startswith("/"):
                url = urljoin(base_url, url)
            return f"{prefix}{url}{suffix}"

        # Match markdown links: [text](url) and images: ![alt](url)
        markdown = re.sub(
            r"(\]\()([^)]+)(\))",
            replace_url,
            markdown,
        )
        return markdown

    def _strip_html_outside_code(self, markdown: str) -> str:
        """Remove leftover HTML tags that are outside code blocks."""
        lines = markdown.split("\n")
        result = []
        in_code = False

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("```"):
                in_code = not in_code
                result.append(line)
                continue

            if in_code:
                result.append(line)
            else:
                # Remove HTML tags but preserve content
                cleaned = re.sub(r"<(?!code|/code)[^>]+>", "", line)
                result.append(cleaned)

        return "\n".join(result)

    def _compute_confidence(self, markdown: str, html: str) -> float:
        """Heuristic confidence based on extraction quality."""
        confidence = 0.9  # Base confidence for successful extraction

        # Penalize very short content
        if len(markdown.strip()) < self.MIN_CONTENT_LENGTH:
            confidence = 0.3

        # Penalize if markdown is drastically shorter than a rough plaintext estimate
        # (rough estimate: HTML is ~3-5x longer than content)
        html_text_estimate = len(html) / 4
        if html_text_estimate > 100 and len(markdown) < html_text_estimate * 0.05:
            confidence = min(confidence, 0.5)

        return round(confidence, 2)
```

**Verify (tests should PASS -- TDD green phase):**

```bash
cd /Users/suryakoritala/phantombot/intelligence
python -m pytest tests/test_markdown_pipeline.py -v
```

**Commit:** `feat(intelligence): implement MarkdownPipeline with trafilatura + markdownify`

---

### Task 7: Update KnowledgeStore to save markdown

- [ ] Add `save_markdown` method and update `save_crawled_page` upsert

**File:** `/Users/suryakoritala/phantombot/intelligence/intelligence/store.py`

Add this import at the top (after existing imports):

```python
from intelligence.models import CrawlMessage, ExtractedEntity, DetectedSchema, MarkdownResult
```

(Replace the existing import line that reads `from intelligence.models import CrawlMessage, ExtractedEntity, DetectedSchema`.)

Add this method to the `KnowledgeStore` class, after `save_crawled_page`:

```python
    def save_markdown(self, page_id: str, result: MarkdownResult):
        """Save extracted markdown for a crawled page."""
        meta = {
            "word_count": result.word_count,
            "has_code": result.has_code,
            "has_tables": result.has_tables,
            "has_images": result.has_images,
            "pipeline_version": result.pipeline_version,
            "extraction_method": result.extraction_method,
            "confidence": result.confidence,
        }
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE crawled_pages
                SET markdown = %s, markdown_meta = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (result.markdown, json.dumps(meta), page_id),
            )
```

Also add a read method for the API to use:

```python
    def get_page_markdown(self, page_id: str) -> dict | None:
        """Retrieve markdown and metadata for a crawled page."""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, url, markdown, markdown_meta, fetched_at
                FROM crawled_pages
                WHERE id = %s
                """,
                (page_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            columns = [desc[0] for desc in cur.description]
            return dict(zip(columns, row))
```

**Verify:**

```bash
cd /Users/suryakoritala/phantombot/intelligence
python -c "from intelligence.store import KnowledgeStore; print('import ok')"
```

**Commit:** `feat(intelligence): add save_markdown and get_page_markdown to KnowledgeStore`

---

### Task 8: Update Worker to run markdown pipeline

- [ ] Modify `intelligence/intelligence/worker.py`

**File:** `/Users/suryakoritala/phantombot/intelligence/intelligence/worker.py`

Add import at top (after existing pipeline imports):

```python
from intelligence.pipelines.markdown import MarkdownPipeline
```

In `__init__`, add after `self._schema_pipeline = SchemaPipeline()`:

```python
        self._markdown_pipeline = MarkdownPipeline()
```

In `process_message`, add markdown extraction **before** entity extraction (so markdown is available first). After `page_id = self._store.save_crawled_page(msg)`, add:

```python
        # Markdown extraction (run first -- other pipelines could use it)
        md_results = self._markdown_pipeline.extract(msg.html, msg.url)
        if md_results:
            self._store.save_markdown(page_id, md_results[0])
            logger.info(f"  Extracted markdown ({md_results[0].word_count} words) from {msg.url}")
```

The full updated `process_message` method should be:

```python
    def process_message(self, msg: CrawlMessage):
        """Run all pipelines on a crawled page and store results."""
        logger.info(f"Processing: {msg.url}")

        page_id = self._store.save_crawled_page(msg)

        # Markdown extraction (run first -- other pipelines could use it)
        md_results = self._markdown_pipeline.extract(msg.html, msg.url)
        if md_results:
            self._store.save_markdown(page_id, md_results[0])
            logger.info(f"  Extracted markdown ({md_results[0].word_count} words) from {msg.url}")

        # Entity extraction
        entities = self._entity_pipeline.extract(msg.html, msg.url)
        if entities:
            self._store.save_entities(page_id, entities)
            logger.info(f"  Extracted {len(entities)} entities from {msg.url}")

        # Schema detection
        schemas = self._schema_pipeline.extract(msg.html, msg.url)
        if schemas:
            self._store.save_schemas(page_id, schemas)
            logger.info(f"  Detected {len(schemas)} schemas from {msg.url}")
```

**Verify:**

```bash
cd /Users/suryakoritala/phantombot/intelligence
python -c "from intelligence.worker import Worker; print('Worker imports ok')"
```

**Commit:** `feat(intelligence): integrate MarkdownPipeline into Worker processing loop`

---

### Task 9: New API endpoint GET /page/{page_id}/markdown

- [ ] Add `PageContent` model to `api/api/models.py`
- [ ] Create `api/api/routes/page.py`
- [ ] Register the new router in `api/api/main.py`

**File:** `/Users/suryakoritala/phantombot/api/api/models.py`

Append after the `PhantomResponse` class:

```python
class PageContent(BaseModel):
    url: str
    markdown: str | None = None
    html: str | None = None
    markdown_meta: dict | None = None
```

**File:** `/Users/suryakoritala/phantombot/api/api/routes/page.py`

```python
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_pg_connection, verify_api_key
from api.models import PhantomResponse

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.get("/page/{page_id}/markdown", response_model=PhantomResponse)
def get_page_markdown(page_id: str):
    """Return clean markdown for a crawled page."""
    pg = get_pg_connection()
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT id, url, markdown, markdown_meta, fetched_at
            FROM crawled_pages
            WHERE id = %s
            """,
            (page_id,),
        )
        row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Page not found")

    columns = [desc[0] for desc in cur.description]
    data = dict(zip(columns, row))

    if data.get("markdown") is None:
        raise HTTPException(status_code=404, detail="Markdown not available for this page")

    # Serialize datetime fields
    for key, val in data.items():
        if isinstance(val, datetime):
            data[key] = val.isoformat()

    # Convert UUID to string
    data["id"] = str(data["id"])

    return PhantomResponse(
        data={
            "url": data["url"],
            "markdown": data["markdown"],
            "meta": data.get("markdown_meta"),
        },
        sources=[data["url"]],
        confidence=data.get("markdown_meta", {}).get("confidence", 0.0) if data.get("markdown_meta") else 0.0,
        freshness=data.get("fetched_at"),
        related=[],
    )
```

**File:** `/Users/suryakoritala/phantombot/api/api/main.py`

Replace with:

```python
from fastapi import FastAPI

from api.routes import crawl, query, search, page

app = FastAPI(
    title="Phantombot",
    description="Agent-queryable web knowledge API",
    version="0.1.0",
)

app.include_router(crawl.router, tags=["crawl"])
app.include_router(query.router, tags=["query"])
app.include_router(search.router, tags=["search"])
app.include_router(page.router, tags=["page"])


@app.get("/health")
def health():
    return {"status": "ok"}
```

**Verify:**

```bash
cd /Users/suryakoritala/phantombot/api
python -c "from api.main import app; print([r.path for r in app.routes])"
# Should include '/page/{page_id}/markdown'
```

**Commit:** `feat(api): add GET /page/{page_id}/markdown endpoint`

---

### Task 10: Update query/search to support include_markdown

- [ ] Add `include_markdown` to `QueryRequest` and `SearchRequest`
- [ ] Update query route to join markdown when requested
- [ ] Update search route to include markdown when requested

**File:** `/Users/suryakoritala/phantombot/api/api/models.py`

Update `QueryRequest`:

```python
class QueryRequest(BaseModel):
    query: str
    limit: int = Field(default=10, ge=1, le=100)
    include_markdown: bool = False
```

Update `SearchRequest`:

```python
class SearchRequest(BaseModel):
    query: str
    limit: int = Field(default=10, ge=1, le=100)
    include_markdown: bool = False
```

**File:** `/Users/suryakoritala/phantombot/api/api/routes/query.py`

Replace the `query_knowledge` function body to support `include_markdown`. Full replacement:

```python
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_pg_connection, verify_api_key
from api.models import QueryRequest, PhantomResponse

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.post("/query", response_model=PhantomResponse)
def query_knowledge(req: QueryRequest):
    pg = get_pg_connection()

    if req.include_markdown:
        sql = """
            SELECT e.entity_text, e.entity_type, e.confidence,
                   p.url as source_url, p.fetched_at, e.extracted_at,
                   p.markdown, p.markdown_meta
            FROM entities e
            JOIN crawled_pages p ON e.page_id = p.id
            WHERE e.entity_text ILIKE %s
            ORDER BY e.confidence DESC
            LIMIT %s
        """
    else:
        sql = """
            SELECT e.entity_text, e.entity_type, e.confidence,
                   p.url as source_url, p.fetched_at, e.extracted_at
            FROM entities e
            JOIN crawled_pages p ON e.page_id = p.id
            WHERE e.entity_text ILIKE %s
            ORDER BY e.confidence DESC
            LIMIT %s
        """

    with pg.cursor() as cur:
        cur.execute(sql, (f"%{req.query}%", req.limit))
        columns = [desc[0] for desc in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]

    sources = list({r["source_url"] for r in rows})
    avg_confidence = sum(r["confidence"] for r in rows) / len(rows) if rows else 0.0
    freshest = max((r["fetched_at"] for r in rows), default=None)

    # Serialize datetime fields for JSON
    for row in rows:
        for key, val in row.items():
            if isinstance(val, datetime):
                row[key] = val.isoformat()

    return PhantomResponse(
        data=rows,
        sources=sources,
        confidence=round(avg_confidence, 3),
        freshness=freshest,
        related=[],
    )


@router.get("/entity/{entity_id}", response_model=PhantomResponse)
def get_entity(entity_id: str):
    pg = get_pg_connection()
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
        raise HTTPException(status_code=404, detail="Entity not found")

    data = dict(zip(columns, row))
    for key, val in data.items():
        if isinstance(val, datetime):
            data[key] = val.isoformat()

    return PhantomResponse(
        data=data,
        sources=[data.get("source_url", "")],
        confidence=data.get("confidence", 0.0),
        freshness=data.get("fetched_at"),
        related=[],
    )
```

**File:** `/Users/suryakoritala/phantombot/api/api/routes/search.py`

Replace the `semantic_search` function to support `include_markdown`. Full replacement:

```python
from datetime import datetime

from fastapi import APIRouter, Depends

from api.dependencies import get_qdrant_client, get_embedder, get_pg_connection, verify_api_key
from api.config import Config
from api.models import SearchRequest, PhantomResponse

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.post("/search", response_model=PhantomResponse)
def semantic_search(req: SearchRequest):
    embedder = get_embedder()
    qdrant = get_qdrant_client()

    embedding = embedder.encode(req.query).tolist()
    results = qdrant.query_points(
        collection_name=Config.QDRANT_COLLECTION,
        query=embedding,
        limit=req.limit,
    )

    data = []
    for point in results.points:
        item = {
            "id": point.id,
            "score": point.score,
            **point.payload,
        }
        data.append(item)

    # If include_markdown requested, fetch markdown for each page_id
    if req.include_markdown and data:
        page_ids = list({d.get("page_id") for d in data if d.get("page_id")})
        if page_ids:
            pg = get_pg_connection()
            with pg.cursor() as cur:
                placeholders = ",".join(["%s"] * len(page_ids))
                cur.execute(
                    f"SELECT id, markdown, markdown_meta FROM crawled_pages WHERE id::text IN ({placeholders})",
                    page_ids,
                )
                md_map = {}
                for row in cur.fetchall():
                    md_map[str(row[0])] = {"markdown": row[1], "markdown_meta": row[2]}

            for item in data:
                pid = item.get("page_id")
                if pid and pid in md_map:
                    item["markdown"] = md_map[pid]["markdown"]
                    item["markdown_meta"] = md_map[pid]["markdown_meta"]

    avg_confidence = (
        sum(d.get("confidence", d.get("score", 0)) for d in data) / len(data)
        if data else 0.0
    )

    return PhantomResponse(
        data=data,
        sources=[],
        confidence=round(avg_confidence, 3),
        freshness=None,
        related=[],
    )
```

**Verify:**

```bash
cd /Users/suryakoritala/phantombot/api
python -c "
from api.models import QueryRequest, SearchRequest
q = QueryRequest(query='test', include_markdown=True)
s = SearchRequest(query='test', include_markdown=True)
print(q.include_markdown, s.include_markdown)
"
```

**Commit:** `feat(api): add include_markdown parameter to query and search endpoints`

---

## Feature 2: MCP Server

### Task 11: MCP server project setup

- [ ] Create `mcp_server/pyproject.toml`
- [ ] Create `mcp_server/mcp_server/__init__.py`
- [ ] Create `mcp_server/mcp_server/config.py`
- [ ] Create `mcp_server/mcp_server/db.py`

**File:** `/Users/suryakoritala/phantombot/mcp_server/pyproject.toml`

```toml
[project]
name = "phantombot-mcp"
version = "0.1.0"
description = "Phantombot MCP server — AI agent tool integration"
requires-python = ">=3.12"
dependencies = [
    "mcp>=1.25.0",
    "psycopg[binary]>=3.1.0",
    "redis>=5.0.0",
    "qdrant-client>=1.9.0",
    "sentence-transformers>=3.0.0",
]

[project.scripts]
mcp_server = "mcp_server.server:main"

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
]

[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"
```

**File:** `/Users/suryakoritala/phantombot/mcp_server/mcp_server/__init__.py`

```python
```

(Empty file.)

**File:** `/Users/suryakoritala/phantombot/mcp_server/mcp_server/config.py`

```python
import os


class Config:
    MCP_TRANSPORT: str = os.getenv("MCP_TRANSPORT", "stdio")
    MCP_HTTP_HOST: str = os.getenv("MCP_HTTP_HOST", "0.0.0.0")
    MCP_HTTP_PORT: int = int(os.getenv("MCP_HTTP_PORT", "8001"))

    POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "localhost")
    POSTGRES_PORT: int = int(os.getenv("POSTGRES_PORT", "5432"))
    POSTGRES_DB: str = os.getenv("POSTGRES_DB", "phantombot")
    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "phantombot")
    POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "localdev")

    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")

    QDRANT_HOST: str = os.getenv("QDRANT_HOST", "localhost")
    QDRANT_PORT: int = int(os.getenv("QDRANT_PORT", "6335"))
    QDRANT_COLLECTION: str = os.getenv("QDRANT_COLLECTION", "phantombot")

    @classmethod
    def postgres_dsn(cls) -> str:
        return (
            f"host={cls.POSTGRES_HOST} port={cls.POSTGRES_PORT} "
            f"dbname={cls.POSTGRES_DB} user={cls.POSTGRES_USER} "
            f"password={cls.POSTGRES_PASSWORD}"
        )
```

**File:** `/Users/suryakoritala/phantombot/mcp_server/mcp_server/db.py`

```python
from functools import lru_cache

import psycopg
import redis
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer

from mcp_server.config import Config


@lru_cache
def get_pg_connection() -> psycopg.Connection:
    conn = psycopg.connect(Config.postgres_dsn())
    conn.autocommit = True
    return conn


@lru_cache
def get_redis_client() -> redis.Redis:
    return redis.from_url(Config.REDIS_URL)


@lru_cache
def get_qdrant_client() -> QdrantClient:
    return QdrantClient(host=Config.QDRANT_HOST, port=Config.QDRANT_PORT)


@lru_cache
def get_embedder() -> SentenceTransformer:
    return SentenceTransformer("all-MiniLM-L6-v2")
```

**Verify:**

```bash
cd /Users/suryakoritala/phantombot/mcp_server
pip install -e ".[dev]"
python -c "from mcp_server.config import Config; print(Config.postgres_dsn())"
```

**Commit:** `feat(mcp): scaffold mcp_server package with config and db modules`

---

### Task 12: MCP server with crawl_url tool

- [ ] Create `mcp_server/mcp_server/server.py` with `crawl_url` tool
- [ ] Create `mcp_server/tests/__init__.py`
- [ ] Create `mcp_server/tests/test_crawl_url.py`

**File:** `/Users/suryakoritala/phantombot/mcp_server/tests/__init__.py`

```python
```

(Empty file.)

**File:** `/Users/suryakoritala/phantombot/mcp_server/tests/test_crawl_url.py`

```python
import uuid
from unittest.mock import MagicMock, patch

import pytest

from mcp_server.server import crawl_url


class TestCrawlUrl:
    """Tests for the crawl_url MCP tool."""

    @patch("mcp_server.server.db")
    def test_valid_url_returns_job_id(self, mock_db):
        mock_pg = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (uuid.uuid4(),)
        mock_pg.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_pg.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.get_pg_connection.return_value = mock_pg
        mock_db.get_redis_client.return_value = MagicMock()

        result = crawl_url(url="https://example.com", max_depth=3)

        assert "job_id" in result
        assert result["url"] == "https://example.com"
        assert result["status"] == "pending"

    @patch("mcp_server.server.db")
    def test_invalid_scheme_raises(self, mock_db):
        result = crawl_url(url="ftp://example.com", max_depth=3)
        assert "error" in result

    @patch("mcp_server.server.db")
    def test_no_scheme_raises(self, mock_db):
        result = crawl_url(url="example.com", max_depth=3)
        assert "error" in result

    @patch("mcp_server.server.db")
    def test_max_depth_clamped(self, mock_db):
        mock_pg = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (uuid.uuid4(),)
        mock_pg.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_pg.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.get_pg_connection.return_value = mock_pg
        mock_db.get_redis_client.return_value = MagicMock()

        result = crawl_url(url="https://example.com", max_depth=99)
        assert "job_id" in result  # Should clamp, not error
```

**File:** `/Users/suryakoritala/phantombot/mcp_server/mcp_server/server.py`

```python
import json
import uuid
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP

from mcp_server import db
from mcp_server.config import Config

mcp = FastMCP(
    "Phantombot",
    description=(
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


def main():
    """Entry point for the MCP server."""
    if Config.MCP_TRANSPORT == "streamable-http":
        mcp.run(transport="streamable-http", host=Config.MCP_HTTP_HOST, port=Config.MCP_HTTP_PORT)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
```

**Verify (tests should pass with mocks):**

```bash
cd /Users/suryakoritala/phantombot/mcp_server
python -m pytest tests/test_crawl_url.py -v
```

**Commit:** `feat(mcp): add MCP server with crawl_url tool`

---

### Task 13: query_knowledge tool

- [ ] Create `mcp_server/tests/test_query_knowledge.py`
- [ ] Add `query_knowledge` tool to `server.py`

**File:** `/Users/suryakoritala/phantombot/mcp_server/tests/test_query_knowledge.py`

```python
from unittest.mock import MagicMock, patch
from datetime import datetime

import pytest

from mcp_server.server import query_knowledge


class TestQueryKnowledge:
    """Tests for the query_knowledge MCP tool."""

    @patch("mcp_server.server.db")
    def test_returns_results_list(self, mock_db):
        mock_pg = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("entity_text",), ("entity_type",), ("confidence",),
            ("source_url",), ("fetched_at",),
        ]
        mock_cursor.fetchall.return_value = [
            ("Google", "ORG", 0.9, "https://example.com", datetime(2026, 1, 1)),
        ]
        mock_pg.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_pg.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.get_pg_connection.return_value = mock_pg

        result = query_knowledge(query="Google", limit=10)

        assert "results" in result
        assert "total" in result
        assert len(result["results"]) == 1
        assert result["results"][0]["entity_text"] == "Google"

    @patch("mcp_server.server.db")
    def test_empty_results(self, mock_db):
        mock_pg = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("entity_text",), ("entity_type",), ("confidence",),
            ("source_url",), ("fetched_at",),
        ]
        mock_cursor.fetchall.return_value = []
        mock_pg.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_pg.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.get_pg_connection.return_value = mock_pg

        result = query_knowledge(query="nonexistent", limit=10)

        assert result["results"] == []
        assert result["total"] == 0

    @patch("mcp_server.server.db")
    def test_limit_clamped(self, mock_db):
        mock_pg = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("entity_text",), ("entity_type",), ("confidence",),
            ("source_url",), ("fetched_at",),
        ]
        mock_cursor.fetchall.return_value = []
        mock_pg.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_pg.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.get_pg_connection.return_value = mock_pg

        result = query_knowledge(query="test", limit=999)
        # Should not error -- limit is clamped internally
        assert isinstance(result, dict)
```

Add to `mcp_server/mcp_server/server.py` (after `crawl_url` function, before `main`):

```python
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
```

**Verify:**

```bash
cd /Users/suryakoritala/phantombot/mcp_server
python -m pytest tests/test_query_knowledge.py -v
```

**Commit:** `feat(mcp): add query_knowledge tool`

---

### Task 14: search_semantic tool

- [ ] Create `mcp_server/tests/test_search_semantic.py`
- [ ] Add `search_semantic` tool to `server.py`

**File:** `/Users/suryakoritala/phantombot/mcp_server/tests/test_search_semantic.py`

```python
from unittest.mock import MagicMock, patch

import pytest

from mcp_server.server import search_semantic


class MockPoint:
    def __init__(self, id, score, payload):
        self.id = id
        self.score = score
        self.payload = payload


class MockQueryResponse:
    def __init__(self, points):
        self.points = points


class TestSearchSemantic:
    """Tests for the search_semantic MCP tool."""

    @patch("mcp_server.server.db")
    def test_returns_results(self, mock_db):
        mock_embedder = MagicMock()
        mock_embedder.encode.return_value = MagicMock(tolist=lambda: [0.1] * 384)
        mock_db.get_embedder.return_value = mock_embedder

        mock_qdrant = MagicMock()
        mock_qdrant.query_points.return_value = MockQueryResponse([
            MockPoint(
                id="abc-123",
                score=0.95,
                payload={
                    "type": "entity",
                    "page_id": "page-1",
                    "entity_type": "ORG",
                    "entity_text": "Google",
                    "confidence": 0.9,
                },
            )
        ])
        mock_db.get_qdrant_client.return_value = mock_qdrant

        result = search_semantic(query="large tech companies", limit=10)

        assert "results" in result
        assert "total" in result
        assert len(result["results"]) == 1
        assert result["results"][0]["entity_text"] == "Google"
        assert result["results"][0]["score"] == 0.95

    @patch("mcp_server.server.db")
    def test_empty_results(self, mock_db):
        mock_embedder = MagicMock()
        mock_embedder.encode.return_value = MagicMock(tolist=lambda: [0.1] * 384)
        mock_db.get_embedder.return_value = mock_embedder

        mock_qdrant = MagicMock()
        mock_qdrant.query_points.return_value = MockQueryResponse([])
        mock_db.get_qdrant_client.return_value = mock_qdrant

        result = search_semantic(query="nonexistent stuff", limit=10)

        assert result["results"] == []
        assert result["total"] == 0

    @patch("mcp_server.server.db")
    def test_limit_clamped(self, mock_db):
        mock_embedder = MagicMock()
        mock_embedder.encode.return_value = MagicMock(tolist=lambda: [0.1] * 384)
        mock_db.get_embedder.return_value = mock_embedder

        mock_qdrant = MagicMock()
        mock_qdrant.query_points.return_value = MockQueryResponse([])
        mock_db.get_qdrant_client.return_value = mock_qdrant

        result = search_semantic(query="test", limit=999)
        assert isinstance(result, dict)
        # Verify qdrant was called with clamped limit
        call_args = mock_qdrant.query_points.call_args
        assert call_args.kwargs.get("limit", call_args[1].get("limit", 50)) <= 50
```

Add to `mcp_server/mcp_server/server.py` (after `query_knowledge`, before `main`):

```python
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
```

**Verify:**

```bash
cd /Users/suryakoritala/phantombot/mcp_server
python -m pytest tests/test_search_semantic.py -v
```

**Commit:** `feat(mcp): add search_semantic tool`

---

### Task 15: get_entity tool

- [ ] Create `mcp_server/tests/test_get_entity.py`
- [ ] Add `get_entity` tool to `server.py`

**File:** `/Users/suryakoritala/phantombot/mcp_server/tests/test_get_entity.py`

```python
from unittest.mock import MagicMock, patch
from datetime import datetime

import pytest

from mcp_server.server import get_entity


class TestGetEntity:
    """Tests for the get_entity MCP tool."""

    @patch("mcp_server.server.db")
    def test_returns_entity(self, mock_db):
        mock_pg = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("id",), ("entity_text",), ("entity_type",),
            ("confidence",), ("source_url",), ("fetched_at",),
        ]
        mock_cursor.fetchone.return_value = (
            "abc-123", "Google", "ORG", 0.9,
            "https://example.com", datetime(2026, 1, 1),
        )
        mock_pg.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_pg.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.get_pg_connection.return_value = mock_pg

        result = get_entity(entity_id="abc-123")

        assert result["entity_text"] == "Google"
        assert result["entity_type"] == "ORG"
        assert result["confidence"] == 0.9

    @patch("mcp_server.server.db")
    def test_not_found(self, mock_db):
        mock_pg = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_pg.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_pg.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.get_pg_connection.return_value = mock_pg

        result = get_entity(entity_id="nonexistent")

        assert "error" in result
```

Add to `mcp_server/mcp_server/server.py` (after `search_semantic`, before `main`):

```python
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
```

**Verify:**

```bash
cd /Users/suryakoritala/phantombot/mcp_server
python -m pytest tests/test_get_entity.py -v
```

**Commit:** `feat(mcp): add get_entity tool`

---

### Task 16: get_page_markdown tool

- [ ] Create `mcp_server/tests/test_get_page_markdown.py`
- [ ] Add `get_page_markdown` tool to `server.py`

**File:** `/Users/suryakoritala/phantombot/mcp_server/tests/test_get_page_markdown.py`

```python
from unittest.mock import MagicMock, patch
from datetime import datetime

import pytest

from mcp_server.server import get_page_markdown


class TestGetPageMarkdown:
    """Tests for the get_page_markdown MCP tool."""

    @patch("mcp_server.server.db")
    def test_returns_markdown(self, mock_db):
        mock_pg = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("id",), ("url",), ("markdown",), ("markdown_meta",), ("fetched_at",),
        ]
        mock_cursor.fetchone.return_value = (
            "page-1",
            "https://example.com/article",
            "# Article Title\n\nContent here...",
            {"word_count": 50, "has_code": False, "has_tables": False, "pipeline_version": "markdown-v1"},
            datetime(2026, 1, 1),
        )
        mock_pg.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_pg.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.get_pg_connection.return_value = mock_pg

        result = get_page_markdown(page_id="page-1")

        assert result["url"] == "https://example.com/article"
        assert "# Article Title" in result["markdown"]
        assert result["meta"]["word_count"] == 50

    @patch("mcp_server.server.db")
    def test_page_not_found(self, mock_db):
        mock_pg = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_pg.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_pg.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.get_pg_connection.return_value = mock_pg

        result = get_page_markdown(page_id="nonexistent")

        assert "error" in result

    @patch("mcp_server.server.db")
    def test_no_markdown_available(self, mock_db):
        mock_pg = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("id",), ("url",), ("markdown",), ("markdown_meta",), ("fetched_at",),
        ]
        mock_cursor.fetchone.return_value = (
            "page-1", "https://example.com", None, None, datetime(2026, 1, 1),
        )
        mock_pg.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_pg.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.get_pg_connection.return_value = mock_pg

        result = get_page_markdown(page_id="page-1")

        assert "error" in result
```

Add to `mcp_server/mcp_server/server.py` (after `get_entity`, before `main`):

```python
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
```

**Verify:**

```bash
cd /Users/suryakoritala/phantombot/mcp_server
python -m pytest tests/test_get_page_markdown.py -v
```

**Commit:** `feat(mcp): add get_page_markdown tool`

---

### Task 17: phantombot://stats resource

- [ ] Create `mcp_server/tests/test_resources.py` (stats portion)
- [ ] Add `get_stats` resource to `server.py`

**File:** `/Users/suryakoritala/phantombot/mcp_server/tests/test_resources.py`

```python
import json
from unittest.mock import MagicMock, patch
from datetime import datetime

import pytest

from mcp_server.server import get_stats, get_recent


class TestStatsResource:
    """Tests for the phantombot://stats resource."""

    @patch("mcp_server.server.db")
    def test_returns_stats_json(self, mock_db):
        mock_pg = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (100, 500, 50, 90, 10, datetime(2026, 4, 12))
        mock_pg.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_pg.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.get_pg_connection.return_value = mock_pg

        result = get_stats()
        data = json.loads(result)

        assert data["total_pages"] == 100
        assert data["total_entities"] == 500
        assert data["total_schemas"] == 50
        assert data["pages_with_markdown"] == 90
        assert data["domains_crawled"] == 10

    @patch("mcp_server.server.db")
    def test_handles_empty_database(self, mock_db):
        mock_pg = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (0, 0, 0, 0, 0, None)
        mock_pg.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_pg.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.get_pg_connection.return_value = mock_pg

        result = get_stats()
        data = json.loads(result)

        assert data["total_pages"] == 0
        assert data["last_crawl_at"] is None


class TestRecentResource:
    """Tests for the phantombot://recent resource (placeholder -- implemented in Task 18)."""
    pass
```

Add to `mcp_server/mcp_server/server.py` (after the last tool, before `main`):

```python
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
```

Note: `json` is already imported at the top of `server.py`.

**Verify:**

```bash
cd /Users/suryakoritala/phantombot/mcp_server
python -m pytest tests/test_resources.py::TestStatsResource -v
```

**Commit:** `feat(mcp): add phantombot://stats resource`

---

### Task 18: phantombot://recent resource

- [ ] Complete `TestRecentResource` in `test_resources.py`
- [ ] Add `get_recent` resource to `server.py`

**File:** `/Users/suryakoritala/phantombot/mcp_server/tests/test_resources.py`

Replace the `TestRecentResource` class:

```python
class TestRecentResource:
    """Tests for the phantombot://recent resource."""

    @patch("mcp_server.server.db")
    def test_returns_recent_pages(self, mock_db):
        mock_pg = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("id",), ("url",), ("domain",), ("fetched_at",),
            ("has_markdown",), ("entity_count",), ("schema_count",),
        ]
        mock_cursor.fetchall.return_value = [
            (
                "page-1", "https://example.com/article", "example.com",
                datetime(2026, 4, 12), True, 5, 1,
            ),
        ]
        mock_pg.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_pg.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.get_pg_connection.return_value = mock_pg

        result = get_recent()
        data = json.loads(result)

        assert "pages" in data
        assert len(data["pages"]) == 1
        assert data["pages"][0]["url"] == "https://example.com/article"
        assert data["pages"][0]["has_markdown"] is True

    @patch("mcp_server.server.db")
    def test_handles_empty_database(self, mock_db):
        mock_pg = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("id",), ("url",), ("domain",), ("fetched_at",),
            ("has_markdown",), ("entity_count",), ("schema_count",),
        ]
        mock_cursor.fetchall.return_value = []
        mock_pg.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_pg.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.get_pg_connection.return_value = mock_pg

        result = get_recent()
        data = json.loads(result)

        assert data["pages"] == []
```

Add to `mcp_server/mcp_server/server.py` (after `get_stats`, before `main`):

```python
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
```

**Verify:**

```bash
cd /Users/suryakoritala/phantombot/mcp_server
python -m pytest tests/test_resources.py -v
```

**Commit:** `feat(mcp): add phantombot://recent resource`

---

### Task 19: Comprehensive MCP server tests

- [ ] Create `mcp_server/tests/test_server_integration.py`

This tests the full server object -- tool listing, resource listing, and validates all tools/resources are registered.

**File:** `/Users/suryakoritala/phantombot/mcp_server/tests/test_server_integration.py`

```python
"""Integration tests for the MCP server -- verifies all tools and resources are registered."""

import pytest

from mcp_server.server import mcp


class TestServerRegistration:
    """Verify all tools and resources are registered on the FastMCP instance."""

    def test_server_name(self):
        assert mcp.name == "Phantombot"

    def test_crawl_url_tool_registered(self):
        tools = mcp._tool_manager._tools
        assert "crawl_url" in tools

    def test_query_knowledge_tool_registered(self):
        tools = mcp._tool_manager._tools
        assert "query_knowledge" in tools

    def test_search_semantic_tool_registered(self):
        tools = mcp._tool_manager._tools
        assert "search_semantic" in tools

    def test_get_entity_tool_registered(self):
        tools = mcp._tool_manager._tools
        assert "get_entity" in tools

    def test_get_page_markdown_tool_registered(self):
        tools = mcp._tool_manager._tools
        assert "get_page_markdown" in tools

    def test_all_five_tools_registered(self):
        tools = mcp._tool_manager._tools
        expected = {"crawl_url", "query_knowledge", "search_semantic", "get_entity", "get_page_markdown"}
        assert expected.issubset(set(tools.keys()))

    def test_stats_resource_registered(self):
        resources = mcp._resource_manager._resources
        assert "phantombot://stats" in resources

    def test_recent_resource_registered(self):
        resources = mcp._resource_manager._resources
        assert "phantombot://recent" in resources

    def test_both_resources_registered(self):
        resources = mcp._resource_manager._resources
        expected = {"phantombot://stats", "phantombot://recent"}
        assert expected.issubset(set(resources.keys()))
```

**Verify:**

```bash
cd /Users/suryakoritala/phantombot/mcp_server
python -m pytest tests/test_server_integration.py -v
```

**Commit:** `test(mcp): add server integration tests for tool and resource registration`

---

### Task 20: Docker Compose service + Dockerfile for MCP server

- [ ] Create `mcp_server/Dockerfile`
- [ ] Update `docker-compose.yml` to add mcp-server service

**File:** `/Users/suryakoritala/phantombot/mcp_server/Dockerfile`

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY mcp_server/ mcp_server/

RUN pip install --no-cache-dir .

EXPOSE 8001

CMD ["mcp_server"]
```

**File:** `/Users/suryakoritala/phantombot/docker-compose.yml`

Add the `mcp-server` service after the `qdrant` service block (before `volumes:`):

```yaml
  mcp-server:
    build: ./mcp_server
    ports:
      - "8001:8001"
    environment:
      MCP_TRANSPORT: streamable-http
      MCP_HTTP_PORT: "8001"
      POSTGRES_HOST: postgres
      POSTGRES_PORT: "5432"
      POSTGRES_DB: phantombot
      POSTGRES_USER: phantombot
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-localdev}
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

**Verify:**

```bash
cd /Users/suryakoritala/phantombot
# Validate docker-compose syntax
docker compose config --quiet 2>&1 || echo "docker compose config check"
cat mcp_server/Dockerfile
```

**Commit:** `feat(mcp): add Dockerfile and Docker Compose service for MCP server`

---

## Complete File Listing

### New files created in this plan:

| # | File | Task |
|---|------|------|
| 1 | `/Users/suryakoritala/phantombot/store/migrations/002_add_markdown.sql` | Task 1 |
| 2 | `/Users/suryakoritala/phantombot/fixtures/article_page.html` | Task 4 |
| 3 | `/Users/suryakoritala/phantombot/fixtures/code_doc_page.html` | Task 4 |
| 4 | `/Users/suryakoritala/phantombot/intelligence/tests/test_markdown_pipeline.py` | Task 5 |
| 5 | `/Users/suryakoritala/phantombot/intelligence/intelligence/pipelines/markdown.py` | Task 6 |
| 6 | `/Users/suryakoritala/phantombot/api/api/routes/page.py` | Task 9 |
| 7 | `/Users/suryakoritala/phantombot/mcp_server/pyproject.toml` | Task 11 |
| 8 | `/Users/suryakoritala/phantombot/mcp_server/mcp_server/__init__.py` | Task 11 |
| 9 | `/Users/suryakoritala/phantombot/mcp_server/mcp_server/config.py` | Task 11 |
| 10 | `/Users/suryakoritala/phantombot/mcp_server/mcp_server/db.py` | Task 11 |
| 11 | `/Users/suryakoritala/phantombot/mcp_server/mcp_server/server.py` | Tasks 12-18 |
| 12 | `/Users/suryakoritala/phantombot/mcp_server/tests/__init__.py` | Task 12 |
| 13 | `/Users/suryakoritala/phantombot/mcp_server/tests/test_crawl_url.py` | Task 12 |
| 14 | `/Users/suryakoritala/phantombot/mcp_server/tests/test_query_knowledge.py` | Task 13 |
| 15 | `/Users/suryakoritala/phantombot/mcp_server/tests/test_search_semantic.py` | Task 14 |
| 16 | `/Users/suryakoritala/phantombot/mcp_server/tests/test_get_entity.py` | Task 15 |
| 17 | `/Users/suryakoritala/phantombot/mcp_server/tests/test_get_page_markdown.py` | Task 16 |
| 18 | `/Users/suryakoritala/phantombot/mcp_server/tests/test_resources.py` | Tasks 17-18 |
| 19 | `/Users/suryakoritala/phantombot/mcp_server/tests/test_server_integration.py` | Task 19 |
| 20 | `/Users/suryakoritala/phantombot/mcp_server/Dockerfile` | Task 20 |

### Existing files modified in this plan:

| # | File | Task |
|---|------|------|
| 1 | `/Users/suryakoritala/phantombot/intelligence/pyproject.toml` | Task 2 |
| 2 | `/Users/suryakoritala/phantombot/intelligence/intelligence/models.py` | Task 3 |
| 3 | `/Users/suryakoritala/phantombot/intelligence/tests/conftest.py` | Task 5 |
| 4 | `/Users/suryakoritala/phantombot/intelligence/intelligence/store.py` | Task 7 |
| 5 | `/Users/suryakoritala/phantombot/intelligence/intelligence/worker.py` | Task 8 |
| 6 | `/Users/suryakoritala/phantombot/api/api/models.py` | Tasks 9, 10 |
| 7 | `/Users/suryakoritala/phantombot/api/api/main.py` | Task 9 |
| 8 | `/Users/suryakoritala/phantombot/api/api/routes/query.py` | Task 10 |
| 9 | `/Users/suryakoritala/phantombot/api/api/routes/search.py` | Task 10 |
| 10 | `/Users/suryakoritala/phantombot/docker-compose.yml` | Task 20 |

---

## Running All Tests

After all tasks are complete, run the full test suites:

```bash
# Intelligence layer tests (markdown pipeline)
cd /Users/suryakoritala/phantombot/intelligence
python -m pytest tests/test_markdown_pipeline.py -v

# MCP server tests (all tools and resources)
cd /Users/suryakoritala/phantombot/mcp_server
python -m pytest tests/ -v
```

## Final Commit Summary

| Task | Commit Message |
|------|---------------|
| 1 | `feat(store): add markdown and markdown_meta columns to crawled_pages` |
| 2 | `feat(intelligence): add trafilatura and markdownify dependencies` |
| 3 | `feat(intelligence): add MarkdownResult model` |
| 4 | `test(fixtures): add article and code doc HTML fixtures for markdown pipeline` |
| 5 | `test(intelligence): add markdown pipeline tests (red phase)` |
| 6 | `feat(intelligence): implement MarkdownPipeline with trafilatura + markdownify` |
| 7 | `feat(intelligence): add save_markdown and get_page_markdown to KnowledgeStore` |
| 8 | `feat(intelligence): integrate MarkdownPipeline into Worker processing loop` |
| 9 | `feat(api): add GET /page/{page_id}/markdown endpoint` |
| 10 | `feat(api): add include_markdown parameter to query and search endpoints` |
| 11 | `feat(mcp): scaffold mcp_server package with config and db modules` |
| 12 | `feat(mcp): add MCP server with crawl_url tool` |
| 13 | `feat(mcp): add query_knowledge tool` |
| 14 | `feat(mcp): add search_semantic tool` |
| 15 | `feat(mcp): add get_entity tool` |
| 16 | `feat(mcp): add get_page_markdown tool` |
| 17 | `feat(mcp): add phantombot://stats resource` |
| 18 | `feat(mcp): add phantombot://recent resource` |
| 19 | `test(mcp): add server integration tests for tool and resource registration` |
| 20 | `feat(mcp): add Dockerfile and Docker Compose service for MCP server` |
