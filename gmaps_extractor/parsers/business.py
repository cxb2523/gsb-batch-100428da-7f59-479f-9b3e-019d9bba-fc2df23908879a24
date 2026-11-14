"""Places in a Google Maps search answer (``/search?tbm=map``).

Each organic result wraps its data in an array (usually ``entry[14]``) where:

- ``[11]``         name
- ``[18]``         full address
- ``[78]``         place_id (``ChIJ...``)
- ``[10]``         hex id (``0x...:0x...``), needed for the place request
- ``[89]``         feature id (``/g/...``), optional hint for the place request
- ``[4][7]``       rating (1.0-5.0)
- ``[4][8]``       number of reviews
- ``[9][2]``, ``[9][3]`` latitude, longitude
- ``[178][0][0]``  phone number
- ``[7]``          website (``/url?q=`` redirect or plain URL)
- ``[13]``         categories (list of strings)

These positions are reverse-engineered and may change without notice.
"""

from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from .response import safe_get

_BASE64_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")


def extract_website(biz: List[Any]) -> Optional[str]:
    """Return the website from ``biz[7]``, unwrapping Google's ``/url?q=`` redirect."""
    contact = safe_get(biz, 7)
    if not isinstance(contact, list):
        return None
    for item in contact[:5]:
        if not isinstance(item, str):
            continue
        if "/url?q=" in item:
            values = parse_qs(urlparse(item).query).get("q")
            if values:
                return values[0]
        elif item.startswith(("http://", "https://")) and "google.com" not in item:
            return item
    return None


def extract_phone(biz: List[Any]) -> Optional[str]:
    """Return the phone number from ``biz[178]``."""
    phone_data = safe_get(biz, 178)
    if not isinstance(phone_data, list) or not phone_data:
        return None
    inner = phone_data[0]
    phone = inner[0] if isinstance(inner, list) and inner else inner
    if isinstance(phone, str) and phone.strip():
        return phone.strip()
    return None


def _number(value: Any, kind: type) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return kind(value)


def _text(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _address(raw: Any, name: str) -> Optional[str]:
    """``[18]`` usually starts with the place name ("Name, 1 Main St, ..."); drop that prefix."""
    text = _text(raw)
    if text and name and text.startswith(name + ", "):
        text = text[len(name) + 2:].strip() or None
    return text


def extract_single_business(biz: Any) -> Optional[Dict[str, Any]]:
    """Parse one result array into a flat dict, or return ``None`` if it is not a place."""
    if not isinstance(biz, list) or len(biz) < 12:
        return None
    name = safe_get(biz, 11)
    if not isinstance(name, str) or len(name) < 2 or name.endswith("="):
        return None
    if all(c in _BASE64_CHARS for c in name):
        return None  # an encoded token, not a name

    raw_categories = safe_get(biz, 13)
    categories = [c for c in raw_categories if isinstance(c, str)] if isinstance(raw_categories, list) else []

    return {
        "name": name,
        "address": _address(safe_get(biz, 18), name),
        "phone": extract_phone(biz),
        "website": extract_website(biz),
        "rating": _number(safe_get(biz, 4, 7), float),
        "reviews_count": _number(safe_get(biz, 4, 8), int),
        "category": categories[0] if categories else None,
        "categories": categories,
        "latitude": _number(safe_get(biz, 9, 2), float),
        "longitude": _number(safe_get(biz, 9, 3), float),
        "place_id": _text(safe_get(biz, 78)),
        "hex_id": _text(safe_get(biz, 10)),
        "ftid": _text(safe_get(biz, 89)),
    }


def _find_entries(obj: Any, depth: int = 0, max_depth: int = 10) -> List[List[Any]]:
    """Find arrays whose ``[14]`` looks like a place (a name string at ``[14][11]``)."""
    found: List[List[Any]] = []
    if depth > max_depth or not isinstance(obj, list):
        return found
    for item in obj:
        if isinstance(item, list) and len(item) > 14:
            name = safe_get(item, 14, 11)
            if isinstance(name, str) and len(name) > 2:
                found.append(item[14])
    for item in obj:
        if isinstance(item, list):
            found.extend(_find_entries(item, depth + 1, max_depth))
    return found


def extract_businesses(data: Any) -> List[Dict[str, Any]]:
    """Return the places in a parsed search answer, in answer order, without duplicates.

    Each dict holds the public fields (except ``hours`` and ``google_maps_url``)
    plus ``hex_id`` and ``ftid``, the two identifiers the place request needs.
    """
    if not isinstance(data, list):
        return []
    candidates = _find_entries(data)
    organic = safe_get(data, 64)
    if isinstance(organic, list):
        candidates.extend(safe_get(entry, 1) for entry in organic if isinstance(entry, list))

    places: List[Dict[str, Any]] = []
    seen = set()
    for raw in candidates:
        place = extract_single_business(raw)
        if not place:
            continue
        key = place["place_id"] or place["hex_id"] or place["name"]
        if key in seen:
            continue
        seen.add(key)
        places.append(place)
    return places
