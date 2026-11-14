from __future__ import annotations

"""Steps for the JSON Event Log Processor."""

from datetime import datetime, timezone

# Shared constants used across multiple steps.
# Severity mapping derived from ALLOWED_EVENT_TYPES to avoid duplication [Q9].
ALLOWED_EVENT_TYPES = ("info", "warning", "error")
SEVERITY_MAP = {"info": "INFO", "warning": "WARNING", "error": "ERROR"}

if set(ALLOWED_EVENT_TYPES) != set(SEVERITY_MAP):
    raise ValueError(
        "ALLOWED_EVENT_TYPES and SEVERITY_MAP must have the same keys",
    )


def parse_timestamp(value: object) -> datetime:
    """Parse one ISO 8601 timestamp string and normalize it to UTC.

    Single source of truth for the accepted timestamp values: ``ValidateEvents``
    rejects whatever this refuses, so the two steps cannot drift apart and a
    value accepted by validation can never fail later during normalization.

    Raises ``TypeError`` for a non-string and ``ValueError`` for an unparseable
    string or a value outside the supported UTC range.
    """
    if not isinstance(value, str):
        raise TypeError(f"timestamp must be a str, got {type(value).__name__}")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    try:
        return parsed.astimezone(timezone.utc)
    except OverflowError as exc:
        raise ValueError("timestamp outside supported UTC range") from exc
