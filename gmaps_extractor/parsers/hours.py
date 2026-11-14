"""Opening hours in a Google Maps place answer (``/maps/preview/place``).

The place data sits at ``data[6]``. Opening hours are read from:

- ``[203][0]``: current format, one entry per day::

      ["Friday", 5, [2026, 1, 23], [["9 AM-5 PM", [[9], [17]]]], 0, 1]

  ``[1]`` is the day number (Sunday is 7, sometimes 0) and ``[3]`` holds one or
  more time slots (split hours give several).
- ``[34]``: older format, a list of 7 days starting on Sunday.

Google sometimes shortens the place array (seen for service-area businesses
without an address): fields past a pivot index then move into one object at
the last position, keyed by field number (index + 1) as a string, so the hours
are at ``tail["204"][0]``.

Only opening hours are read here; nothing else in the answer is parsed.
"""

from typing import Any, Dict, Optional

from .response import safe_get

DAYS = ("sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday")


def _field_object(place: Any) -> Optional[Dict[str, Any]]:
    """Return the trailing field object of a shortened place array, else ``None``."""
    if isinstance(place, list) and place:
        last = place[-1]
        if isinstance(last, dict) and last and all(isinstance(k, str) and k.isdigit() for k in last):
            return last
    return None


def _place_field(place: Any, index: int, *rest: Any) -> Any:
    """``safe_get(place, index, *rest)``, also looking in the trailing field object."""
    if isinstance(place, list) and index >= len(place) - 1:
        tail = _field_object(place)
        if tail is not None:
            node = tail.get(str(index + 1))
            return safe_get(node, *rest) if rest else node
    return safe_get(place, index, *rest)


def _clean(text: str) -> str:
    # Google uses a narrow no-break space before AM/PM and an en dash in ranges.
    return text.replace(" ", " ").replace(" ", " ").replace("–", "-").strip()


def parse_hours_current(days: Any) -> Optional[Dict[str, str]]:
    """Parse the current format at ``[203][0]`` into ``{"monday": "9 AM-5 PM", ...}``."""
    if not isinstance(days, list):
        return None
    hours: Dict[str, str] = {}
    for entry in days:
        if not isinstance(entry, list) or len(entry) < 4 or not isinstance(entry[0], str):
            continue
        day_number = entry[1]
        if isinstance(day_number, int) and not isinstance(day_number, bool) and day_number >= 0:
            key = DAYS[day_number % 7]
        else:
            key = entry[0].strip().lower()
        slots = []
        if isinstance(entry[3], list):
            for slot in entry[3]:
                if isinstance(slot, list) and slot and isinstance(slot[0], str):
                    slots.append(_clean(slot[0]))
                elif isinstance(slot, str):
                    slots.append(_clean(slot))
        hours[key] = ", ".join(s for s in slots if s) or "Unknown"
    return hours or None


def parse_hours_legacy(data: Any) -> Optional[Dict[str, str]]:
    """Parse the older format at ``[34]``."""
    if not isinstance(data, list) or not data:
        return None
    schedule = data[1] if len(data) > 1 and isinstance(data[1], list) else data
    hours: Dict[str, str] = {}
    for i, day in enumerate(schedule[:7]):
        if isinstance(day, list) and day:
            first = day[0]
            if first == "Closed" or first == []:
                hours[DAYS[i]] = "Closed"
            elif isinstance(first, str):
                hours[DAYS[i]] = _clean(first)
            else:
                hours[DAYS[i]] = "Unknown"
        else:
            hours[DAYS[i]] = "Unknown"
    return hours or None


def extract_hours(data: Any) -> Optional[Dict[str, str]]:
    """Return the opening hours in a parsed place answer, or ``None``.

    Keys are English lower-case day names (``sunday`` ... ``saturday``);
    values are Google's text, e.g. ``"7 AM-6 PM"``, ``"Closed"``,
    ``"Open 24 hours"`` or ``"11 AM-2 PM, 5-10 PM"`` for split hours.
    """
    place = safe_get(data, 6)
    if not isinstance(place, list):
        return None
    current = _place_field(place, 203, 0)
    if isinstance(current, list) and current:
        return parse_hours_current(current)
    return parse_hours_legacy(safe_get(place, 34))
