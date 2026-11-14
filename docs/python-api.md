# Python API

```python
from gmaps_extractor import collect, GMapsClient, FIELDS, write_rows
```

## `collect(area, query, **options) -> CollectionResult`

Search an area and return one row per place (see [Output format](output-format.md)).

| Option | Default | Meaning |
| --- | --- | --- |
| `max_results` | `None` | stop once this many places are found |
| `hours` | `True` | one place request per place for its opening hours |
| `proxy` | `None` | proxy URL; otherwise the `GMAPS_PROXY_*` variables are read |
| `workers` | `4` | parallel requests (1-16) |
| `min_interval` | `0.5` | minimum seconds between two requests to Google |
| `buffer_km` | `5.0` | margin around the area box when dropping far-away places |
| `cell_size_meters` | by area size | grid cell size (1 km for small areas up to 100 km for countries) |
| `max_pages_per_cell` | `2` | search pages per cell at most |
| `timeout` | `30.0` | seconds per HTTP request |
| `client` | new client | a `GMapsClient` to reuse |
| `boundary` | Nominatim lookup | `(area_box, filter_box)` as two `AreaBoundary` objects, to skip the lookup |

The result is a `list` of dicts with a `stats` dict: `cells_total`, `cells_searched`, `cells_rate_limited`,
`cells_failed`, `cells_not_searched`, `search_requests_failed`, `places_found`, `places_outside_area`,
`hours_requested`, `hours_found`, `hours_failed`, `hours_rate_limited`. A cell that fails is skipped and the
collection keeps what the other cells found: `cells_rate_limited` counts cells that failed with HTTP 429,
`cells_failed` cells that failed with any other request error, and `cells_not_searched` the cells never reached
(e.g. `max_results` stopped early). `hours_rate_limited` is the part of `hours_failed` caused by HTTP 429.

Raises `BoundaryError` when the area cannot be resolved and `RequestError` when every search request failed
(`RateLimitError` when every failure was HTTP 429).

## `GMapsClient`

```python
with GMapsClient(proxy=None, timeout=30.0, min_interval=0.5) as client:
    results = client.search_page("coffee shop", lat=30.2672, lng=-97.7431, offset=0)
    hours = client.place_hours(results[0]["hex_id"], results[0]["name"],
                               results[0]["latitude"], results[0]["longitude"], results[0]["ftid"])
```

- `search_page(query, lat, lng, offset=0, results_count=20, max_radius=5000, viewport_dist=10000)` returns the
  places of one page; each also has `hex_id` and `ftid`, the identifiers `place_hours()` needs.
- `place_hours(hex_id, name=None, lat=0.0, lng=0.0, ftid=None)` returns `{"monday": "...", ...}` or `None`.

The client is thread-safe and spaces its requests by `min_interval` seconds.

## Other helpers

- `FIELDS`: the output field names, in order.
- `write_rows(rows, stream, fmt)`: write rows as `json`, `jsonl` or `csv`.
- `resolve_proxy(proxy=None)`: the proxy URL that would be used.
- `gmaps_extractor.geo`: `get_area_boundary()`, `generate_grid()`, `AreaBoundary`, `GridCell`.

## Errors

All exceptions derive from `GMapsExtractorError`:

| Exception | When |
| --- | --- |
| `BoundaryError` | the area name is unknown or the Nominatim lookup failed |
| `ConfigurationError` | an incomplete proxy configuration in the environment |
| `RequestError` | a Google Maps request failed, also after its one retry |
| `RateLimitError` | subclass of `RequestError`: HTTP 429 |

## Logging

The library logs to the `gmaps_extractor` logger and is silent by default:

```python
import logging
logging.basicConfig(level=logging.INFO)
```
