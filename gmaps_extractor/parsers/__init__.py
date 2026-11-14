"""Parsers for Google Maps responses.

Google answers with deeply nested JSON arrays that have no field names. The
parsers read fixed array positions that were found by inspecting live answers;
Google can move them without notice.

- :mod:`~gmaps_extractor.parsers.response` -- strip the ``)]}'`` prefix and load the JSON.
- :mod:`~gmaps_extractor.parsers.business` -- places in a search answer.
- :mod:`~gmaps_extractor.parsers.hours` -- opening hours in a place answer.
"""

from .business import extract_businesses
from .hours import extract_hours
from .response import parse_google_response, safe_get

__all__ = ["extract_businesses", "extract_hours", "parse_google_response", "safe_get"]
