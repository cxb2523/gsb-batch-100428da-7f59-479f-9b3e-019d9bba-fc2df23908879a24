"""
Tests for the resilient batch sync engine (core.sync_engine).

No real Meta API calls and no Google Sheets credentials are used: every
external collaborator is an in-memory fake injected through
``sync_engine.Dependencies``. The multi-account configuration is a local
JSON fixture (tests/fixtures/multi_account_settings.json).
"""

import json
import sys
import unittest
from datetime import date, datetime
from pathlib import Path

# Make src/ importable even under `python -I` (isolated mode drops cwd).
SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from core import sync_engine as se  # noqa: E402
from core.row_builder import make_key  # noqa: E402 (pure module, no gspread)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "multi_account_settings.json"


class FixedClock:
    """Deterministic clock so timestamps in results are stable."""

    def __init__(self, moment=None):
        self.moment = moment or datetime(2026, 11, 15, 9, 30, 0)

    def now(self):
        return self.moment


class FakeWorksheet:
    def __init__(self, sheet_id):
        self.sheet_id = sheet_id


class FakeSheetGateway(se.SheetGateway):
    """In-memory replacement for the gspread-backed RealSheetGateway."""

    def __init__(self, existing_keys=None, fail_on=None):
        self.existing_keys = set(existing_keys or [])
        self.fail_on = dict(fail_on or {})
        self.appended_rows = []
        self.analytics_rebuilds = 0
        self.opened_sheet_ids = []

    def _maybe_fail(self, method):
        if method in self.fail_on:
            raise self.fail_on[method]

    def get_worksheets(self, cfg):
        self._maybe_fail("get_worksheets")
        self.opened_sheet_ids.append(cfg["sheet_id"])
        ws = FakeWorksheet(cfg["sheet_id"])
        return ws, ws

    def load_sheet_state(self, ws, cfg):
        self._maybe_fail("load_sheet_state")
        is_fresh = not self.existing_keys
        return None, set(self.existing_keys), is_fresh

    def append_rows(self, ws, rows):
        self._maybe_fail("append_rows")
        self.appended_rows.extend(rows)

    def build_analytics(self, raw_ws, ana_ws, cfg):
        self._maybe_fail("build_analytics")
        self.analytics_rebuilds += 1


class DispatcherSheetGateway(se.SheetGateway):
    """Routes sheet calls to a per-sheet fake gateway."""

    def __init__(self, gateways):
        self.gateways = gateways

    def _gw(self, cfg):
        return self.gateways[cfg["sheet_id"]]

    def get_worksheets(self, cfg):
        return self._gw(cfg).get_worksheets(cfg)

    def load_sheet_state(self, ws, cfg):
        return self._gw(cfg).load_sheet_state(ws, cfg)

    def append_rows(self, ws, rows):
        return self.gateways[ws.sheet_id].append_rows(ws, rows)

    def build_analytics(self, raw_ws, ana_ws, cfg):
        return self._gw(cfg).build_analytics(raw_ws, ana_ws, cfg)


def make_fake_fetch(rows_by_account=None, failures=None):
    """Fake Meta fetcher: returns canned rows or raises canned exceptions."""
    rows_by_account = rows_by_account or {}
    failures = failures or {}
    calls = []

    def fetch(cfg, start, end):
        calls.append({"ad_account_id": cfg["ad_account_id"], "start": start, "end": end})
        if cfg["ad_account_id"] in failures:
            raise failures[cfg["ad_account_id"]]
        return [dict(row) for row in rows_by_account.get(cfg["ad_account_id"], [])]

    fetch.calls = calls
    return fetch


def fake_build_row(raw, cfg):
    """Map a raw row to sheet column order using the fixture's field_map."""
    return [raw.get(source) for source in cfg["field_map"].values()]


class FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code


class FakeHTTP:
    """Records Telegram POSTs instead of hitting the network."""

    def __init__(self, response=None, raise_exc=None):
        self.calls = []
        self.response = response or FakeResponse(200)
        self.raise_exc = raise_exc

    def __call__(self, url, **kwargs):
        self.calls.append({"url": url, "kwargs": kwargs})
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response


def build_deps(
    fetch=None,
    gateway=None,
    http=None,
    settings=None,
    clock=None,
    cache_calls=None,
):
    """Assemble a fully-in-memory Dependencies bundle."""
    if cache_calls is None:
        cache_calls = []
    return se.Dependencies(
        load_settings=lambda: settings if settings is not None else load_fixture(),
        fetch_meta_data=fetch or make_fake_fetch(),
        build_row=fake_build_row,
        make_key=make_key,
        sheets=gateway or FakeSheetGateway(),
        invalidate_cache=cache_calls.append,
        now=(clock or FixedClock()).now,
        http_post=http or FakeHTTP(),
    )


def load_fixture():
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


def sample_rows(account_prefix, n):
    return [
        {
            "date_start": f"2026-11-0{i + 1}",
            "ad_id": f"{account_prefix}-ad-{i + 1}",
            "ad_name": f"Ad {i + 1}",
            "spend": 10.5 + i,
            "clicks": i + 1,
            "impressions": 100 * (i + 1),
        }
        for i in range(n)
    ]


class SingleAccountSyncTests(unittest.TestCase):
    def setUp(self):
        self.settings = load_fixture()
        self.account = self.settings["accounts"][0]

    def test_success_result_schema_and_counts(self):
        gateway = FakeSheetGateway()
        cache_calls = []
        fetch = make_fake_fetch(
            rows_by_account={"act_111111111": sample_rows("a", 3)}
        )
        deps = build_deps(fetch=fetch, gateway=gateway, cache_calls=cache_calls)

        result = se.run_sync_for_account(self.account, self.settings, deps=deps)

        self.assertEqual(
            set(result.keys()),
            {"account_id", "account_name", "timestamp", "status", "rows_added", "message"},
        )
        self.assertEqual(result["account_id"], "acc-001")
        self.assertEqual(result["account_name"], "North America Store")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["rows_added"], 3)
        self.assertEqual(result["timestamp"], "2026-11-15T09:30:00")
        self.assertEqual(len(gateway.appended_rows), 3)
        self.assertEqual(gateway.analytics_rebuilds, 1)
        self.assertEqual(cache_calls, ["acc-001"])
        self.assertEqual(fetch.calls[0]["start"], date(2026, 11, 1))
        self.assertEqual(fetch.calls[0]["end"], date(2026, 11, 7))

    def test_dedup_skips_rows_already_in_sheet(self):
        rows = sample_rows("a", 3)
        existing = {make_key(rows[0])}  # first row already written previously
        gateway = FakeSheetGateway(existing_keys=existing)
        deps = build_deps(
            fetch=make_fake_fetch(rows_by_account={"act_111111111": rows}),
            gateway=gateway,
        )

        result = se.run_sync_for_account(self.account, self.settings, deps=deps)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["rows_added"], 2)
        self.assertEqual(len(gateway.appended_rows), 2)

    def test_rerun_sync_appends_zero_new_rows(self):
        rows = sample_rows("a", 2)
        gateway = FakeSheetGateway()
        deps = build_deps(
            fetch=make_fake_fetch(rows_by_account={"act_111111111": rows}),
            gateway=gateway,
        )

        first = se.run_sync_for_account(self.account, self.settings, deps=deps)
        # Simulate the sheet now containing every previously written key.
        gateway.existing_keys = {make_key(r) for r in rows}
        second = se.run_sync_for_account(self.account, self.settings, deps=deps)

        self.assertEqual(first["rows_added"], 2)
        self.assertEqual(second["status"], "success")
        self.assertEqual(second["rows_added"], 0)
        self.assertEqual(len(gateway.appended_rows), 2)  # nothing new appended

    def test_expired_token_is_captured_not_raised(self):
        failure = RuntimeError("OAuthException: access token has expired")
        gateway = FakeSheetGateway()
        deps = build_deps(
            fetch=make_fake_fetch(failures={"act_111111111": failure}),
            gateway=gateway,
        )

        result = se.run_sync_for_account(self.account, self.settings, deps=deps)

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["rows_added"], 0)
        self.assertIn("access token has expired", result["message"])
        self.assertIn("RuntimeError", result["message"])
        self.assertEqual(result["account_id"], "acc-001")
        # Nothing was written and the error did not propagate.
        self.assertEqual(gateway.appended_rows, [])
        self.assertEqual(gateway.analytics_rebuilds, 0)

    def test_unauthorized_sheet_is_captured_not_raised(self):
        gateway = FakeSheetGateway(
            fail_on={"get_worksheets": RuntimeError(
                "SpreadsheetNotFound: service account lacks access"
            )}
        )
        deps = build_deps(
            fetch=make_fake_fetch(rows_by_account={"act_111111111": sample_rows("a", 1)}),
            gateway=gateway,
        )

        result = se.run_sync_for_account(self.account, self.settings, deps=deps)

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["rows_added"], 0)
        self.assertIn("lacks access", result["message"])
        self.assertEqual(gateway.appended_rows, [])

    def test_request_timeout_is_captured_not_raised(self):
        deps = build_deps(
            fetch=make_fake_fetch(failures={"act_111111111": TimeoutError("read timed out after 30s")})
        )

        result = se.run_sync_for_account(self.account, self.settings, deps=deps)

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["rows_added"], 0)
        self.assertIn("read timed out", result["message"])

    def test_failure_during_analytics_rebuild_is_an_error_result(self):
        gateway = FakeSheetGateway(fail_on={"build_analytics": RuntimeError("quota exceeded")})
        deps = build_deps(
            fetch=make_fake_fetch(rows_by_account={"act_111111111": sample_rows("a", 2)}),
            gateway=gateway,
        )

        result = se.run_sync_for_account(self.account, self.settings, deps=deps)

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["rows_added"], 0)
        self.assertIn("quota exceeded", result["message"])
        self.assertEqual(len(gateway.appended_rows), 2)  # rows were appended before failure

    def test_account_without_id_falls_back_to_ad_account_id(self):
        account = {"name": "Legacy Account", "ad_account_id": "act_999", "meta_token": "t"}
        deps = build_deps(
            fetch=make_fake_fetch(rows_by_account={"act_999": sample_rows("z", 1)})
        )

        result = se.run_sync_for_account(account, self.settings, deps=deps)

        self.assertEqual(result["account_id"], "act_999")
        self.assertEqual(result["account_name"], "Legacy Account")
        self.assertEqual(result["status"], "success")


class BatchSyncTests(unittest.TestCase):
    def setUp(self):
        self.settings = load_fixture()
        self.accounts = self.settings["accounts"]

    def _deps(self, fetch, gateway, http=None):
        return build_deps(fetch=fetch, gateway=gateway, http=http or FakeHTTP())

    def test_partial_success_continues_after_failure(self):
        # Account 1 OK, account 2 has an expired token, account 3 times out.
        rows = {
            "act_111111111": sample_rows("a", 2),
            "act_333333333": sample_rows("c", 4),
        }
        failures = {
            "act_222222222": RuntimeError("OAuthException: access token has expired"),
        }
        fetch = make_fake_fetch(rows_by_account=rows, failures=failures)
        gateways = {
            "fake-sheet-aaa": FakeSheetGateway(),
            "fake-sheet-bbb": FakeSheetGateway(),
            "fake-sheet-ccc": FakeSheetGateway(),
        }
        http = FakeHTTP()
        deps = self._deps(fetch, DispatcherSheetGateway(gateways), http=http)

        results = se.run_all_accounts(self.settings, deps=deps)

        self.assertEqual([r["account_id"] for r in results], ["acc-001", "acc-002", "acc-003"])
        self.assertEqual([r["status"] for r in results], ["success", "error", "success"])
        # Failed account is fully specified, not just logged.
        failed = results[1]
        self.assertEqual(failed["rows_added"], 0)
        self.assertIn("access token has expired", failed["message"])
        # Successful accounts still wrote their rows.
        self.assertEqual(results[0]["rows_added"], 2)
        self.assertEqual(results[2]["rows_added"], 4)
        # Batch-level classification and rollups.
        summary = se.build_batch_summary(results)
        self.assertEqual(summary["outcome"], se.OUTCOME_PARTIAL_SUCCESS)
        self.assertEqual(summary["succeeded"], 2)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["total_rows_added"], 6)
        # A Telegram summary was attempted exactly once.
        self.assertEqual(len(http.calls), 1)

    def test_all_success_outcome(self):
        fetch = make_fake_fetch(
            rows_by_account={
                "act_111111111": sample_rows("a", 1),
                "act_222222222": sample_rows("b", 1),
                "act_333333333": sample_rows("c", 1),
            }
        )
        gateways = {sid: FakeSheetGateway() for sid in
                    ("fake-sheet-aaa", "fake-sheet-bbb", "fake-sheet-ccc")}
        deps = self._deps(fetch, DispatcherSheetGateway(gateways))

        results = se.run_all_accounts(self.settings, deps=deps)

        self.assertTrue(all(r["status"] == "success" for r in results))
        self.assertEqual(se.classify_batch_outcome(results), se.OUTCOME_ALL_SUCCESS)

    def test_all_failed_outcome(self):
        fetch = make_fake_fetch(
            failures={
                "act_111111111": RuntimeError("token expired"),
                "act_222222222": TimeoutError("read timed out"),
                "act_333333333": RuntimeError("forbidden"),
            }
        )
        gateways = {sid: FakeSheetGateway() for sid in
                    ("fake-sheet-aaa", "fake-sheet-bbb", "fake-sheet-ccc")}
        deps = self._deps(fetch, DispatcherSheetGateway(gateways))

        results = se.run_all_accounts(self.settings, deps=deps)

        self.assertTrue(all(r["status"] == "error" for r in results))
        self.assertTrue(all(r["rows_added"] == 0 for r in results))
        self.assertEqual(se.classify_batch_outcome(results), se.OUTCOME_ALL_FAILED)
        # Each account keeps its own distinct reason.
        self.assertIn("token expired", results[0]["message"])
        self.assertIn("timed out", results[1]["message"])
        self.assertIn("forbidden", results[2]["message"])

    def test_no_accounts_outcome(self):
        settings = {"accounts": [], "telegram": self.settings["telegram"]}
        http = FakeHTTP()
        deps = build_deps(settings=settings, gateway=FakeSheetGateway(), http=http)

        results = se.run_all_accounts(settings, deps=deps)

        self.assertEqual(results, [])
        self.assertEqual(se.classify_batch_outcome(results), se.OUTCOME_NO_ACCOUNTS)
        # Telegram still reports the empty batch rather than crashing.
        self.assertEqual(len(http.calls), 1)

    def test_load_settings_called_when_settings_omitted(self):
        loaded = {"accounts": []}
        calls = []

        def load_settings():
            calls.append(True)
            return loaded

        deps = se.Dependencies(
            load_settings=load_settings,
            fetch_meta_data=make_fake_fetch(),
            build_row=fake_build_row,
            make_key=make_key,
            sheets=FakeSheetGateway(),
            invalidate_cache=lambda _id: None,
            now=FixedClock().now,
            http_post=FakeHTTP(),
        )

        results = se.run_all_accounts(deps=deps)

        self.assertEqual(results, [])
        self.assertEqual(calls, [True])


class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.settings = load_fixture()

    def _partial_results(self):
        fetch = make_fake_fetch(
            rows_by_account={"act_111111111": sample_rows("a", 2)},
            failures={"act_222222222": RuntimeError("OAuthException: access token has expired")},
        )
        gateways = {
            "fake-sheet-aaa": FakeSheetGateway(),
            "fake-sheet-bbb": FakeSheetGateway(),
            "fake-sheet-ccc": FakeSheetGateway(),
        }
        deps = build_deps(
            fetch=fetch,
            gateway=DispatcherSheetGateway(gateways),
            http=FakeHTTP(),
        )
        results = se.run_all_accounts(self.settings, deps=deps)
        return results, deps

    def test_summary_lists_successes_and_failures_with_reasons(self):
        results, deps = self._partial_results()
        text = se.build_telegram_message(results)

        self.assertIn("PARTIAL SUCCESS", text)
        self.assertIn("2 succeeded, 1 failed", text)
        self.assertIn("SUCCESS:", text)
        self.assertIn("North America Store", text)
        self.assertIn("2 row(s) added", text)
        self.assertIn("FAILED:", text)
        self.assertIn("Europe Store", text)
        self.assertIn("access token has expired", text)

        # The batch run actually POSTed that text to the bot endpoint.
        http = deps.http_post
        self.assertEqual(len(http.calls), 1)
        call = http.calls[0]
        self.assertEqual(
            call["url"],
            "https://api.telegram.org/botFAKE-BOT-TOKEN/sendMessage",
        )
        payload = call["kwargs"]["json"]
        self.assertEqual(payload["chat_id"], "FAKE-CHAT-ID")
        self.assertEqual(payload["text"], text)

    def test_missing_bot_token_silently_skips(self):
        settings = {"accounts": self.settings["accounts"], "telegram": {"chat_id": "x"}}
        http = FakeHTTP()
        deps = build_deps(
            fetch=make_fake_fetch(
                rows_by_account={a["ad_account_id"]: sample_rows("a", 1)
                                 for a in self.settings["accounts"]}
            ),
            gateway=DispatcherSheetGateway(
                {sid: FakeSheetGateway() for sid in
                 ("fake-sheet-aaa", "fake-sheet-bbb", "fake-sheet-ccc")}
            ),
            http=http,
            settings=settings,
        )

        results = se.run_all_accounts(settings, deps=deps)

        self.assertEqual(len(http.calls), 0)  # nothing sent
        self.assertTrue(all(r["status"] == "success" for r in results))  # sync unaffected
        self.assertFalse(se.send_telegram_alert("hi", settings=settings, deps=deps))

    def test_missing_chat_id_silently_skips(self):
        settings = {"telegram": {"bot_token": "tok"}}
        http = FakeHTTP()
        deps = build_deps(http=http)
        self.assertFalse(se.send_telegram_alert("hi", settings=settings, deps=deps))
        self.assertEqual(http.calls, [])

    def test_telegram_http_error_is_swallowed(self):
        http = FakeHTTP(raise_exc=ConnectionError("network unreachable"))
        fetch = make_fake_fetch(
            rows_by_account={
                "act_111111111": sample_rows("a", 1),
                "act_222222222": sample_rows("b", 1),
                "act_333333333": sample_rows("c", 1),
            }
        )
        gateways = {sid: FakeSheetGateway() for sid in
                    ("fake-sheet-aaa", "fake-sheet-bbb", "fake-sheet-ccc")}
        deps = build_deps(fetch=fetch, gateway=DispatcherSheetGateway(gateways), http=http)

        # Must not raise even though the notification fails.
        results = se.run_all_accounts(self.settings, deps=deps)

        self.assertEqual(len(results), 3)
        self.assertTrue(all(r["status"] == "success" for r in results))
        self.assertEqual(se.classify_batch_outcome(results), se.OUTCOME_ALL_SUCCESS)

    def test_telegram_api_4xx_returns_false(self):
        http = FakeHTTP(response=FakeResponse(status_code=401))
        deps = build_deps(http=http)
        sent = se.send_telegram_alert("hello", settings=self.settings, deps=deps)
        self.assertFalse(sent)
        self.assertEqual(len(http.calls), 1)


class DateRangeTests(unittest.TestCase):
    def test_explicit_window(self):
        settings = {"date_range": {"start": "2026-11-01", "end": "2026-11-07"}}
        start, end = se.resolve_date_range(settings, today=date(2026, 11, 15))
        self.assertEqual((start, end), (date(2026, 11, 1), date(2026, 11, 7)))

    def test_yesterday_preset(self):
        settings = {"date_range": "yesterday"}
        start, end = se.resolve_date_range(settings, today=date(2026, 11, 15))
        self.assertEqual(start, date(2026, 11, 14))
        self.assertEqual(end, date(2026, 11, 14))

    def test_maximum_preset_uses_lookback(self):
        settings = {"date_range": "maximum", "lookback_days": 7}
        start, end = se.resolve_date_range(settings, today=date(2026, 11, 15))
        self.assertEqual(end, date(2026, 11, 14))
        self.assertEqual(start, date(2026, 11, 8))

    def test_integer_preset(self):
        settings = {"date_range": 3}
        start, end = se.resolve_date_range(settings, today=date(2026, 11, 15))
        self.assertEqual((start, end), (date(2026, 11, 12), date(2026, 11, 14)))

    def test_missing_config_defaults_to_yesterday(self):
        start, end = se.resolve_date_range({}, today=date(2026, 11, 15))
        self.assertEqual((start, end), (date(2026, 11, 14), date(2026, 11, 14)))


class NoRealCredentialsTests(unittest.TestCase):
    def test_engine_imports_and_wires_without_gspread(self):
        # gspread is intentionally absent in this environment; the engine and
        # its default wiring must still import (third-party imports are lazy).
        import sys

        self.assertNotIn("gspread", sys.modules)
        deps = se.default_dependencies()
        self.assertNotIn("gspread", sys.modules)
        self.assertIsInstance(deps.sheets, se.RealSheetGateway)

    def test_default_gateway_only_touches_gspread_when_called(self):
        gateway = se.RealSheetGateway()
        with self.assertRaises(ModuleNotFoundError):
            # No gspread installed -> proves no real Google call can escape.
            gateway.get_worksheets({"sheet_id": "whatever"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
