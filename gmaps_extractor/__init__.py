"""gmaps-extractor: search Google Maps for places in an area and get their opening hours.

Quick start::

    from gmaps_extractor import collect

    for place in collect("Austin, Texas", "coffee shop", max_results=10):
        print(place["name"], place["hours"])

Lower level::

    from gmaps_extractor import GMapsClient

    with GMapsClient() as client:
        places = client.search_page("coffee shop", lat=30.2672, lng=-97.7431)
        hours = client.place_hours(places[0]["hex_id"], places[0]["name"],
                                   places[0]["latitude"], places[0]["longitude"], places[0]["ftid"])

The library logs through the ``gmaps_extractor`` logger and is silent by default.
"""

import logging as _logging

__version__ = "3.0.0"

from .client import GMapsClient, resolve_proxy  # noqa: E402
from .collector import FIELDS, CollectionResult, collect  # noqa: E402
from .exceptions import (  # noqa: E402
    BoundaryError,
    ConfigurationError,
    GMapsExtractorError,
    RateLimitError,
    RequestError,
)
from .output import write_rows  # noqa: E402

_logging.getLogger(__name__).addHandler(_logging.NullHandler())

__all__ = [
    "FIELDS",
    "BoundaryError",
    "CollectionResult",
    "ConfigurationError",
    "GMapsClient",
    "GMapsExtractorError",
    "RateLimitError",
    "RequestError",
    "__version__",
    "collect",
    "resolve_proxy",
    "write_rows",
]
