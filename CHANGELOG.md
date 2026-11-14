# Changelog

All notable changes to this project are documented in this file. The project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.0.0] - unreleased

A smaller, rewritten library with one job: search Google Maps for places in an area and return their contact
details and opening hours. This release breaks the 2.x API; see [docs/migration.md](docs/migration.md).

### Removed

- **Reviews.** Reviews moved out of the library: the `gmaps-enrich-reviews` command, `enrich_reviews_only.py`, the
  review parsers, `fetch_reviews`, `GMapsClient.reviews()`, all review options (`--reviews`, `--reviews-limit`,
  `enrich_reviews`, `reviews_limit`) and the `reviews_data` output field. `reviews_count` (a number) stays.
- The optional local API server (`gmaps-server`, `run_server.py`, the `[server]` extra) and the `pb` decoder tools
  (`gmaps_extractor.decoder`, the `pb_decoder/` web app).
- The 2.x collection layers: `GMapsExtractor`, `collect_businesses`, `collect_businesses_v2` (checkpoints, resume),
  `gmaps-collect-v2`, `AsyncGMapsClient` and the async collectors, the event system, `ProgressReporter`,
  `GMapsSettings`, subdivision mode, sponsored-result rows (`is_ad`).
- Configuration files: `config.py`, `config.example.py`, `_config_defaults.py` and `config_manager.py`. There is no
  configuration file any more (see *Security*).
- Repository clutter that was not part of the library: `collect.py`, `collect_v2.py`, `debug_response.txt`,
  `requirements.txt`, a committed `.coverage` file and the old test suite.
- Retry ladders: a failed request is now retried exactly once.

### Added

- `collect(area, query, ...)`: one call from an area name to rows with `name`, `address`, `phone`, `website`,
  `rating`, `reviews_count`, `category`, `categories`, `latitude`, `longitude`, `place_id`, `google_maps_url` and
  `hours`; returns a `CollectionResult` (a list with `stats`). `max_results` stops early.
- `GMapsClient.search_page()` and `GMapsClient.place_hours()` for single requests.
- `gmaps-collect AREA QUERY` with JSON, JSON Lines or CSV output, `--max-results`, `--no-hours`, `--proxy`.
- `google_maps_url`, a public Maps URLs link built from the name and `place_id`.
- `RequestError` / `RateLimitError`: when every search request fails, `collect()` raises instead of returning an
  empty list — `RateLimitError` when every failure was HTTP 429, so callers can switch proxy and retry.
- Partial failures are tolerated and counted: a cell whose search fails is skipped and the collection returns
  what the other cells found. New `stats` counters: `cells_rate_limited`, `cells_failed`, `cells_not_searched`
  (e.g. `max_results` stopped early) and `hours_rate_limited` (the HTTP 429 part of `hours_failed`).

### Changed

- Place requests: the request's first message now declares the right element count when a place has no feature id
  (requests without one were malformed and failed); `!`, `#` and `*` in a business name no longer corrupt the
  request; coordinates are always written in fixed-point notation; non-ASCII names are percent-encoded in the
  `Referer` header (they used to raise an encoding error).
- Opening hours: split hours (e.g. lunch and dinner) keep every time slot; Sunday is mapped correctly; hours are also
  found in the shortened place answers Google returns for some service-area businesses; day keys are always English.
- Grid cells are searched from the centre of the area outwards.
- Addresses no longer start with the place name.
- Nominatim requests send an identifying User-Agent, as its usage policy asks.
- CI runs the offline tests only (Python 3.9-3.13).

### Security

- Proxy settings come only from the `proxy` argument or from the `GMAPS_PROXY_URL` / `GMAPS_PROXY_HOST` /
  `GMAPS_PROXY_USER` / `GMAPS_PROXY_PASS` environment variables. The build drops any `config.py`, `config_*.py`,
  `secrets*.py` or `.env` file from the wheel and sdist, and a test builds both archives with such files planted and
  fails if any of them is packaged.

## [2.0.0] - 2025-02-09

Direct HTTP client without the local server, async API, settings object, event system, CI and packaging updates.

## [1.0.0] - 2024-12-01

First release on PyPI.

[3.0.0]: https://github.com/promisingcoder/GoogleMapsCollector/compare/v2.0.0...v3.0.0
[2.0.0]: https://github.com/promisingcoder/GoogleMapsCollector/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/promisingcoder/GoogleMapsCollector/releases/tag/v1.0.0
