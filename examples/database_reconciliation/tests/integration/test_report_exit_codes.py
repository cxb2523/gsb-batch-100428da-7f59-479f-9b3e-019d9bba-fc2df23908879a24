"""Process-level exit policy for the reconciliation entrypoint.

These tests run the real entrypoint in a disposable project copy so the
scheduler-visible exit code is proven, not just a direct ``main()`` call.
"""

from __future__ import annotations

import csv

import pytest

from rpacore import list_transactions

from tests.conftest import MATCHING_BANK, MISMATCHED_BANK, PRIOR_REPORT


def _transactions(project):
    return {
        tx.reference: tx
        for tx in list_transactions(str(project / "rpacore.db"))
    }


def _report_rows(report_file):
    with report_file.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_matching_payment_exits_zero_and_announces_completion(run_reconciliation):
    project, completed, report_file = run_reconciliation()

    assert completed.returncode == 0, completed.stderr
    assert "Reconciliation complete. matched=1 discrepancies=0" in completed.stderr

    transactions = _transactions(project)
    assert transactions["load-reconciliation-inputs"].status.name == "SUCCESSFUL"
    assert transactions["payment-PAY-1"].status.name == "SUCCESSFUL"
    report_transaction = transactions["write-reconciliation-report"]
    assert report_transaction.status.name == "SUCCESSFUL"
    # The completion line names the report, so the run must also record the
    # artifact record that A4 report history builds on.
    artifact = report_transaction.artifacts[0]
    assert artifact.name == "reconciliation_report"
    assert artifact.path == str(report_file)
    assert artifact.metadata["record_count"] == 1

    assert _report_rows(report_file)[0]["status"] == "matched"


def test_discrepancy_exits_nonzero_after_publishing_the_report(run_reconciliation):
    project, completed, report_file = run_reconciliation(bank=MISMATCHED_BANK)

    assert completed.returncode == 1, completed.stderr
    transactions = _transactions(project)
    assert transactions["write-reconciliation-report"].status.name == "SUCCESSFUL"
    assert transactions["payment-PAY-1"].metadata["reconciliation_status"] == "amount_mismatch"
    assert [row["status"] for row in _report_rows(report_file)] == ["amount_mismatch"]


def test_no_payments_exits_zero_with_an_empty_report(run_reconciliation):
    project, completed, report_file = run_reconciliation(
        internal="payment_id,date,reference,amount,vendor\n"
    )

    assert completed.returncode == 0, completed.stderr
    assert "Reconciliation complete. matched=0 discrepancies=0" in completed.stderr
    report_transaction = _transactions(project)["write-reconciliation-report"]
    assert report_transaction.status.name == "SUCCESSFUL"
    assert report_transaction.artifacts[0].metadata["record_count"] == 0
    assert _report_rows(report_file) == []


@pytest.mark.parametrize(
    "bank",
    [MATCHING_BANK, MISMATCHED_BANK],
    ids=["matching", "discrepancy"],
)
def test_report_write_failure_exits_nonzero_and_keeps_the_prior_report(
    run_reconciliation, bank
):
    project, completed, report_file = run_reconciliation(
        bank=bank, prior_report=PRIOR_REPORT, fault="report_write"
    )

    assert completed.returncode == 1, completed.stderr
    assert "Reconciliation report failed" in completed.stderr
    assert "Reconciliation complete" not in completed.stdout + completed.stderr
    # The failure lands on a data row, so the temporary file held a header
    # before it was discarded and the prior report is untouched either way.
    assert _transactions(project)["write-reconciliation-report"].status.name == "FAILED"
    assert report_file.read_bytes() == PRIOR_REPORT
    assert not list(report_file.parent.glob(".*"))


def test_report_save_failure_exits_nonzero_without_announcing_completion(
    run_reconciliation,
):
    project, completed, report_file = run_reconciliation(fault="report_save")

    assert completed.returncode != 0, completed.stderr
    assert "injected report save failure" in completed.stderr
    assert "Reconciliation complete" not in completed.stdout + completed.stderr
    # The report was published before the failed save. That file is not rolled
    # back, and no report row records the failed attempt: this run is truthful
    # only in its exit code and its log.
    assert _report_rows(report_file)[0]["status"] == "matched"
    assert "write-reconciliation-report" not in _transactions(project)
