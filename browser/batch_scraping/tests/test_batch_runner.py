"""End-to-end verification of the batch scraping layer over real HTTP.

The mock server below speaks the exact AgentInBrowser wire protocol, so these
tests exercise the full client -> HTTP -> server path, including the
ephemeral-index and window-cache semantics that motivate the batch layer.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from aib_batch import (  # noqa: E402
    AIBClient,
    BatchRunner,
    STATUS_FAILED,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
    format_report,
)
from tests.server_harness import MockServerHandle  # noqa: E402

SITES = os.path.join(ROOT, "sites")
INDEX_URL = "/pages/index.html"
DETAIL_URL = "/pages/detail.html"
LIST_URL = "/pages/list.html"
FAILURE_URL = "/pages/failure.html"


class BatchTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.server = MockServerHandle(SITES)
        self.server.__enter__()
        self.client = AIBClient(self.server.base_url)
        self.client.init(INDEX_URL)
        self.runner = BatchRunner(self.client)

    def tearDown(self) -> None:
        self.server.__exit__(None, None, None)

    def run_steps(self, steps):
        report = self.runner.run_batch(steps)
        # Keep the rendered report visible in verbose test output.
        print("\n" + format_report(report))
        return report

    def inject(self, kind: str = "stale"):
        req = urllib.request.Request(
            self.server.base_url + "/inject",
            data=json.dumps({"kind": kind}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))


class TestHappyPath(BatchTestBase):
    def test_full_happy_path_settles_every_step(self):
        steps = [
            {"op": "inputs", "name": "locate-inputs"},
            {"op": "send_keys", "index": 0, "text": "alice", "name": "type-user"},
            {"op": "send_keys", "index": 1, "text": "secret", "name": "type-pass"},
            {"op": "buttons", "name": "locate-buttons"},
            {"op": "click", "index": 0, "name": "login"},
            {"op": "get_text", "store": "detail_text", "name": "read-detail"},
            {"op": "find", "selector": ".record", "name": "find-records",
             "store": "records"},
        ]
        report = self.run_steps(steps)

        self.assertEqual(report["summary"],
                         {"success": 7, "failed": 0, "skipped": 0})
        self.assertEqual(report["total"], 7)
        # Navigated to the detail page and its text was retained.
        self.assertIn("Detail Page", report["results"]["detail_text"])
        self.assertEqual(report["results"]["records"]["count"], 3)
        # The mock browser has moved to the detail page (no inputs there);
        # verify the typed values via stored HTML before navigation instead.
        self.client.init(INDEX_URL)
        report2 = self.runner.run_batch([
            {"op": "inputs"},
            {"op": "send_keys", "index": 0, "text": "alice"},
            {"op": "send_keys", "index": 1, "text": "secret"},
        ])
        self.assertEqual(report2["summary"]["failed"], 0)
        window = self.server.browser.window
        inputs = [el for el in _all_elements(window.root) if el.tag == "input"]
        self.assertEqual(inputs[0].attrs.get("value"), "alice")
        self.assertEqual(inputs[1].attrs.get("value"), "secret")


class TestFailureIsolation(BatchTestBase):
    def test_single_failure_does_not_abort_later_independent_steps(self):
        steps = [
            {"op": "get_text", "store": "before_text", "name": "read-before"},
            {"op": "find", "selector": ".does-not-exist", "name": "find-missing"},
            # Index step with an empty cache -> hard failure.
            {"op": "click", "index": 0, "name": "click-missing"},
            # Independent step must still execute and succeed.
            {"op": "page_info", "name": "page-info"},
            {"op": "get_text", "store": "after_text", "name": "read-after"},
        ]
        report = self.run_steps(steps)

        statuses = [r["status"] for r in report["steps"]]
        self.assertEqual(statuses[0], STATUS_SUCCESS)
        # find itself succeeds with count 0; the click fails.
        self.assertEqual(statuses[1], STATUS_SUCCESS)
        self.assertEqual(statuses[2], STATUS_FAILED)
        self.assertEqual(statuses[3], STATUS_SUCCESS)
        self.assertEqual(statuses[4], STATUS_SUCCESS)
        self.assertEqual(report["summary"]["failed"], 1)
        self.assertIn("Batch Scraping Home", report["results"]["before_text"])
        self.assertIn("Batch Scraping Home", report["results"]["after_text"])
        reason = report["failure_reasons"][0]
        self.assertIn(reason["reason"],
                      {"empty-element-cache", "element-not-found",
                       "stale-index", "command-error"})

    def test_dependent_step_is_skipped_not_falsely_successful(self):
        steps = [
            {"op": "find", "selector": ".does-not-exist", "name": "finder"},
            {"op": "click", "index": 0, "name": "consumer"},
            {"op": "get_html", "index": 0, "name": "dependent-html",
             "depends_on": "consumer"},
            # An explicitly named dependency chain.
            {"op": "get_text", "name": "downstream",
             "depends_on": "dependent-html", "store": "late_text"},
        ]
        report = self.run_steps(steps)
        by_name = {r["name"]: r for r in report["steps"]}

        self.assertEqual(by_name["consumer"]["status"], STATUS_FAILED)
        self.assertEqual(by_name["dependent-html"]["status"], STATUS_SKIPPED)
        self.assertEqual(by_name["downstream"]["status"], STATUS_SKIPPED)
        self.assertEqual(by_name["downstream"]["skipped_due_to"],
                         [by_name["dependent-html"]["seq"]])
        self.assertNotIn("late_text", report["results"])
        self.assertEqual(report["summary"]["skipped"], 2)

    def test_missing_variable_is_failure_and_dependents_skip(self):
        steps = [
            {"op": "send_keys", "index": 0, "text": "${not_set}",
             "name": "use-undefined-var"},
            {"op": "get_text", "name": "after", "depends_on": "use-undefined-var"},
        ]
        report = self.run_steps(steps)
        by_name = {r["name"]: r for r in report["steps"]}
        self.assertEqual(by_name["use-undefined-var"]["status"], STATUS_FAILED)
        self.assertEqual(by_name["use-undefined-var"]["reason"],
                         "missing-variable")
        self.assertEqual(by_name["after"]["status"], STATUS_SKIPPED)

    def test_failed_store_producer_marks_consumer_skipped_not_variable_error(self):
        steps = [
            {"op": "find", "selector": ".ghost", "name": "ghost-find"},
            # Producer fails (empty cache after re-find).
            {"op": "get_html", "index": 0, "store": "ghost_html",
             "name": "ghost-producer"},
            # Consumer references the failed producer's variable: must be
            # SKIPPED as a dependency, not a missing-variable failure.
            {"op": "execute_script",
             "script": "return document.title + ${ghost_html}",
             "name": "ghost-consumer"},
        ]
        report = self.run_steps(steps)
        by_name = {r["name"]: r for r in report["steps"]}
        self.assertEqual(by_name["ghost-producer"]["status"], STATUS_FAILED)
        self.assertEqual(by_name["ghost-consumer"]["status"], STATUS_SKIPPED)
        self.assertNotIn("ghost_html", report["results"])
        self.assertEqual(
            by_name["ghost-consumer"]["skipped_due_to"],
            [by_name["ghost-producer"]["seq"]],
        )


class TestRetryRecovery(BatchTestBase):
    def test_stale_index_triggers_one_refind_and_retry_succeeds(self):
        # Finder and indexed step live in the SAME batch so the runner still
        # holds the replayable finder when the stale event fires.
        self.inject("stale")
        report = self.run_steps([
            {"op": "find", "selector": ".nav-link", "name": "find-links"},
            {"op": "get_html", "index": 0, "name": "recovered-html"},
        ])
        record = report["steps"][1]
        self.assertEqual(record["status"], STATUS_SUCCESS)
        self.assertTrue(record["retried"])
        self.assertEqual(record["attempts"], 2)
        self.assertIn("Detail page", record["data"]["html"])

    def test_refind_replayed_against_correct_selector(self):
        # Two different finders in sequence; recovery must replay the LAST one.
        steps = [
            {"op": "find", "selector": ".nav-link", "name": "first-find"},
            {"op": "find", "selector": "button", "name": "last-find"},
        ]
        self.runner.run_batch(steps)
        self.assertEqual(self.runner._last_finder["spec"]["cmd"], "find")
        self.assertEqual(
            self.runner._last_finder["spec"]["params"]["selector"], "button"
        )

    def test_hard_failure_when_indexed_call_keeps_failing(self):
        # Two queued stale events: even before a retry can be attempted the
        # second cache check is stale too, so the step is a hard failure
        # classified as stale-index rather than a false success or a skip.
        self.inject("stale")
        self.inject("stale")
        report = self.run_steps([
            {"op": "find", "selector": ".nav-link", "name": "find-links"},
            {"op": "get_html", "index": 0, "name": "twice-stale"},
        ])
        record = report["steps"][1]
        self.assertEqual(record["status"], STATUS_FAILED)
        self.assertEqual(record["reason"], "stale-index")
        self.assertIn("stale", (record["error"] or "").lower())

    def test_hard_failure_when_refind_returns_empty_cache(self):
        # Simulate an element that disappears permanently: after the first
        # stale failure the re-find returns zero elements, so the retried
        # indexed command fails on an empty cache and the batch records a
        # hard failure with both error messages preserved.
        self.client.init(LIST_URL)
        report = self.run_steps([
            {"op": "find", "selector": ".remove-btn", "name": "find-removers"},
            {"op": "click", "index": 0, "name": "remove-first"},
            {"op": "click", "index": 0, "name": "remove-second"},
            {"op": "click", "index": 0, "name": "remove-third"},
            # All three items gone; re-find finds no remove buttons.
            {"op": "click", "index": 0, "name": "remove-fourth"},
        ])
        record = report["steps"][-1]
        self.assertEqual(record["status"], STATUS_FAILED)
        self.assertTrue(record["retried"])
        self.assertIn("re-find returned 0 elements", record["error"])
        self.assertEqual(record["reason"], "element-not-found")

    def test_index_out_of_range_without_cached_finder_fails_cleanly(self):
        report = self.runner.run_batch([
            {"op": "click", "index": 99, "name": "bad-index"},
        ])
        record = report["steps"][0]
        self.assertEqual(record["status"], STATUS_FAILED)
        self.assertFalse(record["retried"])

    def test_dom_mutation_recovered_by_refind_on_mutable_page(self):
        self.client.init(LIST_URL)
        report = self.runner.run_batch([
            {"op": "find", "selector": ".remove-btn", "name": "find-removers"},
            # Clicking remove mutates the DOM and empties the cache.
            {"op": "click", "index": 0, "name": "remove-first"},
            # Cache is now stale; runner must re-find before this click.
            {"op": "click", "index": 0, "name": "remove-second"},
            {"op": "get_text", "store": "list_text"},
        ])
        statuses = [r["status"] for r in report["steps"]]
        self.assertEqual(statuses, [STATUS_SUCCESS, STATUS_SUCCESS,
                                    STATUS_SUCCESS, STATUS_SUCCESS])
        self.assertTrue(report["steps"][2]["retried"])
        # Two of three items removed.
        remaining = self.server.browser.window.root
        items = [el for el in _all_elements(remaining)
                 if el.tag == "li" and "item" in el.class_list]
        self.assertEqual(len(items), 1)


class TestWindowSwitching(BatchTestBase):
    def _open_popup_steps(self):
        self.client.init(FAILURE_URL)
        return [
            {"op": "buttons", "name": "find-open"},
            {"op": "click", "index": 0, "name": "open-popup"},
            {"op": "switch_window", "index": -1, "name": "to-popup"},
        ]

    def test_cache_invalidated_after_switch_and_refind_required(self):
        steps = self._open_popup_steps() + [
            # No finder has run in the popup window yet -> cache empty.
            {"op": "click", "index": 0, "name": "stale-popup-click"},
            {"op": "find", "selector": "#popup-title", "name": "popup-find"},
            {"op": "get_html", "index": 0, "name": "popup-title-html",
             "store": "popup_title"},
        ]
        report = self.runner.run_batch(steps)
        by_name = {r["name"]: r for r in report["steps"]}
        self.assertEqual(by_name["stale-popup-click"]["status"], STATUS_FAILED)
        self.assertEqual(by_name["popup-find"]["status"], STATUS_SUCCESS)
        self.assertEqual(by_name["popup-title-html"]["status"], STATUS_SUCCESS)
        self.assertIn("Popup Window", report["results"]["popup_title"])
        self.assertEqual(len(self.server.browser.windows), 2)

    def test_close_window_returns_to_main_and_remaining_steps_continue(self):
        steps = self._open_popup_steps() + [
            {"op": "find", "selector": ".note", "name": "popup-notes"},
            {"op": "get_text", "store": "popup_text", "name": "read-popup"},
            {"op": "close_window", "name": "close-popup"},
            # Back on the main window; its old cache must be gone, so find first.
            {"op": "find", "selector": "#lone-span", "name": "main-refind"},
            {"op": "get_html", "index": 0, "name": "main-html",
             "store": "main_span"},
            {"op": "get_text", "store": "main_text", "name": "read-main"},
        ]
        report = self.runner.run_batch(steps)
        self.assertEqual(report["summary"]["failed"], 0)
        self.assertEqual(report["summary"]["skipped"], 0)
        self.assertEqual(len(self.server.browser.windows), 1)
        self.assertEqual(self.server.browser.window.url, FAILURE_URL)
        self.assertIn("popup note one", report["results"]["popup_text"])
        self.assertIn("lone element", report["results"]["main_span"])
        self.assertIn("Controlled Failure Scenarios",
                      report["results"]["main_text"])

    def test_failure_inside_popup_does_not_lose_main_window_data(self):
        steps = self._open_popup_steps() + [
            {"op": "find", "selector": ".note", "name": "notes"},
            {"op": "get_html", "index": 0, "store": "note_one",
             "name": "note-html"},
            {"op": "find", "selector": ".ghost", "name": "ghost-find"},
            {"op": "click", "index": 0, "name": "ghost-click"},
            {"op": "close_window", "name": "close-popup"},
            {"op": "get_text", "store": "main_after", "name": "main-after"},
        ]
        report = self.runner.run_batch(steps)
        by_name = {r["name"]: r for r in report["steps"]}
        self.assertEqual(by_name["ghost-click"]["status"], STATUS_FAILED)
        self.assertEqual(by_name["close-popup"]["status"], STATUS_SUCCESS)
        self.assertEqual(by_name["main-after"]["status"], STATUS_SUCCESS)
        # Popup collection retained despite the later failure.
        self.assertIn("popup note one", report["results"]["note_one"])
        self.assertIn("Controlled Failure Scenarios",
                      report["results"]["main_after"])


class TestReportShape(BatchTestBase):
    def test_report_contains_counts_reasons_and_retained_data(self):
        steps = [
            {"op": "get_text", "store": "kept", "name": "keep"},
            {"op": "find", "selector": ".nope", "name": "empty-find"},
            {"op": "click", "index": 2, "name": "fail-click"},
            {"op": "get_text", "name": "skipped-text",
             "depends_on": "fail-click"},
        ]
        report = self.runner.run_batch(steps)

        self.assertEqual(report["total"], 4)
        self.assertEqual(sum(report["summary"].values()), 4)
        # get_text and the empty find both succeed; click fails; the
        # explicitly dependent get_text is skipped.
        self.assertEqual(report["summary"]["success"], 2)
        self.assertEqual(report["summary"]["failed"], 1)
        self.assertEqual(report["summary"]["skipped"], 1)
        self.assertEqual(len(report["failure_reasons"]), 1)
        self.assertIn("kept", report["results"])
        # Every record carries the full settlement envelope.
        for record in report["steps"]:
            self.assertTrue(
                { "seq", "op", "status", "attempts", "reason",
                  "error", "skipped_due_to", "data", "duration_ms" }
                <= set(record)
            )
        # Renderer must not raise and must mention every status.
        rendered = format_report(report)
        self.assertIn("Batch settlement report", rendered)
        self.assertIn("FAILED", rendered)
        self.assertIn("SKIPPED", rendered)
        self.assertIn("Failure reason summary", rendered)
        self.assertIn("kept =", rendered)

    def test_json_roundtrip(self):
        import tempfile

        report = self.runner.run_batch([
            {"op": "page_info", "name": "info"},
        ])
        with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False,
                                         encoding="utf-8") as tmp:
            path = tmp.name
        from aib_batch import write_json_report
        write_json_report(report, path)
        with open(path, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        self.assertEqual(loaded["summary"], report["summary"])
        os.unlink(path)


def _all_elements(root):
    from mock_server.dom import all_elements
    return all_elements(root)


if __name__ == "__main__":
    unittest.main(verbosity=2)
