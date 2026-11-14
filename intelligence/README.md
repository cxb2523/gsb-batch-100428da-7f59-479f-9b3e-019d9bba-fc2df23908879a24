# Intelligence

The Python intelligence layer of Phantombot. It consumes crawled pages from a Redis Stream, runs extraction pipelines to identify named entities and structured schemas, and stores the results in PostgreSQL (structured records) and Qdrant (vector embeddings for semantic search).

## Prerequisites

- Python 3.12+
- Redis, PostgreSQL, and Qdrant running and reachable

## Setup

```bash
cd intelligence
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m spacy download en_core_web_sm
```

Copy `../.env.example` to `.env` at the project root (or export the variables directly) before running.

## Run

```bash
python -m intelligence.worker
```

The worker creates the Redis consumer group on first start (`BUSYGROUP` errors on subsequent starts are silently ignored). It then enters a loop, reading one message at a time from the stream, running all pipelines, acknowledging the message, and repeating. Shut down cleanly with `Ctrl-C`.

## Configuration

All configuration is via environment variables. See `../.env.example` for the full list.

| Variable | Default | Description |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379` | Redis connection URL |
| `REDIS_STREAM_NAME` | `phantombot:crawled` | Redis Stream to consume from |
| `REDIS_CONSUMER_GROUP` | `intelligence` | Redis consumer group name |
| `POSTGRES_HOST` | `localhost` | PostgreSQL host |
| `POSTGRES_PORT` | `5432` | PostgreSQL port |
| `POSTGRES_DB` | `phantombot` | PostgreSQL database name |
| `POSTGRES_USER` | `phantombot` | PostgreSQL user |
| `POSTGRES_PASSWORD` | `localdev` | PostgreSQL password |
| `QDRANT_HOST` | `localhost` | Qdrant host |
| `QDRANT_PORT` | `6335` | Qdrant gRPC port |
| `QDRANT_COLLECTION` | `phantombot` | Qdrant collection name |
| `SPACY_MODEL` | `en_core_web_sm` | SpaCy model to load for NER |

## Pipelines

### Entity pipeline (`pipelines/entity.py`)

Uses SpaCy NER to extract named entities from page text. HTML is first stripped of `<script>`, `<style>`, `<nav>`, `<footer>`, and `<header>` elements before being converted to plain text with BeautifulSoup. Duplicate entity/type pairs within a page are deduplicated.

Supported entity types: `PERSON`, `ORG`, `GPE`, `PRODUCT`, `MONEY`, `LOC`, `FAC`, `NORP`, `EVENT`.

Extracted entities are written to the `entities` PostgreSQL table and also indexed as vector embeddings in Qdrant (using `all-MiniLM-L6-v2`) to support semantic search.

### Schema pipeline (`pipelines/schema.py`)

Detects structured schemas embedded in HTML. Currently handles two formats:

- **JSON-LD** — finds `<script type="application/ld+json">` blocks and parses them as JSON.
- **OpenAPI** — finds JSON in `<pre><code>` blocks that contain `openapi`, `swagger`, or `info` + `paths` keys.

Detected schemas are written to the `schemas` PostgreSQL table.

## Adding a new pipeline

1. Create a new file in `intelligence/pipelines/`, for example `intelligence/pipelines/mypipeline.py`.
2. Define a class that extends `BasePipeline` and implements the `extract(self, html: str, source_url: str) -> list` method.
3. Instantiate the pipeline in `intelligence/worker.py` inside `Worker.__init__`, then call `pipeline.extract(msg.html, msg.url)` and store the results inside `Worker.process_message`.

```python
# intelligence/pipelines/mypipeline.py
from intelligence.pipelines.base import BasePipeline

class MyPipeline(BasePipeline):
    def extract(self, html: str, source_url: str) -> list:
        # parse html, return a list of extracted items
        return []
```

```python
# intelligence/worker.py — inside Worker.__init__
from intelligence.pipelines.mypipeline import MyPipeline
self._my_pipeline = MyPipeline()

# inside Worker.process_message
results = self._my_pipeline.extract(msg.html, msg.url)
if results:
    self._store.save_my_results(page_id, results)
```

## Testing

```bash
cd intelligence
source .venv/bin/activate
pytest tests/ -v
```

Tests cover the entity pipeline, schema pipeline, store interactions, and the worker message-processing loop. External services (Redis, PostgreSQL, Qdrant) are mocked in the test fixtures.
