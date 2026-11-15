"""
Meta Marketing API client — fetches paginated ad insights data.
"""

import json
import time
import requests
import logging
from datetime import date


def fetch_meta_data(cfg: dict, start: date, end: date) -> list[dict]:
    """
    Fetch ad-level insights from the Meta Graph API for a given date range.

    Handles pagination automatically. Each page is fetched with a short delay
    to stay within Meta API rate limits.

    Args:
        cfg: Configuration dict with meta credentials and field list.
        start: Start date (inclusive).
        end: End date (inclusive).

    Returns:
        List of raw API row dicts, one per ad per day.

    Raises:
        RuntimeError: On network errors or Meta API error responses.

    Note:
        Pagination logic and field selection are proprietary and not included
        in this public version.
    """
    raise NotImplementedError("Proprietary implementation")


def validate_meta_token(access_token: str, ad_account_id: str, api_version: str = "v25.0") -> dict:
    """
    Test whether a Meta access token is valid for a given ad account.

    Args:
        access_token: Meta long-lived access token.
        ad_account_id: Ad account ID in format act_XXXXXXXXX.
        api_version: Meta API version string (e.g. 'v25.0').

    Returns:
        Dict with keys:
            valid (bool): True if the token is accepted.
            message (str): Account name on success, error message on failure.
    """
    url = f"https://graph.facebook.com/{api_version}/{ad_account_id}"
    try:
        res = requests.get(url, params={"access_token": access_token, "fields": "id,name"}, timeout=10)
        data = res.json()
        if "error" in data:
            return {"valid": False, "message": data["error"]["message"]}
        return {"valid": True, "message": f"Connected: {data.get('name', ad_account_id)}"}
    except Exception as e:
        return {"valid": False, "message": str(e)}
