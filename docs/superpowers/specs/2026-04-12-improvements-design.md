# Phantombot Improvements Design Spec

> Design spec for 10 improvements identified during end-to-end testing across 11 sites. Prioritized P0-P3 based on impact.

## Background

End-to-end benchmarks across 11 sites revealed critical quality and completeness gaps:

- **Entity extraction F1 = 0.04** with `en_core_web_sm` model
- **Garbage entities** like "Elon Musk's", "Elon Musk-", "Elon Musk Starts" polluting results
- **API-initiated crawls never execute** because the Go crawler only reads seed URLs from CLI args, not from the `phantombot:crawl_requests` Redis stream
- **24% of benchmark URLs failed** due to JS-heavy rendering
- **Entity duplicates** like "Tesla" / "Tesla Inc" / "Tesla, Inc." stored as separate entities
- **0% OpenAPI detection rate** on real-world API docs using Swagger UI or Redoc
- **13 of 24 crawled pages** have no markdown due to being crawled before the pipeline existed

---

## Improvement 1: Upgrade SpaCy Model (P0)

### Problem
`en_core_web_sm` is a small statistical model. On our benchmark, it produces F1=0.04 for entity extraction. The transformer-based `en_core_web_trf` dramatically improves accuracy.

### Current State
- `intelligence/intelligence/config.py` line 19: `SPACY_MODEL: str = os.getenv("SPACY_MODEL", "en_core_web_sm")`
- `intelligence/intelligence/pipelines/entity.py` line 15: `self._nlp = spacy.load(model_name or Config.SPACY_MODEL)`
- `intelligence/pyproject.toml` lists `spacy>=3.7.0` but no transformer dependencies

### Changes

**File: `intelligence/intelligence/config.py`**
- Change default `SPACY_MODEL` from `"en_core_web_sm"` to `"en_core_web_trf"`

**File: `intelligence/pyproject.toml`**
- Add `spacy-transformers>=1.3.0` to dependencies
- Add `spacy[transformers]` or ensure `torch` is available as a transitive dep

**File: `intelligence/intelligence/pipelines/entity.py`**
- Update `pipeline_version` from `"entity-v1"` to `"entity-v2"` to track which model generated entities
- Adjust confidence heuristic: transformer model provides higher base confidence

**File: `Dockerfile` / setup docs**
- Add `python -m spacy download en_core_web_trf` to build steps

### Integration
No API or worker changes needed. The `EntityPipeline.__init__` already accepts model name override. The config change propagates automatically.

### Trade-offs
- `en_core_web_trf` is ~500MB vs ~12MB for `en_core_web_sm`
- Inference is slower (~5-10x) but still fast enough for our per-page pipeline
- Users on resource-constrained systems can set `SPACY_MODEL=en_core_web_sm` to opt out

---

## Improvement 2: Entity Post-Processing (P0)

### Problem
Even with a better model, SpaCy produces dirty entities: trailing possessives ("Elon Musk's"), trailing punctuation ("Tesla,"), partial phrases ("Elon Musk Starts"), single-character garbage.

### Current State
`entity.py` does only `ent.text.strip()` and dedup by exact `(text, label)` pair. No cleaning, no validation, no filtering.

### Changes

**File: `intelligence/intelligence/pipelines/entity_cleaner.py` (NEW)**

A post-processing module with these cleaning steps:
1. **Strip possessives** -- remove trailing `'s`, `'s`
2. **Strip trailing punctuation** -- remove trailing `,`, `.`, `-`, `:`, `;`, `!`, `?`, `"`
3. **Strip leading punctuation** -- remove leading `-`, `"`, `(`
4. **Min/max length filter** -- discard entities < 2 chars or > 200 chars
5. **Stopword filter** -- discard entities that are entirely common words (e.g., "The", "First")
6. **Garbage pattern filter** -- discard entities matching known garbage patterns (all-caps single words under 3 chars, entities that are just numbers)
7. **Normalization** -- collapse internal whitespace

```python
class EntityCleaner:
    def clean(self, entities: list[ExtractedEntity]) -> list[ExtractedEntity]: ...
    def _clean_text(self, text: str) -> str: ...
    def _is_valid(self, text: str, entity_type: str) -> bool: ...
```

**File: `intelligence/intelligence/pipelines/entity.py`**
- Import and apply `EntityCleaner` after SpaCy extraction
- Re-dedup after cleaning (cleaning may merge previously-distinct strings)

### Integration
The cleaner is called inside `EntityPipeline.extract()` before returning results. No changes to worker or store.

---

## Improvement 3: Connect Crawl API to Go Crawler (P0)

### Problem
`POST /crawl` writes to `phantombot:crawl_requests` Redis stream (see `api/api/routes/crawl.py` line 35). But the Go crawler (`crawler/cmd/phantombot/main.go`) only reads seed URLs from the `-urls` CLI flag. API-initiated crawls never execute.

### Current State
- API writes: `rdb.xadd("phantombot:crawl_requests", {"url": ..., "max_depth": ..., "job_id": ...})`
- Go crawler: `seedURLs := flag.String("urls", "", ...)` -- only CLI input
- Go config: no `CrawlRequestStream` field
- Go models: `CrawlRequest` struct exists in `crawler/pkg/models/models.go` but is unused

### Changes

**File: `crawler/internal/config/config.go`**
- Add `CrawlRequestStream string` field, default `"phantombot:crawl_requests"`
- Add `CrawlRequestGroup string` field, default `"crawler"`

**File: `crawler/internal/consumer/consumer.go` (NEW)**

A Redis Streams consumer that:
1. Creates consumer group on `phantombot:crawl_requests` if not exists
2. Reads messages with `XREADGROUP`
3. Parses each message into a `models.CrawlRequest`
4. Pushes the URL into the existing `Frontier`
5. Acknowledges the message with `XACK`

```go
type Consumer struct {
    rdb      *redis.Client
    stream   string
    group    string
    consumer string
    frontier *frontier.Frontier
}

func (c *Consumer) Run(ctx context.Context) error { ... }
```

**File: `crawler/internal/consumer/consumer_test.go` (NEW)**
- Test that a message written to the stream gets consumed and pushed to frontier

**File: `crawler/cmd/phantombot/main.go`**
- Make `-urls` flag optional (not required when running in stream-consumer mode)
- Start `consumer.Run(ctx)` in a goroutine alongside the existing crawl workers
- When the consumer pushes URLs to the frontier, the existing worker goroutines pick them up naturally
- Crawler stays alive waiting for stream messages instead of exiting when frontier is empty

### Integration
The frontier is shared between CLI-seeded URLs and stream-consumed URLs. Worker goroutines don't care where URLs come from. The main loop needs to not exit when frontier is empty if the consumer is running -- add a check or use a "done" channel.

### Message Format Compatibility
The API writes: `{"url": "...", "max_depth": "3", "job_id": "uuid"}`
The consumer parses these fields and pushes `url` to frontier with `max_depth` override and `job_id` propagation.

---

## Improvement 4: Entity Resolution / Dedup (P1)

### Problem
"Tesla", "Tesla Inc", "Tesla, Inc.", "Tesla Motors" are stored as 4 separate entities. Agents querying for "Tesla" miss related mentions.

### Current State
`entity.py` deduplicates by exact `(text, label)` match within a single page. No cross-page or fuzzy dedup.

### Changes

**File: `intelligence/intelligence/pipelines/entity_resolver.py` (NEW)**

Entity resolution layer that:
1. **Normalize** -- lowercase, strip punctuation, remove common suffixes ("Inc", "Corp", "LLC", "Ltd")
2. **Fuzzy match** -- Levenshtein distance or token overlap ratio
3. **Canonical form** -- pick the most common form as canonical, store alternatives as aliases
4. **Group ID** -- assign a `canonical_entity_id` to each entity

```python
class EntityResolver:
    def resolve(self, entities: list[ExtractedEntity]) -> list[ResolvedEntity]: ...
    def _normalize(self, text: str) -> str: ...
    def _similarity(self, a: str, b: str) -> float: ...
```

**File: `intelligence/intelligence/models.py`**
- Add `ResolvedEntity` model with `canonical_text`, `canonical_id`, `aliases`

**File: `intelligence/intelligence/store.py`**
- Add `save_resolved_entities()` method
- Add `canonical_entities` table query support

**File: `store/migrations/002_entity_resolution.sql` (NEW)**
- Add `canonical_entities` table: `id`, `canonical_text`, `entity_type`, `aliases` (JSONB)
- Add `canonical_entity_id` FK column to `entities` table

**File: `intelligence/intelligence/worker.py`**
- After entity extraction, run entity resolution before saving

### Integration
Resolution runs as a post-processing step after extraction, before storage. The worker pipeline becomes: extract -> clean -> resolve -> save.

---

## Improvement 5: JS Rendering via Playwright (P1)

### Problem
24% of benchmark URLs failed because they are JS-heavy SPAs. The Go crawler does static HTTP fetches only.

### Current State
- `fetcher.go` does `http.NewRequest("GET", ...)` -- no JS execution
- `models.CrawlResult` has a `Rendered bool` field (always `false` currently)
- Design spec mentions Playwright sidecar but it was not implemented in v1

### Changes

**File: `playwright-service/server.py` (NEW)**

A lightweight HTTP sidecar that:
1. Exposes `POST /render` endpoint
2. Accepts `{"url": "...", "timeout": 30000}`
3. Uses Playwright to load the page, wait for network idle, return rendered HTML
4. Returns `{"html": "...", "status_code": 200, "rendered": true}`

```python
# FastAPI app with single endpoint
@app.post("/render")
async def render(request: RenderRequest) -> RenderResponse: ...
```

**File: `playwright-service/pyproject.toml` (NEW)**
- Dependencies: `fastapi`, `uvicorn`, `playwright`

**File: `playwright-service/Dockerfile` (NEW)**
- Install Playwright + browsers

**File: `crawler/internal/renderer/renderer.go` (NEW)**

Go client that calls the Playwright sidecar:
```go
type Renderer struct {
    baseURL string
    client  *http.Client
}

func (r *Renderer) Render(rawURL string) (*models.CrawlResult, error) { ... }
```

**File: `crawler/internal/config/config.go`**
- Add `RendererURL string` field, default `"http://localhost:3001"`
- Add `EnableRenderer bool` field, default `false`

**File: `crawler/cmd/phantombot/main.go`**
- After a static fetch returns suspicious content (very short HTML, or specific JS framework markers), optionally re-fetch via renderer
- Set `result.Rendered = true` for renderer-fetched pages

**File: `docker-compose.yml`**
- Add `playwright-service` container

### Integration
The renderer is opt-in. When enabled, the crawler tries static fetch first. If the response looks like a JS shell (< 500 chars of text content, or contains `<div id="root"></div>` / `<div id="app"></div>` with minimal other content), it falls back to the Playwright sidecar. This avoids the cost of rendering for pages that don't need it.

---

## Improvement 6: Anti-Bot Basics (P1)

### Problem
Many sites return 403s because the crawler uses a single static user-agent string and no stealth headers.

### Current State
- `config.go` line 25: `UserAgent: getEnv("CRAWLER_USER_AGENT", "Phantombot/1.0")`
- `fetcher.go` line 42: `req.Header.Set("User-Agent", f.userAgent)` -- single header, no rotation

### Changes

**File: `crawler/internal/fetcher/useragents.go` (NEW)**

A user-agent rotation pool:
```go
var UserAgents = []string{
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ...",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 ...",
    // 8-10 common browser UAs
}

func RandomUserAgent() string { ... }
```

**File: `crawler/internal/fetcher/fetcher.go`**
- Add stealth headers: `Accept`, `Accept-Language`, `Accept-Encoding`, `Connection`
- If `userAgent` is `"rotate"`, pick from pool per-request
- Add configurable proxy support via `http.Transport.Proxy`

**File: `crawler/internal/config/config.go`**
- Add `ProxyURL string` field (optional)
- Add `RotateUserAgent bool` field, default `false`

**File: `crawler/internal/fetcher/fetcher_test.go`**
- Test that stealth headers are sent
- Test that user-agent rotation produces different UAs

### Integration
Backwards compatible. Default behavior unchanged. Enable rotation with `CRAWLER_ROTATE_USER_AGENT=true`. Proxy configured with `CRAWLER_PROXY_URL=http://...`.

---

## Improvement 7: Better Entity Typing (P2)

### Problem
SpaCy misclassifies well-known entities: "Claude" as PERSON (should be PRODUCT), "Python" as PERSON (should be PRODUCT), etc.

### Current State
No type override logic in `entity.py`. Raw SpaCy labels are used directly.

### Changes

**File: `intelligence/intelligence/pipelines/entity_typing_rules.py` (NEW)**

A rule-based override layer:
```python
# Maps (text_pattern, current_type) -> corrected_type
TYPE_OVERRIDES: dict[tuple[str, str], str] = {
    ("Claude", "PERSON"): "PRODUCT",
    ("Python", "PERSON"): "PRODUCT",
    ("Java", "PERSON"): "PRODUCT",
    ("Rust", "PERSON"): "PRODUCT",
    ("Go", "PERSON"): "PRODUCT",
    ("Ruby", "PERSON"): "PRODUCT",
    ("Swift", "PERSON"): "PRODUCT",
    ("Kotlin", "PERSON"): "PRODUCT",
    # More rules as discovered
}

class EntityTypingRules:
    def apply(self, entities: list[ExtractedEntity]) -> list[ExtractedEntity]: ...
```

**File: `intelligence/intelligence/pipelines/entity.py`**
- Apply typing rules after cleaning, before resolution

### Integration
Simple post-processing step. Rules are data-driven and easy to extend. Applied in the entity pipeline's extract method.

---

## Improvement 8: Fix OpenAPI Detection (P2)

### Problem
Current OpenAPI detection (0% rate) only looks for JSON inside `<pre><code>` blocks. Real API docs use:
- **Swagger UI** -- renders spec via JavaScript, spec URL in a `<script>` tag or `swagger-ui-init.js`
- **Redoc** -- renders spec from `spec-url` attribute or inline `<script>` tag
- **Raw JSON/YAML** -- sometimes served as `application/json` or `text/yaml` content type

### Current State
`schema.py` `_extract_openapi()` only checks `soup.find_all("pre")` for `<code>` children containing JSON.

### Changes

**File: `intelligence/intelligence/pipelines/schema.py`**

Add detection methods:
1. **Swagger UI detection** -- look for `swagger-ui` class/id, extract spec URL from `SwaggerUIBundle({url: "..."})` pattern in `<script>` tags
2. **Redoc detection** -- look for `<redoc>` element or `spec-url` attribute
3. **Inline script detection** -- check `<script>` tags (not just `type="application/ld+json"`) for OpenAPI-shaped JSON
4. **YAML support** -- try YAML parsing for candidates that start with `openapi:` or `swagger:`
5. **Meta tag detection** -- check for OpenAPI spec URLs in `<link>` or `<meta>` tags

```python
def _extract_openapi(self, soup, source_url):
    results = []
    results.extend(self._detect_swagger_ui(soup, source_url))
    results.extend(self._detect_redoc(soup, source_url))
    results.extend(self._detect_inline_spec(soup, source_url))
    results.extend(self._detect_pre_code_spec(soup, source_url))  # existing logic
    return results
```

**File: `intelligence/pyproject.toml`**
- Add `pyyaml>=6.0` dependency for YAML parsing

### Integration
Extends existing `SchemaPipeline` with additional detection methods. No changes to worker or store.

---

## Improvement 9: Backfill Markdown (P2)

### Problem
13 of 24 crawled pages have no markdown because they were crawled before the markdown pipeline was added.

### Current State
- `store.py` has `save_markdown()` and `get_page_markdown()`
- `worker.py` runs markdown pipeline on new pages
- No way to re-process existing pages

### Changes

**File: `intelligence/intelligence/scripts/backfill_markdown.py` (NEW)**

A one-time script that:
1. Queries PostgreSQL for pages where `markdown IS NULL`
2. Runs `MarkdownPipeline.extract()` on each page's stored HTML
3. Saves the result via `KnowledgeStore.save_markdown()`
4. Logs progress and errors

```python
def backfill():
    store = KnowledgeStore()
    pipeline = MarkdownPipeline()
    pages = store.get_pages_without_markdown()  # new method
    for page in pages:
        results = pipeline.extract(page["html"], page["url"])
        if results:
            store.save_markdown(page["id"], results[0])
```

**File: `intelligence/intelligence/store.py`**
- Add `get_pages_without_markdown()` method
- Add `get_page_html(page_id)` method

### Integration
Run as a one-time CLI command: `python -m intelligence.scripts.backfill_markdown`. Idempotent -- skips pages that already have markdown.

---

## Improvement 10: Crawl Scheduler (P3)

### Problem
No way to periodically re-crawl known URLs to detect changes. Needed for production use but not urgent.

### Current State
No scheduling infrastructure. Crawls are one-shot (CLI or API-triggered).

### Changes

**File: `intelligence/intelligence/scheduler.py` (NEW)**

A simple scheduler that:
1. Queries PostgreSQL for pages where `fetched_at < NOW() - interval`
2. Pushes URLs to `phantombot:crawl_requests` Redis stream
3. Runs on a configurable interval (default: 24 hours)

```python
class CrawlScheduler:
    def __init__(self, interval_hours: int = 24, max_age_hours: int = 72): ...
    def run_once(self): ...  # Check and enqueue stale URLs
    def run(self): ...       # Loop with sleep
```

**File: `intelligence/intelligence/config.py`**
- Add `SCHEDULER_INTERVAL_HOURS` and `SCHEDULER_MAX_AGE_HOURS` env vars

**File: `intelligence/intelligence/store.py`**
- Add `get_stale_pages(max_age_hours)` method

### Integration
Runs as a separate process alongside the worker. Pushes to the same `phantombot:crawl_requests` stream that the Go crawler consumes (after Improvement 3 is implemented).

---

## Dependency Graph

```
Improvement 1 (SpaCy upgrade) ─────────────────┐
Improvement 2 (Entity cleaning) ────────────────┤
                                                 ├─> Improvement 4 (Entity resolution)
Improvement 7 (Entity typing) ──────────────────┘
                                                      │
Improvement 3 (Crawl loop) ──────────────────────> Improvement 10 (Scheduler)
                                                      │
Improvement 5 (JS rendering) ─── independent          │
Improvement 6 (Anti-bot) ─────── independent          │
Improvement 8 (OpenAPI fix) ───── independent          │
Improvement 9 (Markdown backfill) independent          │
```

Improvements 1, 2, and 3 are fully independent and can be worked in parallel. Improvement 4 should come after 1+2 since it builds on cleaned entities. Improvement 10 depends on 3. Everything else is independent.

---

## Pipeline Flow After All Improvements

```
HTML page arrives via Redis Stream
        │
        ▼
  MarkdownPipeline.extract()          ──> save_markdown()
        │
        ▼
  EntityPipeline.extract()            ──> SpaCy en_core_web_trf NER
        │
        ▼
  EntityCleaner.clean()               ──> strip possessives, punctuation, garbage
        │
        ▼
  EntityTypingRules.apply()           ──> fix Claude=PRODUCT, Python=PRODUCT
        │
        ▼
  EntityResolver.resolve()            ──> dedup "Tesla" / "Tesla Inc" / "Tesla, Inc."
        │
        ▼
  save_entities() + save_resolved()
        │
        ▼
  SchemaPipeline.extract()            ──> JSON-LD + OpenAPI (Swagger/Redoc/inline)
        │
        ▼
  save_schemas()
```
