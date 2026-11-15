"""
JWT-based license key system.

License keys are issued by an external license management system and decoded
here to enforce feature limits (primarily the number of ad accounts allowed
per organization).
"""

import os
import logging
from datetime import datetime
import jwt
from dotenv import load_dotenv

load_dotenv()


def decode_license(key: str) -> dict:
    """
    Decode a license JWT and return its contents.

    Validates the product slug and checks the expiry date.
    Returns safe defaults on any error so the app continues functioning
    with a reduced account limit.

    Args:
        key: JWT license key string.

    Returns:
        Dict with keys:
            allowed_accounts (int): Maximum ad accounts permitted.
            client_id (str): Client/company name from the license.
            expires (str): Expiry date string YYYY-MM-DD.
            expired (bool): True if the license has passed its expiry date.
            valid (bool): True if the license is valid and not expired.

    Note:
        License validation logic, product slug matching, and JWT secret
        handling are proprietary and not included in this public version.
    """
    raise NotImplementedError("Proprietary implementation")


def get_allowed_accounts() -> int:
    """
    Return the maximum number of ad accounts allowed for the current tenant.

    Reads the license key from settings and decodes it. Returns 1 if no
    license is set, the license is invalid, or the license has expired.

    Returns:
        Integer account limit (minimum 1).
    """
    raise NotImplementedError("Proprietary implementation")
