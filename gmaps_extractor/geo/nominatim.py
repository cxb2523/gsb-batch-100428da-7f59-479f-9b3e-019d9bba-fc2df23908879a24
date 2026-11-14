"""Resolve an area name to a bounding box with OpenStreetMap Nominatim.

Nominatim's usage policy allows at most one request per second and asks for an
identifying User-Agent: https://operations.osmfoundation.org/policies/nominatim/
This library sends one request per collection.
"""

from typing import Optional, Tuple

import httpx

from ..exceptions import BoundaryError
from .grid import AreaBoundary

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
DEFAULT_USER_AGENT = "gmaps-extractor/3.0 (+https://github.com/promisingcoder/GoogleMapsCollector)"


def get_area_boundary(
    area_name: str,
    buffer_km: float = 5.0,
    proxy: Optional[str] = None,
    timeout: float = 30.0,
    user_agent: str = DEFAULT_USER_AGENT,
) -> Tuple[AreaBoundary, AreaBoundary]:
    """Look up ``area_name`` and return ``(area, filter_area)``.

    ``area`` is the bounding box Nominatim returns; ``filter_area`` is the same
    box widened by ``buffer_km`` on every side, used to drop places that lie
    clearly outside the area.

    Raises:
        BoundaryError: the name is unknown or the lookup failed.
    """
    if not area_name or not area_name.strip():
        raise BoundaryError("area name is empty")
    params = {"q": area_name, "format": "json", "limit": 1}
    kwargs = {"timeout": timeout}
    if proxy:
        kwargs["proxy"] = proxy
    try:
        with httpx.Client(**kwargs) as client:
            response = client.get(NOMINATIM_URL, params=params, headers={"User-Agent": user_agent})
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise BoundaryError(f"area lookup failed ({type(exc).__name__})") from exc
    if not data:
        raise BoundaryError(f"no area found for {area_name!r}")
    try:
        south, north, west, east = (float(v) for v in data[0]["boundingbox"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BoundaryError("area lookup returned no bounding box") from exc

    area = AreaBoundary(name=area_name.split(",")[0].strip(), north=north, south=south, east=east, west=west)
    buffer_deg = max(0.0, buffer_km) / 111.0
    filter_area = AreaBoundary(
        name=area.name,
        north=north + buffer_deg,
        south=south - buffer_deg,
        east=east + buffer_deg,
        west=west - buffer_deg,
    )
    return area, filter_area
