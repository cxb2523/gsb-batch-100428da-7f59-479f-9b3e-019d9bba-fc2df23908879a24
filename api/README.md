# API

The FastAPI agent query API for Phantombot. It exposes structured knowledge extracted by the intelligence layer to AI agents and other consumers via a simple HTTP interface backed by PostgreSQL (entity lookups) and Qdrant (semantic search).

## Prerequisites

- Python 3.12+
- PostgreSQL and Qdrant running and reachable

## Setup

```bash
cd api
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Copy `../.env.example` to `.env` at the project root (or export the variables directly) before running.

## Run

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

For local development with auto-reload:

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check. Returns `{"status": "ok"}`. |
| `POST` | `/crawl` | Submit a URL for crawling. Enqueues a crawl job and returns a job ID. |
| `POST` | `/query` | Search entities by text (case-insensitive substring match against PostgreSQL). |
| `GET` | `/entity/{entity_id}` | Retrieve a single entity record by its UUID. |
| `POST` | `/search` | Semantic similarity search over the Qdrant vector index. |

Interactive documentation is available at `http://localhost:8000/docs` (Swagger UI) and `http://localhost:8000/redoc`.

## Example requests

**Health check**

```bash
curl http://localhost:8000/health
```

**Submit a crawl job**

```bash
curl -X POST http://localhost:8000/crawl \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{"url": "https://example.com", "max_depth": 2}'
```

**Query entities by text**

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{"query": "OpenAI", "limit": 5}'
```

**Retrieve a single entity**

```bash
curl http://localhost:8000/entity/3fa85f64-5717-4562-b3fc-2c963f66afa6 \
  -H "X-API-Key: your-api-key"
```

**Semantic search**

```bash
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{"query": "AI research organisations", "limit": 10}'
```

## Response format

Every endpoint (except `/health`) returns a `PhantomResponse` envelope:

```json
{
  "data": {},
  "sources": ["https://example.com/page"],
  "confidence": 0.85,
  "freshness": "2025-04-10T14:32:00+00:00",
  "related": []
}
```

| Field | Type | Description |
|---|---|---|
| `data` | object or array | The primary result payload. Shape varies by endpoint. |
| `sources` | array of strings | URLs of the crawled pages that contributed to this result. |
| `confidence` | float 0–1 | Average confidence score across results (0.0 when not applicable). |
| `freshness` | ISO 8601 datetime or null | Timestamp of the most recently fetched source page. |
| `related` | array of strings | Related entity IDs or URLs (currently empty, reserved for future use). |

## Authentication

Authentication uses the `X-API-Key` HTTP header.

- If `API_KEY` is set to a non-empty value, every request to `/crawl`, `/query`, `/entity/{id}`, and `/search` must include a matching `X-API-Key` header. Requests with a missing or incorrect key receive `401 Unauthorized`.
- If `API_KEY` is empty (the default in `.env.example`), authentication is disabled and no header is required. This is the intended behaviour for local development.

## Configuration

All configuration is via environment variables. See `../.env.example` for the full list.

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_HOST` | `localhost` | PostgreSQL host |
| `POSTGRES_PORT` | `5432` | PostgreSQL port |
| `POSTGRES_DB` | `phantombot` | PostgreSQL database name |
| `POSTGRES_USER` | `phantombot` | PostgreSQL user |
| `POSTGRES_PASSWORD` | `localdev` | PostgreSQL password |
| `QDRANT_HOST` | `localhost` | Qdrant host |
| `QDRANT_PORT` | `6335` | Qdrant gRPC port |
| `QDRANT_COLLECTION` | `phantombot` | Qdrant collection name |
| `REDIS_URL` | `redis://localhost:6379` | Redis connection URL (used by `/crawl` to enqueue jobs) |
| `REDIS_STREAM_NAME` | `phantombot:crawled` | Redis Stream name |
| `API_HOST` | `0.0.0.0` | Bind address (used when running via `python -m api`) |
| `API_PORT` | `8000` | Listen port (used when running via `python -m api`) |
| `API_KEY` | `` (empty) | API key for authentication; empty disables auth |

## Testing

```bash
cd api
source .venv/bin/activate
pytest tests/ -v
```

Tests cover all three route modules (crawl, query, search) using FastAPI's `TestClient` with mocked PostgreSQL, Qdrant, and Redis dependencies.
