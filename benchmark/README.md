# Phantombot Benchmark Suite

A reproducible benchmark comparing **Phantombot**, **Firecrawl**, and **Crawl4AI** across crawl speed, entity extraction quality, schema detection, and reliability.

## Prerequisites

- Python 3.12+
- Docker and Docker Compose (for Firecrawl self-hosted)
- Go 1.23+ (for building Phantombot crawler)
- ~4GB free disk space (Docker images)
- ~8GB RAM (running all three tools)

## Quick Start

```bash
# Install dependencies
make -C benchmark install

# Run unit tests
make -C benchmark test

# Run full benchmark (all tools)
make -C benchmark benchmark

# Run Phantombot only (no Docker needed)
make -C benchmark benchmark-quick
```

## CLI Usage

```bash
# Run full benchmark (all tools, all metrics)
python -m benchmark.runner

# Run specific tools only
python -m benchmark.runner --tools phantombot,crawl4ai

# Custom timeout per URL
python -m benchmark.runner --timeout 60

# Custom output directory
python -m benchmark.runner --output /tmp/benchmark-results
```

## Corpus

The benchmark uses a fixed set of 25 URLs across six page categories:

| Category | Count | Description |
|---|---|---|
| Static HTML / Documentation | 5 | Wikipedia, Python docs, MDN |
| JS-Heavy SPAs | 4 | React, Angular, Vue docs, Hacker News |
| JSON-LD Structured Data | 4 | Recipe, movie, event pages |
| API Documentation | 4 | Swagger, GitHub API docs |
| News Articles (Entity-Rich) | 4 | Reuters, BBC, AP News |
| E-commerce Product Pages | 4 | Etsy, Target, Best Buy, Walmart |

URLs are defined in `corpus.json`. Ground truth annotations are in `ground_truth/`.

## Metrics

### M1: Crawl Speed
- Time to first result (seconds)
- Total corpus time (seconds)
- Pages per second

### M2: Extraction Quality
- Entity precision, recall, F1
- Entity type accuracy
- Uses Levenshtein distance (threshold=2) for fuzzy matching

### M3: Schema Detection
- JSON-LD detection rate
- OpenAPI detection rate
- Schema completeness (key fields found)
- False positive rate

### M4: Reliability
- Success rate (pages returning usable content)
- Error rate by page category

## Fairness Controls

All tools are compared under identical conditions:
- Same URLs (from `corpus.json`)
- Same timeout budget (30s per URL)
- Same entity extraction model (SpaCy `en_core_web_sm`)
- Same schema detection logic
- Sequential execution (no resource contention)
- 10-second cool-down between tools

## Output

Results are saved to `benchmark/results/{timestamp}/`:
- `raw_results.json` -- machine-readable results
- `report.md` -- human-readable markdown report

## Project Structure

```
benchmark/
  corpus.json              # URL manifest (25 URLs)
  ground_truth/            # Manual entity and schema annotations
  adapters/
    base.py                # Abstract adapter interface
    phantombot_adapter.py  # Phantombot direct mode
    crawl4ai_adapter.py    # Crawl4AI Python library
    firecrawl_adapter.py   # Firecrawl self-hosted Docker
  metrics/
    speed.py               # Crawl speed metrics
    extraction.py          # Entity extraction quality
    schema.py              # Schema detection accuracy
    reliability.py         # Success/error rates
  runner.py                # Benchmark orchestrator + CLI
  report.py                # Report generation
  config.py                # Configuration dataclass
  tests/                   # Unit tests
  results/                 # Output directory (gitignored)
  Makefile                 # Convenience targets
```

## Adding a New Tool

1. Create `benchmark/adapters/newtool_adapter.py` implementing `BaseAdapter`
2. Register it in `benchmark/runner.py` `ADAPTER_REGISTRY`
3. Add tests in `benchmark/tests/test_newtool_adapter.py`
4. Run: `python -m benchmark.runner --tools phantombot,newtool`
