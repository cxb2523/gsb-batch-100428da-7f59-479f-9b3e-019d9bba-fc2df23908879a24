"""
Sync orchestrator — coordinates data syncs for Meta ad accounts.

This module implements two layers of resilience:

1. ``run_sync_for_account`` runs the full fetch -> deduplicate -> write
   pipeline for one account and *never raises*: every failure is captured
   into a result dict whose ``message`` carries the exception text.
2. ``run_all_accounts`` loops over every account of the current tenant,
   isolates per-account failures so one expired token, unauthorized sheet,
   or timeout cannot abort the batch, classifies the overall outcome
   (all success / partial / all failed), and sends a Telegram summary that
   lists successes and failures separately.

All external side effects (Meta API, Google Sheets, the cache, the clock,
and HTTP) go through an injectable :class:`Dependencies` bundle. Production
code wires the real modules via :func:`default_dependencies`; tests inject
in-memory fakes, so no real Meta token or Google credential is ever needed.
"""

import logging
import os
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Result dict keys returned for every account, in every outcome.
RESULT_KEYS = (
    "account_id",
    "account_name",
    "timestamp",
    "status",
    "rows_added",
    "message",
)

# The only two status values allowed on a per-account result.
STATUS_SUCCESS = "success"
STATUS_ERROR = "error"

# Batch-level outcomes.
OUTCOME_ALL_SUCCESS = "all_success"
OUTCOME_PARTIAL_SUCCESS = "partial_success"
OUTCOME_ALL_FAILED = "all_failed"
OUTCOME_NO_ACCOUNTS = "no_accounts"

DEFAULT_API_VERSION = "v25.0"
DEFAULT_RAW_WORKSHEET = "RAW_DATA"
DEFAULT_ANALYTICS_WORKSHEET = "ANALYTICS"
# Lookback window for the "maximum" preset when nothing else is configured.
DEFAULT_MAX_LOOKBACK_DAYS = 365


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------


class SheetGateway:
    """Interface for the Google Sheets operations the sync engine needs.

    The real implementation delegates to :mod:`core.sheets_writer` (and
    gspread). Tests substitute an in-memory fake with the same methods.
    """

    def get_worksheets(self, cfg: dict):
        raise NotImplementedError

    def load_sheet_state(self, ws, cfg: dict):
        raise NotImplementedError

    def append_rows(self, ws, rows: list) -> None:
        raise NotImplementedError

    def build_analytics(self, raw_ws, ana_ws, cfg: dict) -> None:
        raise NotImplementedError


class RealSheetGateway(SheetGateway):
    """Production gateway.

    gspread / sheets_writer are imported lazily inside each method so that
    simply importing :mod:`core.sync_engine` does not require the Google
    client libraries to be installed.
    """

    def get_worksheets(self, cfg: dict):
        from core.sheets_writer import get_worksheets

        return get_worksheets(cfg)

    def load_sheet_state(self, ws, cfg: dict):
        from core.sheets_writer import load_sheet_state

        return load_sheet_state(ws, cfg)

    def append_rows(self, ws, rows: list) -> None:
        # gspread worksheet append; RAW prevents coercion of ids/date strings.
        ws.append_rows(rows, value_input_option="RAW")

    def build_analytics(self, raw_ws, ana_ws, cfg: dict) -> None:
        from core.sheets_writer import build_analytics

        build_analytics(raw_ws, ana_ws, cfg)


def _default_invalidate_cache(account_id: str) -> None:
    # Lazy import: data_reader pulls in gspread at module level.
    from core.data_reader import invalidate_cache

    return invalidate_cache(account_id)


def _requests_post(url: str, **kwargs):
    import requests

    kwargs.setdefault("timeout", 15)
    return requests.post(url, **kwargs)


@dataclass(frozen=True)
class Dependencies:
    """Replaceable collaborators for the sync engine.

    Every field is a callable (or :class:`SheetGateway`) so that tests can
    inject fakes while :func:`default_dependencies` wires the real modules.
    """

    load_settings: Callable[[], dict]
    fetch_meta_data: Callable[[dict, date, date], list]
    build_row: Callable[[dict, dict], list]
    make_key: Callable[[dict], str]
    sheets: SheetGateway
    invalidate_cache: Callable[[str], None]
    now: Callable[[], datetime]
    http_post: Callable


def default_dependencies() -> Dependencies:
    """Wire the real production collaborators.

    Only modules needed for the actual code path are imported, and the
    gspread-backed modules are imported lazily behind their wrappers.
    """
    from core.config_manager import load_settings
    from core.meta_fetcher import fetch_meta_data
    from core.row_builder import build_row, make_key

    return Dependencies(
        load_settings=load_settings,
        fetch_meta_data=fetch_meta_data,
        build_row=build_row,
        make_key=make_key,
        sheets=RealSheetGateway(),
        invalidate_cache=_default_invalidate_cache,
        now=datetime.now,
        http_post=_requests_post,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _account_identity(account: dict) -> tuple[str, str]:
    """Extract a stable (id, display name) pair from an account dict."""
    account_id = str(
        account.get("id")
        or account.get("account_id")
        or account.get("ad_account_id")
        or ""
    )
    account_name = str(
        account.get("name")
        or account.get("account_name")
        or account_id
        or "unknown account"
    )
    return account_id, account_name


def _error_result(account: dict, now_fn: Callable[[], datetime], exc: Exception) -> dict:
    """Build a normalized error result for an account that failed."""
    account_id, account_name = _account_identity(account)
    reason = str(exc).strip() or exc.__class__.__name__
    return {
        "account_id": account_id,
        "account_name": account_name,
        "timestamp": now_fn().isoformat(timespec="seconds"),
        "status": STATUS_ERROR,
        "rows_added": 0,
        "message": f"{exc.__class__.__name__}: {reason}",
    }


def _parse_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def resolve_date_range(settings: dict, today: Optional[date] = None) -> tuple[date, date]:
    """Resolve the configured sync window to an inclusive (start, end).

    Supported ``settings['date_range']`` shapes:

    * ``{'start': 'YYYY-MM-DD', 'end': 'YYYY-MM-DD'}`` — explicit window;
    * ``'yesterday'`` / ``{'preset': 'yesterday'}`` — the previous day;
    * ``'maximum'`` / ``{'preset': 'maximum'}`` — a lookback window ending
      yesterday (``settings['lookback_days']``, default 365);
    * an integer N (or ``{'preset': N}``) — the last N days ending yesterday.

    Anything unparseable falls back to yesterday, matching the product's
    default daily sync window.
    """
    if today is None:
        today = date.today()

    date_range = settings.get("date_range")
    explicit_start = explicit_end = None
    preset = None

    if isinstance(date_range, dict):
        explicit_start = date_range.get("start") or date_range.get("start_date")
        explicit_end = date_range.get("end") or date_range.get("end_date")
        preset = date_range.get("preset") or date_range.get("type")
    elif isinstance(date_range, str):
        preset = date_range
    elif isinstance(date_range, int):
        preset = date_range

    if explicit_start and explicit_end:
        return _parse_date(explicit_start), _parse_date(explicit_end)

    if preset == "maximum":
        days = int(settings.get("lookback_days", DEFAULT_MAX_LOOKBACK_DAYS))
    elif isinstance(preset, int) and not isinstance(preset, bool):
        days = preset
    else:
        # "yesterday", unknown presets, and missing config all default to 1 day.
        days = 1

    days = max(days, 1)
    end = today - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    return start, end


def _build_account_config(account: dict, settings: dict) -> dict:
    """Merge tenant-level settings with one account's credentials."""
    google = settings.get("google", {}) or {}
    api_version = (
        account.get("api_version")
        or settings.get("api_version")
        or DEFAULT_API_VERSION
    )
    return {
        # Meta
        "meta_token": account.get("meta_token"),
        "access_token": account.get("meta_token"),
        "ad_account_id": account.get("ad_account_id"),
        "api_version": api_version,
        "fields": settings.get("fields", []),
        # Google Sheets
        "api_key_file": google.get("api_key_file"),
        "sheet_id": account.get("sheet_id"),
        "raw_worksheet": account.get("raw_worksheet")
        or google.get("raw_worksheet")
        or DEFAULT_RAW_WORKSHEET,
        "analytics_worksheet": account.get("analytics_worksheet")
        or google.get("analytics_worksheet")
        or DEFAULT_ANALYTICS_WORKSHEET,
        # Column mapping
        "sheet_columns": settings.get("sheet_columns", []),
        "field_map": settings.get("field_map", {}),
    }


# ---------------------------------------------------------------------------
# Per-account sync
# ---------------------------------------------------------------------------


def run_sync_for_account(
    account: dict,
    settings: Optional[dict] = None,
    deps: Optional[Dependencies] = None,
) -> dict:
    """
    Run a full sync for a single Meta ad account.

    Resolves the date range, fetches data from Meta, deduplicates against
    existing rows in the Google Sheet, appends new rows, rebuilds the
    ANALYTICS tab, and invalidates the dashboard cache.

    This function never raises: any exception is captured and returned as a
    result with ``status='error'``, ``rows_added=0`` and the exception text
    in ``message`` (the error is also logged, but never only logged).

    Args:
        account: Account dict from settings (token, ad_account_id, sheet_id, ...).
        settings: Tenant settings dict. Loaded via ``deps.load_settings`` if None.
        deps: Injectable collaborators. Defaults to the production wiring.

    Returns:
        Result dict with keys: account_id, account_name, timestamp,
        status ('success' or 'error'), rows_added, message.
    """
    deps = deps or default_dependencies()
    if settings is None:
        settings = deps.load_settings()

    account_id, account_name = _account_identity(account)
    timestamp = deps.now().isoformat(timespec="seconds")

    try:
        start, end = resolve_date_range(settings, deps.now().date())
        cfg = _build_account_config(account, settings)

        raw_rows = deps.fetch_meta_data(cfg, start, end)

        raw_ws, ana_ws = deps.sheets.get_worksheets(cfg)
        _last_date, existing_keys, _is_fresh = deps.sheets.load_sheet_state(
            raw_ws, cfg
        )

        seen_keys = set(existing_keys)
        new_rows = []
        for raw in raw_rows:
            key = deps.make_key(raw)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            new_rows.append(deps.build_row(raw, cfg))

        if new_rows:
            deps.sheets.append_rows(raw_ws, new_rows)

        # ANALYTICS always reflects the current RAW_DATA, even with 0 new rows.
        deps.sheets.build_analytics(raw_ws, ana_ws, cfg)
        deps.invalidate_cache(account_id)

        logger.info(
            "Account %s (%s) synced: %d fetched, %d new row(s)",
            account_name,
            account_id,
            len(raw_rows),
            len(new_rows),
        )
        return {
            "account_id": account_id,
            "account_name": account_name,
            "timestamp": timestamp,
            "status": STATUS_SUCCESS,
            "rows_added": len(new_rows),
            "message": (
                f"Synced {len(new_rows)} new row(s) "
                f"from {len(raw_rows)} fetched row(s)."
            ),
        }
    except Exception as exc:  # noqa: BLE001 - batch isolation requires a broad catch
        logger.exception(
            "Sync failed for account %s (%s)", account_name, account_id
        )
        result = _error_result(account, deps.now, exc)
        # Keep the timestamp captured before the attempt for stable reporting.
        result["timestamp"] = timestamp
        return result


# ---------------------------------------------------------------------------
# Batch orchestration
# ---------------------------------------------------------------------------


def classify_batch_outcome(results: list[dict]) -> str:
    """Classify a batch of per-account results.

    Returns:
        One of ``all_success``, ``partial_success``, ``all_failed``, or
        ``no_accounts`` (when the batch is empty). The first three are the
        required overall outcomes for any non-empty account list.
    """
    if not results:
        return OUTCOME_NO_ACCOUNTS
    failures = sum(1 for r in results if r.get("status") == STATUS_ERROR)
    if failures == 0:
        return OUTCOME_ALL_SUCCESS
    if failures == len(results):
        return OUTCOME_ALL_FAILED
    return OUTCOME_PARTIAL_SUCCESS


def build_batch_summary(results: list[dict]) -> dict:
    """Aggregate per-account results into a checkable batch summary."""
    successes = [r for r in results if r.get("status") == STATUS_SUCCESS]
    failures = [r for r in results if r.get("status") == STATUS_ERROR]
    return {
        "outcome": classify_batch_outcome(results),
        "total_accounts": len(results),
        "succeeded": len(successes),
        "failed": len(failures),
        "total_rows_added": sum(int(r.get("rows_added", 0)) for r in successes),
        "results": results,
    }


def run_all_accounts(
    settings: Optional[dict] = None,
    deps: Optional[Dependencies] = None,
) -> list[dict]:
    """
    Run sync for every account in the current tenant's settings.

    Each account is processed independently: an exception for one account is
    recorded on that account's result dict and the loop continues with the
    remaining accounts, so a single expired token, unauthorized sheet, or
    request timeout can never abort the whole batch.

    After the loop a Telegram summary is sent. Telegram is best-effort: if it
    is not configured or the send fails, the sync results are unaffected.

    Args:
        settings: Tenant settings dict. Loaded via ``deps.load_settings`` if None.
        deps: Injectable collaborators. Defaults to the production wiring.

    Returns:
        List of result dicts (one per account, input order preserved). Each
        result has keys: account_id, account_name, timestamp, status
        ('success' or 'error'), rows_added, message.
    """
    deps = deps or default_dependencies()
    if settings is None:
        settings = deps.load_settings()

    accounts = settings.get("accounts", []) or []
    results: list[dict] = []

    for account in accounts:
        try:
            result = run_sync_for_account(account, settings=settings, deps=deps)
        except Exception as exc:  # noqa: BLE001 - last-resort batch guard
            # run_sync_for_account should already have caught this; this guard
            # guarantees the batch loop can never be interrupted.
            logger.exception("Unexpected sync failure for account %r", account)
            result = _error_result(account, deps.now, exc)
        results.append(result)

    summary = build_batch_summary(results)
    logger.info(
        "Batch sync finished: outcome=%s total=%d success=%d failed=%d rows=%d",
        summary["outcome"],
        summary["total_accounts"],
        summary["succeeded"],
        summary["failed"],
        summary["total_rows_added"],
    )

    try:
        send_telegram_summary(results, settings, deps=deps)
    except Exception:  # noqa: BLE001 - notification must not alter sync outcome
        logger.exception("Telegram summary failed; sync results are unaffected")

    return results


# ---------------------------------------------------------------------------
# Telegram notifications
# ---------------------------------------------------------------------------


def _telegram_config(settings: Optional[dict]) -> tuple[str, str]:
    """Extract (bot_token, chat_id), tolerating several config shapes."""
    telegram = (settings or {}).get("telegram", {}) or {}
    token = (
        telegram.get("bot_token")
        or telegram.get("token")
        or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    )
    chat_id = (
        telegram.get("chat_id")
        or telegram.get("chat")
        or os.environ.get("TELEGRAM_CHAT_ID", "")
    )
    return str(token or "").strip(), str(chat_id or "").strip()


_OUTCOME_LABELS = {
    OUTCOME_ALL_SUCCESS: "ALL SUCCESS ✅",
    OUTCOME_PARTIAL_SUCCESS: "PARTIAL SUCCESS ⚠️",
    OUTCOME_ALL_FAILED: "ALL FAILED ❌",
    OUTCOME_NO_ACCOUNTS: "NO ACCOUNTS CONFIGURED ℹ️",
}


def build_telegram_message(results: list[dict]) -> str:
    """Build the human-readable batch summary text.

    Successful and failed accounts are listed in separate sections, and every
    failed account includes the failure reason from its ``message`` field.
    """
    summary = build_batch_summary(results)
    outcome_label = _OUTCOME_LABELS.get(summary["outcome"], summary["outcome"])

    lines = [
        f"Meta ad-account sync report — {outcome_label}",
        (
            f"Accounts: {summary['total_accounts']} total, "
            f"{summary['succeeded']} succeeded, {summary['failed']} failed | "
            f"rows added: {summary['total_rows_added']}"
        ),
    ]

    successes = [r for r in results if r.get("status") == STATUS_SUCCESS]
    failures = [r for r in results if r.get("status") == STATUS_ERROR]

    if successes:
        lines.append("")
        lines.append("SUCCESS:")
        for r in successes:
            lines.append(
                f"- {r['account_name']} ({r['account_id']}): "
                f"{r['rows_added']} row(s) added"
            )

    if failures:
        lines.append("")
        lines.append("FAILED:")
        for r in failures:
            lines.append(
                f"- {r['account_name']} ({r['account_id']}): {r['message']}"
            )

    return "\n".join(lines)


def send_telegram_alert(
    message: str,
    settings: Optional[dict] = None,
    deps: Optional[Dependencies] = None,
) -> bool:
    """
    Send a message to the configured Telegram chat.

    Silently skips (returns False) when no bot token or chat ID is configured,
    so missing notification settings never affect the sync result. Network or
    API errors are logged and swallowed for the same reason.

    Args:
        message: The text to send.
        settings: Settings dict. Loaded via ``deps.load_settings`` if None.
        deps: Injectable collaborators (uses ``deps.http_post``).

    Returns:
        True if the Telegram API accepted the message, False otherwise.
    """
    deps = deps or default_dependencies()
    if settings is None:
        settings = deps.load_settings()

    token, chat_id = _telegram_config(settings)
    if not token or not chat_id:
        logger.info("Telegram bot token or chat ID not configured; skipping send")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        response = deps.http_post(
            url,
            json={"chat_id": chat_id, "text": message},
        )
        status_code = getattr(response, "status_code", 200)
        if status_code >= 400:
            logger.warning("Telegram API returned HTTP %s", status_code)
            return False
        return True
    except Exception:  # noqa: BLE001 - notifications are best-effort
        logger.exception("Telegram send failed")
        return False


def send_telegram_summary(
    results: list[dict],
    settings: dict,
    deps: Optional[Dependencies] = None,
) -> bool:
    """
    Build and send a Telegram summary message for a batch of sync results.

    The message reports the overall outcome and lists successful and failed
    accounts separately, with the failure reason attached to each failure.

    Args:
        results: List of result dicts from :func:`run_all_accounts`.
        settings: Settings dict containing the telegram config.
        deps: Injectable collaborators.

    Returns:
        True if sent, False if skipped or failed.
    """
    deps = deps or default_dependencies()
    message = build_telegram_message(results)
    return send_telegram_alert(message, settings=settings, deps=deps)
