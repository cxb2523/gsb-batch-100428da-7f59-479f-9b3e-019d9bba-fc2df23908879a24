"""
Sync orchestrator — coordinates a full data sync for one ad account.
"""

import json
import logging
from datetime import datetime, date, timedelta
from pathlib import Path

from core.config_manager import load_settings
from core.meta_fetcher import fetch_meta_data
from core.row_builder import build_row, make_key
from core.sheets_writer import get_worksheets, load_sheet_state, build_analytics


def run_sync_for_account(account: dict) -> dict:
    """
    Run a full sync for a single Meta ad account.

    Loads settings, resolves the date range, fetches data from Meta, deduplicates
    against existing rows in the Google Sheet, appends new rows, and rebuilds
    the ANALYTICS tab.

    Args:
        account: Account dict from settings (token, ad_account_id, sheet_id, etc.)

    Returns:
        Result dict with keys: account_id, account_name, timestamp,
        status ('success' or 'error'), rows_added, message.

    Note:
        Date range resolution and sync orchestration logic is proprietary
        and not included in this public version.
    """
    raise NotImplementedError("Proprietary implementation")


def run_all_accounts() -> list[dict]:
    """
    Run sync for every account in the current tenant's settings.

    Sends a Telegram summary after all accounts have synced.

    Returns:
        List of result dicts, one per account.
    """
    raise NotImplementedError("Proprietary implementation")


def send_telegram_alert(message: str, settings: dict | None = None) -> None:
    """
    Send a message to the configured Telegram chat.

    Args:
        message: The text to send.
        settings: Settings dict. Loads from config_manager if None.

    Note:
        Silently returns if no bot token or chat ID is configured.
    """
    raise NotImplementedError("Proprietary implementation")


def send_telegram_summary(results: list[dict], settings: dict) -> None:
    """
    Build and send a Telegram summary message for a batch of sync results.

    Args:
        results: List of result dicts from run_all_accounts.
        settings: Settings dict containing telegram config.
    """
    raise NotImplementedError("Proprietary implementation")
