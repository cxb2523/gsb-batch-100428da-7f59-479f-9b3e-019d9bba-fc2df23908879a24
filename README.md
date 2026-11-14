# Phantombot

**An open-source web crawler and knowledge extraction engine built for the AI agentic era.**

Traditional crawlers fetch pages and return links. Phantombot fetches the web and returns **structured, queryable knowledge** — named entities, detected schemas, and semantic embeddings — with full provenance, confidence scores, and freshness timestamps. Purpose-built for AI agents that need facts, not HTML.

[![CI](https://github.com/surya-koritala/phantombot/actions/workflows/ci.yml/badge.svg)](https://github.com/surya-koritala/phantombot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Go](https://img.shields.io/badge/Go-1.22-00ADD8?logo=go)](https://go.dev)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python)](https://www.python.org)

---

## Table of Contents

- [Why Phantombot?](#why-phantombot)
- [Architecture](#architecture)
- [Features](#features)
- [Quick Start](#quick-start)
- [API Reference](#api-reference)
- [Configuration](#configuration)
- [Testing](#testing)
- [Project Structure](#project-structure)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)

---

## Why Phantombot?

AI agents need to consume web knowledge programmatically. The current tooling forces them to either:

1. **Call a search engine** — get a list of links, then fetch and parse each page themselves.
2. **Use a traditional crawler** — get raw HTML dumps with no semantic structure.
3. **Use a hosted RAG pipeline** — pay per call, lose control over freshness and provenance.

Phantombot is the fourth option: a self-hosted, open-source pipeline that continuously crawls the web and serves the extracted knowledge through a structured API. Every response includes the source URLs, a confidence score, and a freshness timestamp so agents can make trust decisions without additional round trips.

The architecture separates concerns cleanly. The Go crawler is fast and polite — it handles robots.txt, politeness delays, and concurrent depth-first crawling. The Python intelligence layer runs NLP and schema detection without slowing the crawler. The FastAPI layer speaks the language agents expect.

---

## Architecture

```
+---------------------------------------------+
|              Agent Query API                |
|         FastAPI — structured data,          |
|         not links, not raw HTML             |
+---------------------+-----------------------+
                       |
+---------------------------------------------+
|          Intelligence Layer (Python)        |
|   Entity extraction (SpaCy NER)             |
|   Schema detection (JSON-LD, OpenGraph)     |
|   Vector embedding (sentence-transformers)  |
+-------------------+-------------------------+
                     |
        Redis Streams (phantombot:crawled)
                     |
+--------------------+------------------------+
|    Live Crawler (Go)   |   Common Crawl     |
|    Real-time, deep     |   (v2 — planned)   |
|    robots.txt aware    |                    |
+--------------------+------------------------+
                     |
+---------------------------------------------+
|              Knowledge Store                |
|   PostgreSQL (JSONB) — entities, schemas    |
|   Qdrant — vector embeddings                |
+---------------------------------------------+
```

**Data flow:**

1. A `POST /crawl` request (or a seed URL passed directly to the crawler binary) enqueues a URL.
2. The Go crawler fetches the page, respects `robots.txt` and politeness delays, and publishes the raw result to the `phantombot:crawled` Redis Stream.
3. The Python intelligence worker consumes from the stream, runs the entity and schema pipelines, embeds the results with `sentence-transformers`, and writes to PostgreSQL and Qdrant.
4. The FastAPI layer reads from PostgreSQL and Qdrant to serve structured responses to any agent.

---

## Features

- **High-throughput Go crawler** — configurable concurrency, depth limits, politeness delays, and `robots.txt` compliance out of the box.
- **Named entity extraction** — SpaCy NER extracts persons, organizations, locations, products, events, and more from crawled HTML.
- **Structured schema detection** — JSON-LD and OpenGraph schemas are detected, parsed, and stored as typed records.
- **Semantic vector search** — every extracted record is embedded with `sentence-transformers` and stored in Qdrant for sub-second similarity search.
- **Provenance on every result** — every API response includes source URLs, per-record confidence scores, and freshness timestamps.
- **Standard response envelope** — all endpoints return the same `PhantomResponse` shape, making agent integration trivial.
- **Optional API key auth** — set `API_KEY` in `.env` to require bearer token authentication; leave it empty for open local development.
- **Docker Compose for local dev** — Redis, PostgreSQL, and Qdrant start with a single command.
- **CI on GitHub Actions** — unit tests, pipeline tests, API tests, and full integration tests with live services run on every push.

---

## Quick Start

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose
- [Go 1.22+](https://go.dev/dl/)
- [Python 3.12+](https://www.python.org/downloads/)

### 1. Clone the repository

```bash
git clone https://github.com/surya-koritala/phantombot.git
cd phantombot
```

### 2. Configure environment

```bash
cp .env.example .env
```

The defaults in `.env.example` work for local development without any changes. See the [Configuration](#configuration) section for a full reference.

### 3. Start the infrastructure

```bash
docker compose up -d
```

This starts Redis 7, PostgreSQL 16, and Qdrant. Wait for all three health checks to pass:

```bash
docker compose ps
```

### 4. Run the database migrations

```bash
docker compose exec postgres psql -U phantombot -d phantombot \
  -f /docker-entrypoint-initdb.d/001_initial.sql
```

> Note: If you mounted the `store/migrations` directory, the migration runs automatically on first start. This step is only needed if it did not.

### 5. Build and run the Go crawler

```bash
cd crawler
go build -o phantombot ./cmd/phantombot
./phantombot -urls https://example.com
```

The crawler reads configuration from environment variables (or your `.env` file). It publishes each crawled page to the `phantombot:crawled` Redis Stream.

To crawl multiple seed URLs:

```bash
./phantombot -urls "https://example.com,https://other-site.com"
```

### 6. Install and run the intelligence layer

```bash
cd intelligence
pip install -e ".[dev]"
python -m spacy download en_core_web_sm
python -m intelligence.worker
```

The worker consumes pages from the Redis Stream, runs entity and schema extraction, and writes results to PostgreSQL and Qdrant. Leave it running in a separate terminal.

### 7. Install and run the API

```bash
cd api
pip install -e ".[dev]"
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

The API is now available at `http://localhost:8000`. Visit `http://localhost:8000/docs` for the interactive Swagger UI.

### 8. Make your first query

```bash
# Request a fresh crawl
curl -X POST http://localhost:8000/crawl \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "max_depth": 2}'

# Query extracted entities
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"query": "IANA", "limit": 5}'

# Semantic vector search
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query": "domain registration authority", "limit": 5}'
```

---

## API Reference

All endpoints return the `PhantomResponse` envelope:

```json
{
  "data": [...],
  "sources": ["https://example.com/page"],
  "confidence": 0.85,
  "freshness": "2026-04-12T12:00:00Z",
  "related": []
}
```

If `API_KEY` is set in your environment, include it as a bearer token:

```
Authorization: Bearer <your-api-key>
```

---

### POST /crawl

Request a fresh crawl of a URL. Returns immediately with a job ID; crawling happens asynchronously.

**Request**

```json
{
  "url": "https://example.com",
  "max_depth": 3
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `url` | string | required | The seed URL to crawl. Must be `http` or `https`. |
| `max_depth` | integer | `3` | How many link-hops deep to follow. Range: 1–10. |

**Response — 202 Accepted**

```json
{
  "data": {
    "job_id": "550e8400-e29b-41d4-a716-446655440000",
    "url": "https://example.com",
    "status": "pending"
  },
  "sources": [],
  "confidence": 0.0,
  "freshness": null,
  "related": []
}
```

---

### POST /query

Search extracted entities by text. Uses a case-insensitive substring match against the entity text stored in PostgreSQL.

**Request**

```json
{
  "query": "IANA",
  "limit": 10
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `query` | string | required | Text to search for in extracted entities. |
| `limit` | integer | `10` | Maximum number of results. Range: 1–100. |

**Response — 200 OK**

```json
{
  "data": [
    {
      "entity_text": "IANA",
      "entity_type": "ORG",
      "confidence": 0.9,
      "source_url": "https://example.com",
      "fetched_at": "2026-04-12T12:00:00Z",
      "extracted_at": "2026-04-12T12:00:05Z"
    }
  ],
  "sources": ["https://example.com"],
  "confidence": 0.9,
  "freshness": "2026-04-12T12:00:00Z",
  "related": []
}
```

---

### GET /entity/{id}

Retrieve a specific entity by its UUID, including full provenance.

**Response — 200 OK**

```json
{
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "entity_text": "IANA",
    "entity_type": "ORG",
    "confidence": 0.9,
    "source_url": "https://example.com",
    "fetched_at": "2026-04-12T12:00:00Z"
  },
  "sources": ["https://example.com"],
  "confidence": 0.9,
  "freshness": "2026-04-12T12:00:00Z",
  "related": []
}
```

**Response — 404 Not Found**

```json
{"detail": "Entity not found"}
```

---

### POST /search

Semantic vector search across all embedded knowledge in Qdrant. Returns results ranked by cosine similarity to the query embedding.

**Request**

```json
{
  "query": "domain registration authority",
  "limit": 10
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `query` | string | required | Natural language query to embed and search. |
| `limit` | integer | `10` | Maximum number of results. Range: 1–100. |

**Response — 200 OK**

```json
{
  "data": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "score": 0.91,
      "entity_text": "IANA",
      "entity_type": "ORG",
      "source_url": "https://example.com"
    }
  ],
  "sources": [],
  "confidence": 0.91,
  "freshness": null,
  "related": []
}
```

---

### GET /health

Health check. Returns `200 OK` when the API process is running.

**Response — 200 OK**

```json
{"status": "ok"}
```

---

## Configuration

All configuration is driven by environment variables. Copy `.env.example` to `.env` and edit as needed. The `.env` file is intentionally excluded from version control.

```bash
cp .env.example .env
```

### Redis

| Variable | Default | Description |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379` | Redis connection URL. |
| `REDIS_STREAM_NAME` | `phantombot:crawled` | Stream name for crawler output. |
| `REDIS_CONSUMER_GROUP` | `intelligence` | Consumer group for the intelligence worker. |

### PostgreSQL

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_HOST` | `localhost` | PostgreSQL host. |
| `POSTGRES_PORT` | `5432` | PostgreSQL port. |
| `POSTGRES_DB` | `phantombot` | Database name. |
| `POSTGRES_USER` | `phantombot` | Database user. |
| `POSTGRES_PASSWORD` | `localdev` | Database password. Change this in production. |

### Qdrant

| Variable | Default | Description |
|---|---|---|
| `QDRANT_HOST` | `localhost` | Qdrant host. |
| `QDRANT_PORT` | `6335` | Qdrant HTTP port (mapped from container port 6333). |
| `QDRANT_COLLECTION` | `phantombot` | Qdrant collection name. |

### Crawler (Go)

| Variable | Default | Description |
|---|---|---|
| `CRAWLER_CONCURRENCY` | `10` | Number of parallel fetch workers. |
| `CRAWLER_MAX_DEPTH` | `3` | Maximum link depth from seed URLs. |
| `CRAWLER_USER_AGENT` | `Phantombot/1.0` | User-Agent header sent with every request. |
| `CRAWLER_REQUEST_TIMEOUT_SECONDS` | `30` | Per-request timeout. |
| `CRAWLER_POLITENESS_DELAY_MS` | `1000` | Delay between requests to the same domain (ms). |

### API

| Variable | Default | Description |
|---|---|---|
| `API_HOST` | `0.0.0.0` | Bind address for the FastAPI server. |
| `API_PORT` | `8000` | Port for the FastAPI server. |
| `API_KEY` | _(empty)_ | Bearer token for API authentication. Leave empty to disable auth. |

### Intelligence Layer

| Variable | Default | Description |
|---|---|---|
| `SPACY_MODEL` | `en_core_web_sm` | SpaCy model to load. Use `en_core_web_lg` for better accuracy. |

### Optional LLM integration

| Variable | Default | Description |
|---|---|---|
| `LLM_API_KEY` | _(empty)_ | API key for an optional LLM provider (unused in v1). |
| `LLM_MODEL` | _(empty)_ | Model identifier (unused in v1). |

---

## Testing

### Go — unit tests

The crawler's unit tests cover the frontier, robots.txt checker, and HTTP fetcher without requiring a live Redis instance.

```bash
cd crawler
go test ./internal/frontier/ ./internal/robots/ ./internal/fetcher/ -v
```

### Go — all tests (requires Redis)

```bash
cd crawler
REDIS_URL=redis://localhost:6379 go test ./... -v
```

### Python — intelligence layer

Pipeline tests run against fixture HTML files and do not require live infrastructure.

```bash
cd intelligence
pip install -e ".[dev]"
python -m spacy download en_core_web_sm
pytest tests/test_entity_pipeline.py tests/test_schema_pipeline.py tests/test_worker.py -v
```

### Python — API

```bash
cd api
pip install -e ".[dev]"
pytest tests/ -v
```

### Integration tests (requires Docker Compose)

Start the full stack, then run all tests end-to-end:

```bash
docker compose up -d

# Run DB migrations
docker compose exec postgres psql -U phantombot -d phantombot \
  -f /docker-entrypoint-initdb.d/001_initial.sql

# Go integration tests
cd crawler
REDIS_URL=redis://localhost:6379 go test ./... -v

# Python intelligence integration tests
cd intelligence
REDIS_URL=redis://localhost:6379 \
POSTGRES_PASSWORD=localdev \
QDRANT_PORT=6335 \
pytest tests/ -v
```

The CI pipeline runs all three test suites in parallel and then runs integration tests as a dependent job with live Redis, PostgreSQL, and Qdrant services. See [`.github/workflows/ci.yml`](.github/workflows/ci.yml) for the full configuration.

---

## Project Structure

```
phantombot/
|
|-- crawler/                    # Go live crawler
|   |-- cmd/
|   |   +-- phantombot/
|   |       +-- main.go         # Entry point — flag parsing, worker pool, link extraction
|   |-- internal/
|   |   |-- config/             # Environment-driven configuration
|   |   |-- fetcher/            # HTTP client with timeout and User-Agent
|   |   |-- frontier/           # Thread-safe BFS URL queue with deduplication
|   |   |-- publisher/          # Redis Streams publisher
|   |   +-- robots/             # robots.txt fetcher and rule checker
|   |-- pkg/
|   |   +-- models/             # Shared Go types (CrawlResult, etc.)
|   |-- go.mod
|   +-- go.sum
|
|-- intelligence/               # Python intelligence layer
|   |-- intelligence/
|   |   |-- pipelines/
|   |   |   |-- base.py         # BasePipeline interface
|   |   |   |-- entity.py       # SpaCy NER entity extraction
|   |   |   +-- schema.py       # JSON-LD and OpenGraph schema detection
|   |   |-- config.py           # Environment-driven configuration
|   |   |-- models.py           # Pydantic models (ExtractedEntity, etc.)
|   |   |-- store.py            # PostgreSQL + Qdrant write layer
|   |   +-- worker.py           # Redis Streams consumer main loop
|   |-- tests/
|   |   |-- conftest.py
|   |   |-- test_entity_pipeline.py
|   |   |-- test_schema_pipeline.py
|   |   |-- test_store.py
|   |   +-- test_worker.py
|   +-- pyproject.toml
|
|-- api/                        # Python FastAPI agent query API
|   |-- api/
|   |   |-- routes/
|   |   |   |-- crawl.py        # POST /crawl
|   |   |   |-- query.py        # POST /query, GET /entity/{id}
|   |   |   +-- search.py       # POST /search
|   |   |-- config.py           # Environment-driven configuration
|   |   |-- dependencies.py     # FastAPI dependency injection (Redis, PG, Qdrant, auth)
|   |   |-- main.py             # FastAPI app, router registration, GET /health
|   |   +-- models.py           # Pydantic request/response models, PhantomResponse
|   |-- tests/
|   |   |-- conftest.py
|   |   |-- test_crawl.py
|   |   |-- test_query.py
|   |   +-- test_search.py
|   +-- pyproject.toml
|
|-- store/
|   +-- migrations/
|       +-- 001_initial.sql     # PostgreSQL schema: crawled_pages, entities, schemas, crawl_jobs
|
|-- fixtures/                   # HTML fixture files for pipeline tests
|   |-- entity_rich_page.html
|   |-- jsonld_page.html
|   |-- openapi_page.html
|   +-- simple_page.html
|
|-- .github/
|   +-- workflows/
|       +-- ci.yml              # CI: Go tests, Python tests, integration tests
|
|-- docker-compose.yml          # Redis, PostgreSQL, Qdrant for local development
|-- .env.example                # Environment variable template
+-- LICENSE
```

---

## Roadmap

### v2 — planned

- **Common Crawl integration** — consume and re-process WARC files from the Common Crawl corpus (CC v2) to bootstrap the knowledge store without running a live crawler.
- **Neo4j knowledge graph** — promote the flat entity store to a graph database, linking co-occurring entities and building relationship edges automatically.
- **Playwright renderer** — optionally render JavaScript-heavy pages using Playwright before publishing to the stream, enabling extraction from SPAs and dynamic content.
- **Additional extraction pipelines** — relationship extraction, financial data detection, table parsing, and claim detection pipelines to complement NER.
- **Streaming query API** — server-sent events (SSE) endpoint for agents that need real-time updates as new knowledge arrives.
- **Web dashboard** — lightweight UI for browsing crawled pages, inspecting extracted entities, and monitoring crawler health.

See [open issues](https://github.com/surya-koritala/phantombot/issues) for the current backlog.

---

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. The short version:

1. Fork the repository and create a feature branch.
2. Write tests for any new functionality.
3. Run the full test suite (`go test ./...` and `pytest`) before submitting.
4. Open a pull request against `main` with a clear description of the change and the motivation behind it.

---

## License

MIT — see [LICENSE](LICENSE) for the full text.
