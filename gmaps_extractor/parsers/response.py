"""Turn a raw Google Maps answer into Python objects."""

import json
from typing import Any


def parse_google_response(text: str) -> Any:
    """Parse the body of a Google Maps answer.

    Strips the ``)]}'`` anti-JSON-hijacking prefix and loads the first complete
    JSON array. Returns ``[]`` when the body holds no JSON.
    """
    if not text:
        return []
    text = text.strip()
    if text.startswith(")]}'"):
        text = text[4:].strip()
    if not text:
        return []
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Some answers carry trailing data after the first array: take the first
    # balanced array, ignoring brackets inside JSON strings.
    start = text.find("[")
    if start < 0:
        return []
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        char = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return []
    return []


def safe_get(obj: Any, *indices: Any, default: Any = None) -> Any:
    """Walk nested lists/dicts by index or key; return ``default`` on any miss."""
    current = obj
    for idx in indices:
        if isinstance(current, list) and isinstance(idx, int) and not isinstance(idx, bool):
            if -len(current) <= idx < len(current):
                current = current[idx]
            else:
                return default
        elif isinstance(current, dict):
            if idx not in current:
                return default
            current = current[idx]
        else:
            return default
        if current is None:
            return default
    return current
