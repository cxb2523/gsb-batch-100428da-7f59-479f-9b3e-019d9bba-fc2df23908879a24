# Benchmark Suite — Design Spec

> A reproducible, fair benchmark suite that compares Phantombot against Firecrawl and Crawl4AI across crawl speed, extraction quality, schema detection, and output usefulness.

## Problem Statement

Phantombot positions itself as a web crawler and knowledge extraction engine for the AI agentic era. To credibly make that claim, we need hard numbers showing how it compares to the two most prominent open-source alternatives: Firecrawl (the cloud/self-hosted scraping API with 50k+ GitHub stars) and Crawl4AI (the Python-native LLM-ready crawler with 58k+ stars).

Existing benchmarks between these tools are published by competitors (Spider, Firecrawl themselves) and are methodologically questionable — they test narrow scenarios, use inconsistent configurations, or omit capabilities that matter for AI agents (entity extraction, schema detection, structured output quality). No benchmark measures the full pipeline from raw URL to agent-queryable knowledge.

We need a benchmark that is:

- **Reproducible** — anyone can clone the repo, run one command, and get the same results
- **Fair** — same URLs, same hardware constraints, same network conditions, same timeout budgets
- **Comprehensive** — measures what actually matters for AI agent consumers: speed, extraction quality, schema detection, and output token efficiency
- **Extensible** — new tools or metrics can be added without rewriting the harness

## Test Corpus

A fixed set of 25 URLs covering six page archetypes that AI agents commonly encounter. URLs are chosen to be stable (unlikely to change structure), publicly accessible (no auth required), and diverse in complexity.

### Page Categories

| Category | Count | Why it matters | Example URLs |
|---|---|---|---|
| **Static HTML / Documentation** | 5 | Baseline — clean HTML, no JS needed | Wikipedia articles, Python docs, MDN reference pages |
| **JS-Heavy SPAs** | 4 | Tests JS rendering capability — many modern sites require it | React-based dashboards, Angular docs, Hacker News (dynamic comments) |
| **JSON-LD Structured Data** | 4 | Tests schema detection — structured data embedded in pages | Recipe sites (schema.org/Recipe), product pages, event listings |
| **API Documentation** | 4 | Tests OpenAPI/schema extraction — core differentiator for agents | Swagger UI pages, Redoc-hosted APIs, GitHub REST API docs |
| **News Articles (Entity-Rich)** | 4 | Tests entity extraction quality — named entities, orgs, locations | BBC News, Reuters, AP News articles |
| **E-commerce Product Pages** | 4 | Tests structured data + entity extraction combined | Amazon product pages, Best Buy listings, Etsy products |

### Corpus Stability

The corpus is version-controlled as a JSON manifest (`benchmark/corpus.json`) containing:

```json
{
  "version": "1.0",
  "created_at": "2026-04-12",
  "urls": [
    {
      "id": "static-001",
      "url": "https://en.wikipedia.org/wiki/Web_crawler",
      "category": "static_html",
      "expected_entities": 15,
      "expected_schemas": 0,
      "notes": "Well-structured Wikipedia article with many named entities"
    }
  ]
}
```

To guard against URL rot and page changes, the benchmark runner archives each page (WARC or HTML snapshot) on first run. Subsequent runs can operate against either live URLs or the archived snapshots. The `--use-archive` flag controls this behavior.

### Corpus Selection Criteria

1. **Publicly accessible** — no login, paywall, or API key required
2. **Stable structure** — prefer pages with low churn (docs, Wikipedia, reference pages)
3. **Diverse complexity** — mix of simple HTML, JS-rendered, and heavily structured pages
4. **Known ground truth** — pages where entities and schemas can be manually verified
5. **Robots.txt permissive** — all URLs must allow crawling by all three tools

## Metrics

### M1: Crawl Speed

| Metric | Unit | How measured |
|---|---|---|
| Time to first result | Seconds | Wall-clock time from request to first page content returned |
| Total corpus time | Seconds | Wall-clock time to complete all 25 URLs sequentially |
| Pages per second | Float | 25 / total_corpus_time |
| Concurrent throughput | Pages/sec | Time to complete corpus with concurrency=5 |

Each tool is given the same timeout budget (30 seconds per URL, 15 minutes total). URLs that timeout are recorded as failures, not retried.

### M2: Extraction Quality (Entity Extraction)

| Metric | Unit | How measured |
|---|---|---|
| Entity precision | Float (0-1) | TP / (TP + FP) — of entities returned, how many are correct |
| Entity recall | Float (0-1) | TP / (TP + FN) — of known entities, how many were found |
| Entity F1 | Float (0-1) | Harmonic mean of precision and recall |
| Entity type accuracy | Float (0-1) | Of correctly identified entities, how many have the right type label |

Entity matching uses normalized text comparison (lowercased, stripped) with a Levenshtein distance threshold of 2 characters to account for minor formatting differences. Type matching is exact (PERSON must match PERSON, not ORG).

For Phantombot, entity extraction runs via the EntityPipeline (SpaCy NER). For Firecrawl and Crawl4AI, which do not have native entity extraction, the benchmark extracts their markdown/text output and runs the same SpaCy pipeline against it. This isolates the comparison to "quality of text extraction from HTML" rather than "quality of NER model" — a fairer test.

### M3: Schema Detection Accuracy

| Metric | Unit | How measured |
|---|---|---|
| JSON-LD detection rate | Float (0-1) | Fraction of pages with JSON-LD where the tool found it |
| OpenAPI detection rate | Float (0-1) | Fraction of pages with OpenAPI specs where the tool found it |
| Schema completeness | Float (0-1) | Fields present in detected schema / fields in ground truth |
| False positive rate | Float (0-1) | Schemas detected where none exist in ground truth |

Phantombot's SchemaPipeline handles this natively. For competitors, we check if their output preserves JSON-LD script tags and structured data in a parseable form.

### M4: Output Format Usefulness

| Metric | Unit | How measured |
|---|---|---|
| Output token count | Integer | tiktoken (cl100k_base) token count of output |
| Content density | Float | (relevant_tokens / total_tokens) — measures signal-to-noise |
| Markdown well-formedness | Float (0-1) | Ratio of valid markdown sections (headers, lists, code blocks) |
| Readability score | Float | Flesch-Kincaid grade level of extracted text |
| Boilerplate ratio | Float (0-1) | Fraction of output that is navigation, ads, footers vs. main content |

Token count matters because AI agents pay per token. A tool that returns 10,000 tokens of boilerplate-heavy content is worse than one returning 2,000 tokens of clean content, even if both "succeed."

### M5: Resource Usage

| Metric | Unit | How measured |
|---|---|---|
| Peak memory (RSS) | MB | Maximum resident set size during corpus run |
| CPU time | Seconds | User + system CPU time |
| Network bytes | MB | Total bytes downloaded (measured via traffic counter) |
| Process count | Integer | Number of child processes spawned (browsers, workers) |

Measured using `psutil` for Python processes and `/proc` monitoring for Go/Docker processes.

### M6: Reliability

| Metric | Unit | How measured |
|---|---|---|
| Success rate | Float (0-1) | Pages returning usable content / total pages attempted |
| Error rate by category | Dict | Errors broken down by page category |
| Retry resilience | Float (0-1) | Success rate on second attempt for initially failed URLs |

## Ground Truth

Ground truth is the foundation of extraction quality metrics. We create it in three layers:

### Layer 1: Entity Annotations (Manual)

For each of the 25 corpus URLs, a human annotator produces a JSON file listing expected entities:

```json
{
  "url": "https://en.wikipedia.org/wiki/Web_crawler",
  "annotator": "human",
  "annotated_at": "2026-04-12",
  "entities": [
    {"text": "Google", "type": "ORG"},
    {"text": "Googlebot", "type": "PRODUCT"},
    {"text": "World Wide Web", "type": "PRODUCT"},
    {"text": "Tim Berners-Lee", "type": "PERSON"}
  ]
}
```

Annotation guidelines:
- Include entities of types: PERSON, ORG, GPE (geopolitical entity), PRODUCT, LOC, EVENT, MONEY
- Only include entities that appear in the **main content** (not navigation, sidebars, footers)
- Use the exact surface form as it appears in the page text
- Minimum confidence threshold: only include entities a reasonable NER system should find

### Layer 2: Schema Annotations (Manual)

For pages containing JSON-LD or OpenAPI specifications:

```json
{
  "url": "https://example.com/recipe/chocolate-cake",
  "schemas": [
    {
      "type": "JSON_LD",
      "schema_org_type": "Recipe",
      "key_fields": ["name", "author", "cookTime", "ingredients"],
      "expected_count": 1
    }
  ]
}
```

### Layer 3: Output Quality Ratings (Semi-Automated)

For output usefulness metrics, we combine automated scoring (token count, readability) with a human spot-check of 5 URLs per category, rating on a 1-5 scale:
- 1: Unusable (empty, garbled, mostly boilerplate)
- 2: Poor (main content present but heavily polluted)
- 3: Acceptable (main content readable, some noise)
- 4: Good (clean content, well-structured)
- 5: Excellent (clean, structured, minimal noise)

### Ground Truth Storage

All ground truth files live in `benchmark/ground_truth/` organized by URL ID:

```
benchmark/ground_truth/
  static-001-entities.json
  static-001-schemas.json
  jsonld-001-entities.json
  jsonld-001-schemas.json
  ...
```

### Ground Truth Maintenance

Ground truth is versioned with the corpus. When a URL's content changes significantly (detected by content hash comparison), the benchmark warns and skips quality metrics for that URL until ground truth is re-annotated.

## Architecture

### Directory Structure

```
benchmark/
  corpus.json                    # URL manifest with metadata
  ground_truth/                  # Manual annotations
  adapters/                      # Tool-specific adapter modules
    phantombot_adapter.py
    firecrawl_adapter.py
    crawl4ai_adapter.py
    base.py                      # Abstract adapter interface
  metrics/                       # Metric computation modules
    speed.py
    extraction.py
    schema.py
    output_quality.py
    resources.py
    reliability.py
  runner.py                      # Main benchmark orchestrator
  report.py                      # Report generation (markdown + JSON + charts)
  archive.py                     # Page archiving for reproducibility
  config.py                      # Benchmark configuration
  requirements.txt               # Python dependencies
  Makefile                       # Convenience targets
  README.md                      # How to run the benchmark
  results/                       # Output directory (gitignored)
    {timestamp}/
      raw_results.json
      report.md
      charts/
```

### Adapter Pattern

Each tool implements a common interface so the runner treats them identically:

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class CrawlResult:
    url: str
    status_code: int
    html: str                    # Raw HTML (if available)
    markdown: str                # Cleaned markdown output
    text: str                    # Plain text extraction
    metadata: dict               # Tool-specific metadata
    elapsed_seconds: float       # Time for this single crawl
    success: bool
    error: str | None = None

@dataclass
class ExtractionResult:
    entities: list[dict]         # [{"text": "...", "type": "..."}, ...]
    schemas: list[dict]          # [{"type": "JSON_LD", "data": {...}}, ...]

class BaseAdapter(ABC):
    """Common interface for all benchmark tools."""

    @abstractmethod
    def name(self) -> str:
        """Tool display name."""
        ...

    @abstractmethod
    def version(self) -> str:
        """Tool version string."""
        ...

    @abstractmethod
    def setup(self) -> None:
        """One-time setup (start Docker containers, load models, etc.)."""
        ...

    @abstractmethod
    def teardown(self) -> None:
        """Cleanup after benchmark run."""
        ...

    @abstractmethod
    def crawl(self, url: str, timeout: int = 30) -> CrawlResult:
        """Crawl a single URL and return structured result."""
        ...

    @abstractmethod
    def extract_entities(self, html: str, url: str) -> list[dict]:
        """Extract entities from HTML. May use tool-native or SpaCy fallback."""
        ...

    @abstractmethod
    def extract_schemas(self, html: str, url: str) -> list[dict]:
        """Extract schemas (JSON-LD, OpenAPI) from HTML."""
        ...
```

### Runner Flow

```
1. Load corpus.json
2. For each adapter (phantombot, firecrawl, crawl4ai):
   a. adapter.setup()
   b. For each URL in corpus:
      i.   Start resource monitoring (CPU, memory, network)
      ii.  result = adapter.crawl(url, timeout=30)
      iii. Record timing, status, resource usage
      iv.  entities = adapter.extract_entities(result.html, url)
      v.   schemas = adapter.extract_schemas(result.html, url)
      vi.  Stop resource monitoring
   c. adapter.teardown()
3. Compute metrics against ground truth
4. Generate report (markdown + JSON + charts)
```

### Configuration

```python
@dataclass
class BenchmarkConfig:
    corpus_path: str = "benchmark/corpus.json"
    ground_truth_dir: str = "benchmark/ground_truth"
    output_dir: str = "benchmark/results"
    timeout_per_url: int = 30            # seconds
    total_timeout: int = 900             # 15 minutes
    concurrency: int = 1                 # sequential by default
    concurrent_workers: int = 5          # for concurrent throughput test
    use_archive: bool = False            # use cached pages instead of live
    tools: list[str] = ("phantombot", "firecrawl", "crawl4ai")
    spacy_model: str = "en_core_web_sm"  # for entity extraction fallback
    skip_resource_monitoring: bool = False
```

### CLI Interface

```bash
# Run full benchmark (all tools, all metrics)
python -m benchmark.runner

# Run specific tools only
python -m benchmark.runner --tools phantombot,firecrawl

# Use archived pages (offline mode)
python -m benchmark.runner --use-archive

# Run concurrent throughput test
python -m benchmark.runner --concurrency 5

# Generate report from existing results
python -m benchmark.report benchmark/results/2026-04-12T10:00:00/raw_results.json
```

## Competitor Integration

### Phantombot

Phantombot runs natively from the repo. The adapter:

1. **Setup**: Starts Docker Compose services (Redis, PostgreSQL, Qdrant), builds and runs the Go crawler binary
2. **Crawl**: Invokes the crawler directly via Go subprocess or via the FastAPI `/crawl` endpoint, reads results from Redis Streams
3. **Entity extraction**: Uses the `EntityPipeline` directly (SpaCy NER on extracted HTML text)
4. **Schema extraction**: Uses the `SchemaPipeline` directly (JSON-LD and OpenAPI detection)
5. **Teardown**: Stops Docker Compose services

The adapter can run in two modes:
- **Direct mode**: Calls Go fetcher + Python pipelines directly (lower overhead, measures raw capability)
- **API mode**: Calls through FastAPI endpoints (measures end-to-end latency including API overhead)

### Firecrawl

Firecrawl runs as a self-hosted Docker deployment. We do not use the cloud API to ensure fair comparison (no network latency to external service, no rate limiting from their side).

1. **Setup**: Pulls and starts Firecrawl Docker Compose stack (`docker compose -f benchmark/docker/firecrawl-compose.yml up -d`). Required services: Firecrawl API (port 3002), Redis, Playwright service. Wait for health check at `http://localhost:3002/v1/health`.
2. **Crawl**: POST to `http://localhost:3002/v1/scrape` with `{"url": "...", "formats": ["markdown", "html", "rawHtml"]}`. No API key needed for self-hosted.
3. **Entity extraction**: Firecrawl does not have native NER. The adapter runs SpaCy on Firecrawl's markdown output to produce entities. This tests whether Firecrawl's text extraction is clean enough for downstream NER.
4. **Schema extraction**: Checks if Firecrawl's HTML output preserves `<script type="application/ld+json">` tags. Also checks if markdown output includes structured data in a parseable form.
5. **Teardown**: Stops Firecrawl Docker stack.

**Key configuration**: `NUM_WORKERS_PER_QUEUE=8`, `PLAYWRIGHT_ENABLED=true`, timeout matching our benchmark config.

### Crawl4AI

Crawl4AI runs as a Python library in the same process as the benchmark runner (or optionally via its Docker server).

1. **Setup**: `pip install crawl4ai` (included in `benchmark/requirements.txt`). Initialize `AsyncWebCrawler` with `BrowserConfig(headless=True)`. Run Playwright browser setup if needed.
2. **Crawl**: 
   ```python
   async with AsyncWebCrawler(config=BrowserConfig(headless=True)) as crawler:
       result = await crawler.arun(url=url, config=CrawlerRunConfig(
           markdown_generator=DefaultMarkdownGenerator()
       ))
   ```
   Returns `result.markdown`, `result.html`, `result.success`.
3. **Entity extraction**: Like Firecrawl, Crawl4AI does not have native NER. The adapter runs SpaCy on Crawl4AI's markdown output.
4. **Schema extraction**: Checks `result.html` for JSON-LD and OpenAPI specs using the same detection logic as Phantombot's SchemaPipeline for fairness.
5. **Teardown**: Close the AsyncWebCrawler context, which shuts down the Playwright browser.

**Key configuration**: Headless mode, same timeout budget, no proxy configuration (out-of-box experience).

### Fairness Controls

To ensure a fair comparison:

| Control | Implementation |
|---|---|
| Same URLs | All tools process identical `corpus.json` |
| Same hardware | Benchmark runs on a single machine; no cloud API calls |
| Same timeouts | 30s per URL for all tools |
| Same entity extraction | SpaCy `en_core_web_sm` used for NER on all tools' text output |
| Same schema detection | Phantombot's SchemaPipeline logic used to evaluate all tools' HTML output |
| Sequential execution | Tools run one at a time to avoid resource contention |
| Warm-up | Each tool crawls 2 throwaway URLs before timed runs to warm caches and connections |
| Cool-down | 10 second pause between tools to let system resources settle |
| Multiple runs | 3 runs per tool, report median to reduce variance |

## Output Format

### Markdown Report

The primary output is a human-readable markdown report at `benchmark/results/{timestamp}/report.md`:

```markdown
# Phantombot Benchmark Results

**Date:** 2026-04-12  **Corpus version:** 1.0  **Runs:** 3 (median reported)

## Speed

| Tool | Time to first result | Total corpus time | Pages/sec | Concurrent pages/sec |
|---|---|---|---|---|
| Phantombot | 0.8s | 45.2s | 0.55 | 2.1 |
| Firecrawl | 1.2s | 62.3s | 0.40 | 1.8 |
| Crawl4AI | 0.9s | 38.7s | 0.65 | 2.4 |

## Entity Extraction Quality

| Tool | Precision | Recall | F1 | Type accuracy |
|---|---|---|---|---|
| Phantombot | 0.82 | 0.75 | 0.78 | 0.91 |
| Firecrawl | 0.79 | 0.71 | 0.75 | 0.89 |
| Crawl4AI | 0.80 | 0.73 | 0.76 | 0.90 |

## Schema Detection

| Tool | JSON-LD rate | OpenAPI rate | Completeness | False positive rate |
|---|---|---|---|---|
| ... |

## Output Usefulness

| Tool | Avg tokens | Content density | Readability | Boilerplate ratio |
|---|---|---|---|---|
| ... |

## Resource Usage

| Tool | Peak memory | CPU time | Network bytes |
|---|---|---|---|
| ... |

## Reliability

| Tool | Success rate | Error breakdown |
|---|---|---|
| ... |
```

### JSON Results

Machine-readable results for programmatic consumption at `benchmark/results/{timestamp}/raw_results.json`:

```json
{
  "metadata": {
    "timestamp": "2026-04-12T10:00:00Z",
    "corpus_version": "1.0",
    "runs": 3,
    "hardware": {
      "cpu": "Apple M2 Pro",
      "memory_gb": 16,
      "os": "macOS 15.3"
    }
  },
  "results": {
    "phantombot": {
      "version": "0.1.0",
      "speed": { ... },
      "extraction": { ... },
      "schemas": { ... },
      "output_quality": { ... },
      "resources": { ... },
      "reliability": { ... },
      "per_url": [ ... ]
    },
    "firecrawl": { ... },
    "crawl4ai": { ... }
  }
}
```

### Charts

Generated via matplotlib, saved as PNGs in `benchmark/results/{timestamp}/charts/`:

- `speed_comparison.png` — Bar chart of pages/sec for each tool
- `extraction_f1.png` — Grouped bar chart of F1 by page category per tool
- `resource_usage.png` — Stacked bar of memory + CPU per tool
- `output_tokens.png` — Box plot of token counts per tool
- `reliability.png` — Success rate by page category per tool
- `radar.png` — Radar/spider chart showing all tools across all metric dimensions

Charts are optional (`--no-charts` flag) to support headless environments without display.

## v1 Scope (what we build first)

The benchmark is built incrementally. v1 focuses on what can ship in one sprint:

### In Scope (v1)

1. **Corpus manifest** with 25 URLs across all 6 categories
2. **Ground truth** for entity annotations (10 URLs) and schema annotations (8 URLs)
3. **Three adapters**: Phantombot (direct mode), Firecrawl (self-hosted Docker), Crawl4AI (Python library)
4. **Four metric groups**: Speed (M1), Extraction quality (M2), Schema detection (M3), Reliability (M6)
5. **Benchmark runner** with sequential execution, single run
6. **Markdown report** generation
7. **JSON results** output
8. **CLI** with `--tools`, `--use-archive` flags
9. **Docker Compose** file for Firecrawl self-hosted setup
10. **README** with setup and usage instructions

### Deferred (v2)

- Output usefulness metrics (M4) — requires tiktoken integration and readability scoring
- Resource monitoring (M5) — requires psutil instrumentation and cross-platform support
- Concurrent throughput testing
- Multiple runs with median reporting
- Chart generation (matplotlib)
- Page archiving (WARC snapshots) for offline mode
- Phantombot API mode adapter (through FastAPI)
- CI integration (run benchmark on every release)
- Historical result tracking and regression detection

### Implementation Order

1. `benchmark/adapters/base.py` — Abstract adapter interface
2. `benchmark/corpus.json` — URL manifest
3. `benchmark/ground_truth/` — Entity and schema annotations for 10 URLs
4. `benchmark/adapters/phantombot_adapter.py` — Phantombot direct mode
5. `benchmark/adapters/crawl4ai_adapter.py` — Crawl4AI (simplest, pure Python)
6. `benchmark/adapters/firecrawl_adapter.py` — Firecrawl self-hosted
7. `benchmark/metrics/speed.py` — Timing metrics
8. `benchmark/metrics/extraction.py` — Entity precision/recall/F1
9. `benchmark/metrics/schema.py` — Schema detection rates
10. `benchmark/metrics/reliability.py` — Success rates
11. `benchmark/runner.py` — Orchestrator
12. `benchmark/report.py` — Markdown + JSON report generation
13. `benchmark/config.py` — Configuration dataclass
14. `benchmark/Makefile` — `make benchmark`, `make report`
15. `benchmark/README.md` — Setup, prerequisites, usage

### Prerequisites for Running

- Docker and Docker Compose (for Firecrawl self-hosted + Phantombot services)
- Python 3.11+ with venv
- Go 1.23+ (for building Phantombot crawler)
- ~4GB free disk space (Docker images)
- ~8GB RAM (running all three tools)

## Testing Strategy

### Unit Tests

| Test | Validates |
|---|---|
| Entity matching logic handles exact matches | Core comparison works |
| Entity matching logic handles Levenshtein threshold | Fuzzy matching works |
| Entity matching ignores case and whitespace | Normalization works |
| Schema detection comparator matches JSON-LD types | Schema evaluation works |
| Corpus manifest loads and validates all required fields | Config is well-formed |
| Ground truth files parse without errors | Annotations are valid JSON |
| Metrics compute correctly on synthetic data | Math is right |
| Report generator produces valid markdown | Output is readable |

### Integration Tests

| Test | Validates |
|---|---|
| Phantombot adapter crawls a local test page (from fixtures/) | Adapter works end-to-end |
| Crawl4AI adapter crawls a local test page | Adapter works end-to-end |
| Firecrawl adapter crawls a local test page (requires Docker) | Adapter works end-to-end |
| Runner completes a 3-URL mini corpus with all adapters | Full pipeline works |
| Report generates from real runner output | Report generation is correct |

### Smoke Test (CI-friendly)

A fast smoke test that runs the benchmark against a single static HTML fixture served by a local HTTP server (Python `http.server`), validating that all adapters can crawl, extract, and report without requiring external network access or heavy Docker setups.

```bash
make benchmark-smoke   # Runs in <60 seconds, no Docker required
```

### Manual Validation Checklist

Before publishing any benchmark results:

- [ ] All three tools running on same machine, same session
- [ ] No other CPU/network-intensive processes running
- [ ] Docker resource limits are not artificially constraining any tool
- [ ] Ground truth annotations reviewed by at least two people
- [ ] Results reproduced across 3 independent runs
- [ ] Report reviewed for misleading framing or cherry-picked metrics
