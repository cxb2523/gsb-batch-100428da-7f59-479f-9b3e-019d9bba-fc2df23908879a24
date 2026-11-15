"""
Google Sheets connector — writes RAW_DATA and ANALYTICS worksheets.
Handles worksheet creation, deduplication state loading, and header formatting.
"""

import logging
from datetime import datetime, date
import gspread
from oauth2client.service_account import ServiceAccountCredentials


def get_worksheets(cfg: dict) -> tuple:
    """
    Authenticate with Google Sheets and return (raw_ws, analytics_ws) worksheet objects.

    Creates the ANALYTICS worksheet if it does not exist.

    Args:
        cfg: Config dict with google.api_key_file, google.sheet_id,
             google.raw_worksheet, google.analytics_worksheet.

    Returns:
        Tuple of (raw_worksheet, analytics_worksheet) gspread Worksheet objects.

    Raises:
        gspread.exceptions.SpreadsheetNotFound: If the sheet ID is invalid or
            the service account does not have access.
    """
    raise NotImplementedError("Proprietary implementation")


def load_sheet_state(ws, cfg: dict) -> tuple:
    """
    Read the current state of the RAW_DATA worksheet to support deduplication.

    Returns the most recent date found, the set of existing date+ad_id keys,
    and whether the sheet is freshly initialized.

    Args:
        ws: gspread Worksheet object for RAW_DATA.
        cfg: Config dict containing expected sheet_columns for header validation.

    Returns:
        Tuple of (last_date: date | None, existing_keys: set[str], is_fresh: bool).
    """
    raise NotImplementedError("Proprietary implementation")


def build_analytics(raw_ws, ana_ws, cfg: dict) -> None:
    """
    Rebuild the ANALYTICS worksheet from RAW_DATA.

    Reads all rows from RAW_DATA, applies numeric type cleaning (float/int casting),
    clears the ANALYTICS sheet, writes the cleaned data, and applies formatting
    (bold header, frozen row, column filters).

    Args:
        raw_ws: gspread Worksheet for RAW_DATA.
        ana_ws: gspread Worksheet for ANALYTICS.
        cfg: Config dict (used for column type hints).

    Note:
        Type classification and formatting logic is proprietary and not included
        in this public version.
    """
    raise NotImplementedError("Proprietary implementation")
