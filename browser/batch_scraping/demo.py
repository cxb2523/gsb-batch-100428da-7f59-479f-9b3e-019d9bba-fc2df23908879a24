"""End-to-end demo: run a mixed batch against a local mock AgentInBrowser.

Starts the mock server in-process, executes a deliberately imperfect step
list (one recoverable page refresh, one permanently missing element, a
popup window with a failure inside it) and prints the full settlement
report, proving that:

* one failed step never aborts the batch,
* dependent steps are marked SKIPPED, never falsely successful,
* a stale index is recovered with one re-find + one retry,
* window switches invalidate the element cache and closes return to the
  main window,
* every successfully collected text survives in the final report.

Run::

    python -I demo.py
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from aib_batch import AIBClient, BatchRunner, format_report, write_json_report
from tests.server_harness import MockServerHandle


def _inject_stale(base_url: str) -> None:
    import json
    import urllib.request

    req = urllib.request.Request(
        base_url + "/inject",
        data=json.dumps({"kind": "stale"}).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req).read()


def _merge_reports(reports: list) -> dict:
    """Stitch consecutive run_batch reports into one settlement view."""
    summary = {"success": 0, "failed": 0, "skipped": 0}
    steps, results, reasons = [], {}, []
    seq_offset = 0
    for report in reports:
        for key in summary:
            summary[key] += report["summary"][key]
        results.update(report["results"])
        for bucket in report["failure_reasons"]:
            bucket = dict(bucket)
            bucket["seqs"] = [s + seq_offset for s in bucket["seqs"]]
            reasons.append(bucket)
        for record in report["steps"]:
            record = dict(record)
            record["seq"] += seq_offset
            record["skipped_due_to"] = [
                s + seq_offset for s in record["skipped_due_to"]
            ]
            steps.append(record)
        seq_offset += report["total"]
    return {
        "total": seq_offset,
        "summary": summary,
        "failure_reasons": reasons,
        "results": results,
        "steps": steps,
    }


def build_form_steps() -> list:
    return [
        # ---- main window: form + navigation (all succeed) ----
        {"op": "inputs", "name": "locate-inputs"},
        {"op": "send_keys", "index": 0, "text": "alice", "name": "type-user"},
        {"op": "send_keys", "index": 1, "text": "secret", "name": "type-pass"},
        {"op": "buttons", "name": "locate-button"},
        {"op": "click", "index": 0, "name": "login"},
        {"op": "get_text", "store": "detail_text", "name": "read-detail"},
    ]


def build_recovery_steps() -> list:
    return [
        # ---- recoverable stale index: re-find then retry ----
        {"op": "find", "selector": ".record", "name": "find-records"},
        {"op": "get_html", "index": 0, "store": "first_record",
         "name": "record-html"},

        # ---- hard failure: element will not exist after re-find ----
        {"op": "find", "selector": ".ghost", "name": "find-ghost"},
        {"op": "click", "index": 0, "name": "click-ghost"},
        # Depends on the failure -> skipped, never a fake success.
        {"op": "get_text", "name": "after-ghost",
         "depends_on": "click-ghost", "store": "never_stored"},

        # ---- an independent step still runs after the failure ----
        {"op": "page_info", "name": "detail-info"},
    ]


def build_window_steps() -> list:
    return [
        {"op": "buttons", "name": "find-popup-open"},
        {"op": "click", "index": 0, "name": "open-popup"},
        {"op": "switch_window", "index": -1, "name": "switch-to-popup"},
        # Old cache is invalid after the switch; finder must run here.
        {"op": "find", "selector": ".note", "name": "find-notes"},
        {"op": "get_html", "index": 0, "store": "popup_note",
         "name": "popup-note"},
        # A failure inside the popup.
        {"op": "find", "selector": ".missing", "name": "find-missing-in-popup"},
        {"op": "click", "index": 0, "name": "fail-in-popup"},
        # Close and continue on the main window regardless.
        {"op": "close_window", "name": "close-popup"},
        {"op": "find", "selector": "#lone-span", "name": "refind-main"},
        {"op": "get_html", "index": 0, "store": "main_span",
         "name": "main-span"},
        {"op": "get_text", "store": "main_text", "name": "read-main"},
    ]


def main() -> int:
    sites = os.path.join(HERE, "sites")
    with MockServerHandle(sites) as server:
        client = AIBClient(server.base_url)

        # Scenario A: stale-index recovery + hard failure + skips.
        client.init("/pages/index.html")
        runner = BatchRunner(client)
        report_form = runner.run_batch(build_form_steps())

        # Queue one transient page refresh against the next indexed command:
        # the get_html step must recover via one re-find + one retry.
        _inject_stale(server.base_url)

        report_recovery = runner.run_batch(build_recovery_steps())
        report_a = _merge_reports([report_form, report_recovery])
        print("\n########## SCENARIO A: recovery, hard failure, skips ##########")
        print(format_report(report_a))

        # Scenario B: popup window with internal failure, close, continue.
        client.init("/pages/failure.html")
        runner_b = BatchRunner(client)
        report_b = runner_b.run_batch(build_window_steps())
        print("\n########## SCENARIO B: popup window, failure, resume ##########")
        print(format_report(report_b))

        out_path = os.path.join(HERE, "demo_report.json")
        write_json_report({"scenario_a": report_a, "scenario_b": report_b},
                          out_path)
        print(f"\nMachine-readable report written to: {out_path}")

        # Self-check the guarantees the task asks for.
        assert report_a["summary"]["failed"] >= 1
        assert report_a["summary"]["skipped"] >= 1
        assert "detail_text" in report_a["results"]
        assert "first_record" in report_a["results"]
        assert "never_stored" not in report_a["results"]
        assert report_b["summary"]["skipped"] == 0
        assert "popup_note" in report_b["results"]
        assert "main_text" in report_b["results"]
        print("All demo assertions passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
