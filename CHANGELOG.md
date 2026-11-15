# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Added
- Discovery request budget: `discover(..., max_requests=N)` and
  `SiteDiscoverer(..., max_requests=N)` cap feed/sitemap/robots.txt requests and
  stop fetching once the budget is spent.
- `DiscoveryResult` (a `list` subclass) carries truncation metadata:
  `truncated`, `budget_exhausted`, `limit_truncated`, `remaining_entries`,
  `remaining_candidates`, `unexpanded_candidates`, plus `meta()` / `has_more`.
- CLI `news-fetch discover --max-requests N` and a stderr truncation notice.

## [1.0.0]

### Added
- Production-stable release of the independent extraction engine.
- CLI: `news-fetch`, `news-fetch batch` (JSONL), `news-fetch discover`.
- `fetch_iter` for memory-conscious streaming bulk results.
- Strategy plugin API: `register_strategy` / `ExtractionStrategy` / `CallableStrategy`.
- Optional browser rendering: `pip install news-fetch[browser]` + `render=True` / `browser_fallback=True`.
- Disk cache (`cache=True`) and `respect_robots=True`.
- HTTP 429 `Retry-After` handling in the sync client.
- Extra HTML regression fixtures.

### Changed
- Version / user-agent bumped to 1.0; classifier set to Production/Stable.
- README optimized for PyPI discoverability (news scraper / article extractor queries).
- In-repo `docs/` removed (documentation lives in a separate repository).

## [0.6.0]

### Added
- Evidence → rank → confidence architecture (`article.confidence`, `article.extraction`).
- Page-type detection and strict / threshold gates.
- `LowConfidenceExtractionError`, `article.to_json()`.

## [0.5.1]

### Added
- Proxy / proxy pools, rotation, `request_delay`, progress callbacks, `iter_fetch`.

## [0.5.0]

### Changed
- Independent extraction engine; removed `newspaper4k` as the core scraper.
