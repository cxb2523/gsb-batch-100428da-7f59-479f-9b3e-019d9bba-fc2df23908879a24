# Contributing to Phantombot

Thank you for your interest in contributing to Phantombot. This guide covers everything you need to get started: setting up a local development environment, code style expectations, the pull request process, and how to extend the system with new pipelines or API routes.

---

## Table of Contents

1. [Development Environment Setup](#development-environment-setup)
2. [Code Style](#code-style)
3. [Branch Naming](#branch-naming)
4. [Commit Messages](#commit-messages)
5. [Pull Request Process](#pull-request-process)
6. [Testing Requirements](#testing-requirements)
7. [Architecture Overview](#architecture-overview)
8. [Adding a New Extraction Pipeline](#adding-a-new-extraction-pipeline)
9. [Adding a New API Route](#adding-a-new-api-route)
10. [Reporting Issues](#reporting-issues)

---

## Development Environment Setup

Phantombot is composed of three main components:

| Component      | Language   | Location         |
|----------------|------------|------------------|
| Crawler        | Go         | `crawler/`       |
| Intelligence   | Python     | `intelligence/`  |
| API            | Python     | `api/`           |

Infrastructure (Redis, PostgreSQL, Qdrant) is managed with Docker Compose.

### Prerequisites

- Docker and Docker Compose (v2+)
- Go 1.25 or later
- Python 3.11 or later

### 1. Start infrastructure services

```bash
docker compose up -d redis postgres qdrant
```

This starts Redis (stream broker), PostgreSQL (structured storage), and Qdrant (vector store). Wait for all health checks to pass before proceeding.

### 2. Set up the crawler (Go)

```bash
cd crawler
go mod download
go build ./...
```

To run the crawler locally:

```bash
go run ./cmd/phantombot --help
```

### 3. Set up the intelligence worker (Python)

```bash
cd intelligence
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

To run the worker:

```bash
python -m intelligence.worker
```

### 4. Set up the API server (Python)

```bash
cd api
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

To run the API:

```bash
uvicorn api.main:app --reload
```

### Environment variables

Both Python services read configuration from environment variables. Copy any `.env.example` file (if present) to `.env` and adjust as needed. At minimum you will need:

- `REDIS_URL` — defaults to `redis://localhost:6379`
- `POSTGRES_PASSWORD` — defaults to `localdev` for local Docker Compose
- `DATABASE_URL` — connection string for PostgreSQL

---

## Code Style

### Go (crawler)

- All Go code must be formatted with `gofmt` or `goimports` before committing.
- Run `go vet ./...` and ensure there are no warnings.
- Follow the conventions described in [Effective Go](https://go.dev/doc/effective_go).
- Exported symbols must have doc comments.

```bash
gofmt -w ./
go vet ./...
```

### Python (intelligence, api)

- No specific linter is enforced yet, but code should be readable and consistently styled.
- Follow [PEP 8](https://peps.python.org/pep-0008/) for formatting.
- Type hints are strongly encouraged on all public functions and methods.
- Keep imports sorted (standard library, third-party, local) with a blank line between groups.

---

## Branch Naming

Use the following prefixes to categorize branches:

| Prefix    | Purpose                                      |
|-----------|----------------------------------------------|
| `feat/`   | New features or capabilities                 |
| `fix/`    | Bug fixes                                    |
| `docs/`   | Documentation changes only                  |
| `ci/`     | CI/CD pipeline changes                       |
| `test/`   | Adding or improving tests without feature changes |
| `refactor/` | Code restructuring without behavior change |

Examples:

```
feat/add-sentiment-pipeline
fix/crawler-robots-txt-timeout
docs/update-contributing-guide
```

Branch names should be lowercase and use hyphens as word separators.

---

## Commit Messages

Phantombot uses [Conventional Commits](https://www.conventionalcommits.org/). Every commit message must follow this format:

```
<type>(<scope>): <short description>

[optional body]

[optional footer]
```

### Types

| Type       | When to use                                              |
|------------|----------------------------------------------------------|
| `feat`     | A new feature                                            |
| `fix`      | A bug fix                                                |
| `docs`     | Documentation only changes                              |
| `ci`       | Changes to CI/CD configuration files and scripts        |
| `style`    | Formatting changes that do not affect meaning           |
| `test`     | Adding or correcting tests                              |
| `refactor` | Code change that neither fixes a bug nor adds a feature |
| `chore`    | Maintenance tasks (dependency bumps, build tooling)     |

### Examples

```
feat(intelligence): add sentiment extraction pipeline
fix(crawler): respect crawl-delay directive in robots.txt
docs(api): document /search query parameters
test(intelligence): add unit tests for entity pipeline
ci: add Go test workflow
```

The short description must be lowercase and must not end with a period. Keep it under 72 characters.

---

## Pull Request Process

1. **Fork** the repository and clone your fork locally.
2. **Create a branch** from `main` following the branch naming conventions above.
3. **Make your changes**, writing tests as you go.
4. **Run all tests** and confirm they pass (see [Testing Requirements](#testing-requirements)).
5. **Push** your branch to your fork.
6. **Open a pull request** against the `main` branch of the upstream repository.
7. Fill out the pull request template completely.
8. A maintainer will review your PR. Address any feedback by pushing additional commits to the same branch — do not force-push unless asked.
9. Once approved and all checks pass, a maintainer will merge the PR.

Pull requests that do not include tests for new functionality, or that break existing tests, will not be merged until those issues are resolved.

---

## Testing Requirements

- All existing tests must pass before a pull request will be considered.
- New features must include tests that cover the intended behavior.
- Bug fixes should include a regression test that would have caught the original bug.

### Running Go tests

```bash
cd crawler
go test ./...
```

### Running Python tests

Each Python package has its own virtual environment and test suite:

```bash
# Intelligence worker
cd intelligence
source .venv/bin/activate
pytest

# API server
cd api
source .venv/bin/activate
pytest
```

---

## Architecture Overview

```
crawler/                  Go crawler service
  cmd/phantombot/         Entry point
  internal/
    fetcher/              HTTP fetching and content extraction
    frontier/             URL frontier (Redis-backed)
    publisher/            Publishes crawled pages to Redis Streams
    robots/               robots.txt parsing and enforcement

intelligence/             Python intelligence worker
  intelligence/
    worker.py             Reads from Redis Streams, runs pipelines
    pipelines/            Extraction pipelines (one file per pipeline)
    models.py             Shared data models
    store.py              Writes results to PostgreSQL and Qdrant
    config.py             Environment-based configuration

api/                      Python REST API
  api/
    main.py               FastAPI application setup
    routes/               One file per route group (crawl, search, query)
    dependencies.py       Shared FastAPI dependencies
    models.py             Request/response schemas
    config.py             Environment-based configuration

store/                    Database migrations and shared schema
fixtures/                 Test fixtures and seed data
```

All three services communicate through Redis Streams and the shared databases. The crawler publishes crawled page data to a Redis Stream. The intelligence worker consumes that stream, runs extraction pipelines, and persists results. The API serves data from PostgreSQL and Qdrant.

---

## Adding a New Extraction Pipeline

Follow these steps to add a new intelligence pipeline:

### 1. Create the pipeline module

Create a new file in `intelligence/intelligence/pipelines/`. Name it after the domain concept it extracts (e.g., `sentiment.py`).

Your pipeline class must inherit from `BasePipeline` defined in `pipelines/base.py` and implement the `run` method:

```python
# intelligence/intelligence/pipelines/sentiment.py

from intelligence.models import CrawlMessage
from intelligence.pipelines.base import BasePipeline


class SentimentPipeline(BasePipeline):
    """Extracts sentiment signals from crawled page content."""

    def run(self, message: CrawlMessage) -> dict:
        # Your extraction logic here.
        return {"sentiment": "positive", "confidence": 0.87}
```

### 2. Write tests

Create a corresponding test file in `intelligence/tests/`:

```
intelligence/tests/test_sentiment_pipeline.py
```

Tests should cover normal operation, edge cases (empty content, missing fields), and any error handling your pipeline performs.

### 3. Register the pipeline in the worker

Open `intelligence/intelligence/worker.py` and instantiate your pipeline alongside the existing ones:

```python
from intelligence.pipelines.sentiment import SentimentPipeline

# Inside Worker.__init__:
self._sentiment_pipeline = SentimentPipeline()
```

Then call it in the message processing loop, following the same pattern used by `EntityPipeline` and `SchemaPipeline`.

### 4. Update the store if needed

If your pipeline produces data that needs to be persisted in a new way, add the appropriate logic to `intelligence/intelligence/store.py` and create a database migration in `store/migrations/` if the schema changes.

---

## Adding a New API Route

### 1. Create a route module

Create a new file in `api/api/routes/`. For example, `api/api/routes/stats.py`:

```python
from fastapi import APIRouter, Depends

from api.dependencies import get_db

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/")
async def get_stats(db=Depends(get_db)):
    # Implementation here.
    return {}
```

### 2. Register the router

In `api/api/main.py`, import and include your router:

```python
from api.routes.stats import router as stats_router

app.include_router(stats_router)
```

### 3. Write tests

Add tests in `api/tests/` covering the expected responses, error cases, and any validation logic. Use FastAPI's `TestClient` for request-level tests.

---

## Reporting Issues

Before opening an issue, please search existing issues to avoid duplicates.

When reporting a bug, use the **Bug Report** template and include:

- A clear, specific description of the problem.
- The exact steps to reproduce it.
- What you expected to happen and what actually happened.
- Your environment: OS, Go version, Python version, Docker version.
- Any relevant log output or error messages.

When suggesting a new feature, use the **Feature Request** template and describe the use case clearly. Explain why the feature would be valuable and, if possible, sketch out how you imagine it working.

For security vulnerabilities, do **not** open a public issue. Contact the maintainers directly.
