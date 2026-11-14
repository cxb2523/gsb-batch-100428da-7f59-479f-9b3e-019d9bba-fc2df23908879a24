# gmaps-extractor

[![PyPI version](https://img.shields.io/pypi/v/gmaps-extractor)](https://pypi.org/project/gmaps-extractor/)
[![Python versions](https://img.shields.io/pypi/pyversions/gmaps-extractor)](https://pypi.org/project/gmaps-extractor/)
[![CI](https://github.com/promisingcoder/GoogleMapsCollector/actions/workflows/ci.yml/badge.svg)](https://github.com/promisingcoder/GoogleMapsCollector/actions/workflows/ci.yml)

Search Google Maps for places in an area and get their contact details and opening hours, over plain HTTP (no
browser, no API key).

Give it an area name and a search text (`"Austin, Texas"`, `"coffee shop"`). It looks the area up on OpenStreetMap,
covers it with a grid of search cells, searches Google Maps in each cell, removes duplicates and, for each place,
fetches its opening hours.

> **Version 3.0.0 is a breaking release.** It is a smaller library: search plus opening hours. Reviews, the
> local API server, the `pb` decoder tools, the async/v2 collectors and checkpointing are gone. See
> [CHANGELOG.md](CHANGELOG.md) and [the migration notes](docs/migration.md).

## Install

```sh
pip install gmaps-extractor
```

Python 3.9 or newer. The only dependency is `httpx`.

## Command line

```sh
gmaps-collect "Austin, Texas" "coffee shop" --max-results 10
gmaps-collect "Lyon, France" "bakery" --no-hours -o bakeries.csv
```

```text
gmaps-collect AREA QUERY [-n MAX_RESULTS] [--no-hours] [-o FILE] [-f json|jsonl|csv]
              [--proxy URL] [-w WORKERS] [--min-interval SECONDS] [-b BUFFER_KM] [--cell-size METRES] [-q]
```

- Output goes to standard output as JSON unless `-o` is given; the format is taken from the file extension
  (`.json`, `.jsonl`, `.csv`) or from `-f`.
- Progress and a final statistics line go to standard error; `-q` silences them.
- `--no-hours` skips the opening-hours request per place (one request per place saved).
- `python -m gmaps_extractor ...` works the same way.

## Python

```python
from gmaps_extractor import collect

places = collect("Austin, Texas", "coffee shop", max_results=10)
for place in places:
    print(place["name"], place["phone"], place["hours"])
print(places.stats)  # cells searched, places found, hours found, failed requests, ...
```

`collect()` returns a list of dicts (a `CollectionResult`, which is a `list` with a `stats` attribute).

Lower level, one search page and one place:

```python
from gmaps_extractor import GMapsClient

with GMapsClient() as client:
    results = client.search_page("coffee shop", lat=30.2672, lng=-97.7431)
    first = results[0]
    hours = client.place_hours(first["hex_id"], first["name"], first["latitude"], first["longitude"], first["ftid"])
```

`search_page()` results also carry `hex_id` and `ftid`, the two identifiers `place_hours()` needs.

## Output fields

| Field | Type | Notes |
| --- | --- | --- |
| `name` | string | |
| `address` | string or null | |
| `phone` | string or null | as Google shows it |
| `website` | string or null | Google's redirect wrapper removed |
| `rating` | number or null | 1.0-5.0 |
| `reviews_count` | integer or null | the number only |
| `category` | string or null | first category |
| `categories` | list of strings | |
| `latitude`, `longitude` | number or null | |
| `place_id` | string or null | Google place id (`ChIJ...`) |
| `google_maps_url` | string or null | a [Maps URLs](https://developers.google.com/maps/documentation/urls/get-started) link built from the name and `place_id` |
| `hours` | object or null | `{"monday": "7 AM-6 PM", "friday": "11 AM-2 PM, 5-10 PM", "sunday": "Closed", ...}`; null when Google lists no hours, with `--no-hours`, or when the request failed |

In CSV, `categories` is joined with `"; "` and `hours` is written as a JSON object.

## Proxy

A proxy is optional. It is read only from the `proxy` argument / `--proxy` option or from environment variables, in
this order:

1. `proxy="http://user:pass@host:port"` (Python) or `--proxy` (command line)
2. `GMAPS_PROXY_URL` (any URL httpx accepts, e.g. `http://user:pass@host:port`)
3. `GMAPS_PROXY_HOST` (`host:port`) with optional `GMAPS_PROXY_USER` and `GMAPS_PROXY_PASS`

The URL is passed to httpx as is; the library has no proxy pools or rotation. Prefer the environment variables on
the command line so the login stays out of your shell history. There is no configuration file.

## How requests are made

- One OpenStreetMap Nominatim request per collection to turn the area name into a bounding box.
- One or more Google Maps search requests per grid cell (cells are searched from the centre of the area outwards).
- With hours on, one place request per place.
- At most one request every `min_interval` seconds (default 0.5) per client, `workers` (default 4) in parallel.
- A failed request (network error, error status, consent page, empty answer) is retried once after 2 seconds.
  A cell that still fails is skipped and the collection goes on with what it found; `stats` counts those cells as
  `cells_rate_limited` (HTTP 429) or `cells_failed` (any other request error), `cells_not_searched` counts the
  cells never reached (e.g. `--max-results` stopped early), and `hours_rate_limited` counts opening-hours
  requests that hit HTTP 429 (those rows keep `hours: null`). When every search request fails, `collect()`
  raises `RateLimitError` if it was all HTTP 429, else `RequestError`, instead of returning an empty list.

## Limits

- This uses Google Maps' internal web endpoints, not the official Places API. Their format is undocumented and can
  change at any time; when it does, results come back empty until the parsers are updated.
- Google limits how much one connection can search. Large areas or many runs from one IP address get blocked
  (HTTP 429 or empty answers). Use small areas, `--max-results`, and a proxy you are allowed to use.
- Results depend on Google's ranking and on the grid: a search is not a complete list of every business.
- Opening hours text is in English as Google writes it (`"Open 24 hours"`, split hours joined with `", "`).

## Be polite

Use this for modest, legitimate lookups. Respect Google's Terms of Service and the
[Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/), keep request rates low, and do not
use the data to spam or profile people. You are responsible for how you use it.

## Development

```sh
pip install -e ".[dev]"
pytest -q
```

The tests are offline: they use small synthetic answers in `tests/fixtures/` and fail on any real network request.
`tests/test_packaging.py` builds the wheel and sdist with a planted `config.py` and `.env` and checks that neither is
packaged.

## License

MIT, see [LICENSE](LICENSE).
