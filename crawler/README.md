# Crawler

The Go crawler component of Phantombot. It fetches URLs respecting robots.txt, extracts links to follow, and publishes each crawled page as a message to a Redis Stream for downstream processing.

## Prerequisites

- Go 1.22+
- Redis running and reachable

## Build

```bash
cd crawler
go build ./cmd/phantombot/
```

This produces a `phantombot` binary in the `crawler/` directory.

## Run

```bash
./phantombot -urls https://example.com,https://other.com
```

Provide one or more comma-separated seed URLs. The crawler resolves links found on each page and follows them up to `CRAWLER_MAX_DEPTH` levels deep.

Shut down cleanly with `SIGINT` or `SIGTERM` (Ctrl-C). In-flight workers finish their current fetch before exiting.

## Configuration

All configuration is via environment variables. Copy `../.env.example` to `.env` and source it before running, or export the variables directly.

| Variable | Default | Description |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379` | Redis connection URL |
| `REDIS_STREAM_NAME` | `phantombot:crawled` | Redis Stream to publish crawl results to |
| `CRAWLER_CONCURRENCY` | `10` | Number of parallel fetch workers |
| `CRAWLER_MAX_DEPTH` | `3` | Maximum link depth to follow from seed URLs |
| `CRAWLER_USER_AGENT` | `Phantombot/1.0` | User-Agent header sent on every request |
| `CRAWLER_REQUEST_TIMEOUT_SECONDS` | `30` | Per-request HTTP timeout in seconds |
| `CRAWLER_POLITENESS_DELAY_MS` | `1000` | Minimum wait between consecutive requests to the same host (milliseconds) |

## Architecture

The crawler is composed of four internal packages:

**frontier** (`internal/frontier/`)
Thread-safe URL queue. Deduplicates URLs so each is fetched at most once. Tracks the last fetch time per host and sleeps to enforce the politeness delay before returning a URL whose host was recently visited.

**fetcher** (`internal/fetcher/`)
Thin HTTP client wrapper. Sets the configured User-Agent, follows up to 10 redirects, and returns the response body along with status code, content type, and headers. HTTP error status codes (4xx, 5xx) are surfaced in the result struct rather than as Go errors; only transport-level failures are returned as errors.

**robots** (`internal/robots/`)
Fetches and caches `robots.txt` for each host encountered. Uses the `temoto/robotstxt` library to check whether the configured User-Agent is permitted to crawl a given path. If `robots.txt` cannot be fetched, the URL is allowed by default.

**publisher** (`internal/publisher/`)
Writes a `CrawlResult` to the configured Redis Stream using `XADD`. Header values are JSON-encoded into a single field.

The main entry point (`cmd/phantombot/main.go`) wires these packages together. It seeds the frontier, starts `CRAWLER_CONCURRENCY` goroutines, and feeds discovered links back into the frontier until the queue is empty or a shutdown signal is received.

## Redis Streams message format

Each message published to the stream contains the following fields:

| Field | Type | Description |
|---|---|---|
| `url` | string | The fetched URL |
| `status_code` | string (int) | HTTP response status code |
| `content_type` | string | Value of the `Content-Type` response header |
| `html` | string | Raw response body |
| `headers` | string (JSON object) | All response headers as a JSON object |
| `fetched_at` | string (ISO 8601) | UTC timestamp when the fetch completed |
| `rendered` | string (`true`/`false`) | Whether the page was rendered by a headless browser (always `false` in current implementation) |
| `depth` | string (int) | Crawl depth relative to the seed URL |
| `job_id` | string (optional) | Job identifier when the crawl was initiated via the API |

## Testing

```bash
cd crawler
go test ./... -v
```

Tests cover the frontier (deduplication and politeness), fetcher (HTTP client behaviour), robots checker, and publisher (Redis Streams output format). Unit tests mock HTTP and Redis where appropriate.
