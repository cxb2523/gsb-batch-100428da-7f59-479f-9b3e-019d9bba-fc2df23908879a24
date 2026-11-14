"""Search a whole area for a query and return one row per place."""

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from .client import GMapsClient, resolve_proxy
from .exceptions import GMapsExtractorError, RateLimitError, RequestError
from .geo import AreaBoundary, GridCell, generate_grid, get_area_boundary, is_in_boundary
from .urls import google_maps_url

logger = logging.getLogger(__name__)

#: The fields of every output row, in output order.
FIELDS = (
    "name",
    "address",
    "phone",
    "website",
    "rating",
    "reviews_count",
    "category",
    "categories",
    "latitude",
    "longitude",
    "place_id",
    "google_maps_url",
    "hours",
)

RESULTS_PER_PAGE = 400
MAX_RADIUS_METERS = 5000
VIEWPORT_DIST = 10000
MAX_WORKERS = 16


class CollectionResult(list):
    """The rows of one collection (a plain ``list`` of dicts) plus run statistics.

    ``stats`` holds ``area``, ``query``, ``cells_total``, ``cells_searched``,
    ``cells_rate_limited``, ``cells_failed``, ``cells_not_searched``,
    ``search_requests_failed``, ``places_found``, ``places_outside_area``,
    ``hours_requested``, ``hours_found``, ``hours_failed`` and
    ``hours_rate_limited``.
    """

    def __init__(self, rows: List[Dict[str, Any]], stats: Dict[str, Any]) -> None:
        super().__init__(rows)
        self.stats = stats


def to_row(place: Dict[str, Any], hours: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Build an output row (exactly :data:`FIELDS`) from a parsed search result."""
    row = {field: place.get(field) for field in FIELDS}
    row["categories"] = list(place.get("categories") or [])
    row["google_maps_url"] = google_maps_url(place.get("name"), place.get("place_id"))
    row["hours"] = hours
    return row


def _place_key(place: Dict[str, Any]) -> Optional[str]:
    return place.get("place_id") or place.get("hex_id") or place.get("name")


def _search_cell(
    client: GMapsClient, query: str, cell: GridCell, max_pages: int
) -> Tuple[List[Dict[str, Any]], int, int, bool]:
    """Search one cell page by page.

    Returns ``(places, failed_requests, rate_limited_failures, any_success)``.
    """
    places: List[Dict[str, Any]] = []
    failed = 0
    rate_limited = 0
    ok = False
    for page in range(max_pages):
        try:
            batch = client.search_page(
                query,
                cell.center_lat,
                cell.center_lng,
                offset=page * RESULTS_PER_PAGE,
                results_count=RESULTS_PER_PAGE,
                max_radius=MAX_RADIUS_METERS,
                viewport_dist=VIEWPORT_DIST,
            )
        except RateLimitError as exc:
            logger.warning("cell %d: %s", cell.cell_id, exc)
            failed += 1
            rate_limited += 1
            break
        except RequestError as exc:
            logger.warning("cell %d: %s", cell.cell_id, exc)
            failed += 1
            break
        ok = True
        places.extend(batch)
        if len(batch) < RESULTS_PER_PAGE:
            break
    return places, failed, rate_limited, ok


def _fetch_hours(
    client: GMapsClient, place: Dict[str, Any]
) -> Tuple[Optional[Dict[str, str]], bool, bool]:
    """Fetch one place's hours. Returns ``(hours, ok, rate_limited)``."""
    if not place.get("hex_id"):
        return None, False, False
    try:
        hours = client.place_hours(
            place["hex_id"],
            place.get("name"),
            place.get("latitude") or 0.0,
            place.get("longitude") or 0.0,
            place.get("ftid"),
        )
        return hours, True, False
    except RateLimitError as exc:
        logger.warning("hours for %r: %s", place.get("name"), exc)
        return None, False, True
    except RequestError as exc:
        logger.warning("hours for %r: %s", place.get("name"), exc)
        return None, False, False


def collect(
    area: str,
    query: str,
    *,
    max_results: Optional[int] = None,
    hours: bool = True,
    proxy: Optional[str] = None,
    workers: int = 4,
    buffer_km: float = 5.0,
    cell_size_meters: Optional[int] = None,
    max_pages_per_cell: int = 2,
    min_interval: float = 0.5,
    timeout: float = 30.0,
    client: Optional[GMapsClient] = None,
    boundary: Optional[Tuple[AreaBoundary, AreaBoundary]] = None,
) -> CollectionResult:
    """Search ``area`` for ``query`` and return one row per place.

    The area name is resolved to a bounding box with OpenStreetMap Nominatim,
    the box is covered with a grid of search cells (centre first), and each
    cell is searched on Google Maps. Places are de-duplicated by ``place_id``
    and places clearly outside the area (beyond ``buffer_km``) are dropped.
    With ``hours=True`` one place request per place adds its opening hours.

    Args:
        area: Area name, e.g. ``"Austin, Texas"``.
        query: Free-text search, e.g. ``"coffee shop"``.
        max_results: Stop once this many places are found (``None``: all).
        hours: Fetch opening hours (one extra request per place).
        proxy: Optional proxy URL; otherwise ``GMAPS_PROXY_*`` env vars are read.
        workers: Parallel requests (1-16).
        buffer_km: Margin around the area box when filtering places.
        cell_size_meters: Grid cell size; chosen from the area size if omitted.
        max_pages_per_cell: Search pages fetched per cell at most.
        min_interval: Minimum seconds between two requests to Google.
        timeout: Seconds per HTTP request.
        client: Use this :class:`GMapsClient` instead of creating one.
        boundary: ``(area_box, filter_box)`` to skip the Nominatim lookup.

    Raises:
        BoundaryError: the area name could not be resolved.
        RequestError: every search request failed (usually a block or no network).
        RateLimitError: every search request failed with HTTP 429; another
            proxy or a slower rate may help.
    """
    if not query or not query.strip():
        raise GMapsExtractorError("query is empty")
    if max_results is not None and max_results < 1:
        raise GMapsExtractorError("max_results must be at least 1")
    workers = max(1, min(int(workers), MAX_WORKERS))
    proxy_url = resolve_proxy(proxy)

    area_box, filter_box = boundary or get_area_boundary(area, buffer_km, proxy=proxy_url, timeout=timeout)
    cells = generate_grid(area_box, cell_size_meters)
    stats: Dict[str, Any] = {
        "area": area,
        "query": query,
        "cells_total": len(cells),
        "cells_searched": 0,
        "cells_rate_limited": 0,
        "cells_failed": 0,
        "cells_not_searched": 0,
        "search_requests_failed": 0,
        "places_found": 0,
        "places_outside_area": 0,
        "hours_requested": 0,
        "hours_found": 0,
        "hours_failed": 0,
        "hours_rate_limited": 0,
    }

    own_client = client is None
    if client is None:
        client = GMapsClient(proxy=proxy_url, timeout=timeout, min_interval=min_interval)
    found: Dict[str, Dict[str, Any]] = {}
    any_success = False
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for start in range(0, len(cells), workers):
                batch = cells[start : start + workers]
                results = pool.map(lambda c: _search_cell(client, query, c, max_pages_per_cell), batch)
                for places, failed, rate_limited, ok in results:
                    stats["cells_searched"] += 1
                    stats["search_requests_failed"] += failed
                    if not ok:
                        if rate_limited:
                            stats["cells_rate_limited"] += 1
                        else:
                            stats["cells_failed"] += 1
                    any_success = any_success or ok
                    for place in places:
                        key = _place_key(place)
                        if not key or key in found:
                            continue
                        lat, lng = place.get("latitude"), place.get("longitude")
                        if lat is not None and lng is not None and not is_in_boundary(lat, lng, filter_box):
                            stats["places_outside_area"] += 1
                            continue
                        found[key] = place
                logger.info("searched %d/%d cells, %d places", stats["cells_searched"], len(cells), len(found))
                if max_results and len(found) >= max_results:
                    break

            stats["cells_not_searched"] = len(cells) - stats["cells_searched"]
            if not any_success:
                if stats["cells_rate_limited"] == stats["cells_searched"] and stats["cells_searched"]:
                    raise RateLimitError(
                        "every search request hit HTTP 429 (rate limited); try another proxy or a slower rate"
                    )
                raise RequestError("every search request failed; Google may be blocking this connection")

            places = list(found.values())[:max_results] if max_results else list(found.values())
            stats["places_found"] = len(places)
            if hours:
                stats["hours_requested"] = len(places)
                fetched = list(pool.map(lambda p: _fetch_hours(client, p), places))
            else:
                fetched = [(None, True, False)] * len(places)
    finally:
        if own_client:
            client.close()

    rows = []
    for place, (place_hours, ok, rate_limited) in zip(places, fetched):
        if hours:
            if place_hours:
                stats["hours_found"] += 1
            if not ok:
                stats["hours_failed"] += 1
            if rate_limited:
                stats["hours_rate_limited"] += 1
        rows.append(to_row(place, place_hours))
    return CollectionResult(rows, stats)
