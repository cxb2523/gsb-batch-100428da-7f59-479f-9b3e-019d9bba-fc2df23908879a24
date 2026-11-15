"""
Row builder — converts a raw Meta API response dict into a flat list
matching the configured sheet column order.
"""


def extract_action_value(items: list, action_type: str, value_field: str = "value") -> float:
    """
    Extract a single action value from a Meta API actions list by action_type.

    Args:
        items: List of action dicts from the Meta API response.
        action_type: The action_type string to match (e.g. 'purchase').
        value_field: The field to read from the matched item (default 'value').

    Returns:
        Float value, or 0.0 if not found.
    """
    raise NotImplementedError("Proprietary implementation")


def extract_action_sum(items: list, action_types: list) -> float:
    """
    Sum values across multiple action types from a Meta API actions list.

    Args:
        items: List of action dicts from the Meta API response.
        action_types: List of action_type strings to include in the sum.

    Returns:
        Float total, or 0.0 if no matches found.
    """
    raise NotImplementedError("Proprietary implementation")


def extract_roas(items: list) -> float:
    """
    Extract the purchase ROAS value from a Meta API purchase_roas list.

    Args:
        items: List of ROAS dicts from the Meta API response.

    Returns:
        Float ROAS value, or 0.0 if not found.
    """
    raise NotImplementedError("Proprietary implementation")


def build_row(raw: dict, cfg: dict) -> list:
    """
    Convert a raw Meta API row dict into an ordered list for writing to Google Sheets.

    Uses the field_map from cfg to determine how each sheet column maps to an API field.
    Handles simple fields (str/int/float), action fields, action sums, ROAS, and video
    action aggregations.

    Args:
        raw: Raw API response dict for one ad for one day.
        cfg: Config dict containing field_map and sheet_columns.

    Returns:
        Ordered list of values matching sheet_columns.

    Note:
        Field mapping configuration is proprietary and not included in this public version.
    """
    raise NotImplementedError("Proprietary implementation")


def make_key(raw: dict) -> str:
    """
    Build a deduplication key for a raw API row.

    Args:
        raw: Raw API response dict.

    Returns:
        String key in format 'YYYY-MM-DD|ad_id'.
    """
    return f"{raw.get('date_start', '')}|{raw.get('ad_id', '')}"
