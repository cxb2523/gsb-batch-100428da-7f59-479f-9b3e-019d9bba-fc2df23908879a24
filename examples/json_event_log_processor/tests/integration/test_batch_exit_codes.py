from __future__ import annotations

"""Process-level exit policy for the batch entrypoint.

These tests run the real ``__main__`` entrypoint in a disposable project copy
so the scheduler-visible exit code is proven, not just a direct ``main()`` call.
"""

import json
import os
from pathlib import Path

import pytest

from rpacore import list_transactions

INVALID_EVENT = {
    "event_id": "evt-001",
    "timestamp": "2024-01-01T00:00:00Z",
    "source": "test-service",
}
VALID_EVENT = {
    "event_id": "evt-001",
    "event_type": "info",
    "timestamp": "2024-01-01T00:00:00Z",
    "source": "test-service",
    "payload": {},
}


def _file_transactions(project: Path) -> dict[str, object]:
    return {
        tx.reference: tx
        for tx in list_transactions(str(project / "rpacore.db"))
        if tx.metadata.get("transaction_kind") != "error-report"
    }


def test_completed_batch_with_business_rejections_exits_zero(run_batch) -> None:
    project, completed, report = run_batch({
        "events_001.json": json.dumps([VALID_EVENT]),
        "events_003.json": json.dumps([INVALID_EVENT]),
    })

    assert completed.returncode == 0, completed.stderr

    transactions = _file_transactions(project)
    assert transactions["json-file-events_001"].status.name == "SUCCESSFUL"
    assert transactions["json-file-events_003"].status.name == "FAILED"

    output_lines = (project / "results" / "events_001_cleaned.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(output_lines) == 1
    assert json.loads(output_lines[0])["severity"] == "INFO"
    assert not (project / "results" / "events_003_cleaned.jsonl").exists()

    assert report["total_transactions"] == 2
    assert report["successful"] == 1
    assert report["failed"] == 1
    assert report["unresolved"] == 0
    assert report["persistence_error_count"] == 0
    assert report["failures"][0]["failure_code"] == "json_event_log.validation.invalid_event"


def test_all_business_rejections_exits_zero(run_batch) -> None:
    _project, completed, report = run_batch({
        "events_003.json": json.dumps([INVALID_EVENT]),
        "events_004.json": json.dumps([{**VALID_EVENT, "event_type": "critical"}]),
    })

    assert completed.returncode == 0, completed.stderr
    assert report["successful"] == 0
    assert report["failed"] == 2
    assert {failure["outcome_category"] for failure in report["failures"]} == {"business_failed"}


def test_empty_batch_exits_zero(run_batch) -> None:
    project, completed, report = run_batch({})

    assert completed.returncode == 0, completed.stderr
    assert report["total_transactions"] == 0
    assert _file_transactions(project) == {}
    assert [
        tx.status.name
        for tx in list_transactions(str(project / "rpacore.db"))
        if tx.reference == "error-report"
    ] == ["SUCCESSFUL"]


@pytest.mark.parametrize(
    ("filename", "content", "code"),
    [
        ("events_005.json", "{not valid JSON", "json_event_log.input.malformed_json"),
        ("events_006.json", json.dumps("just a string"), "json_event_log.input.unsupported_document"),
        ("events_007.json", json.dumps(42), "json_event_log.input.unsupported_document"),
    ],
    ids=["malformed", "scalar-string", "scalar-number"],
)
def test_bad_published_input_is_a_terminal_business_rejection(
    run_batch,
    filename: str,
    content: str,
    code: str,
) -> None:
    project, completed, report = run_batch({filename: content})

    assert completed.returncode == 0, completed.stderr
    failure = report["failures"][0]
    assert failure["outcome_category"] == "business_failed"
    assert failure["retry_disposition"] == "not_requested"
    assert failure["retry_count"] == 0
    assert failure["failure_code"] == code
    # max_retries = 2 in the disposable config: a bad document must not retry.
    assert _file_transactions(project)[f"json-file-{Path(filename).stem}"].retry_count == 0


def test_staging_file_is_ignored_until_it_is_renamed_into_place(run_batch) -> None:
    project, first_completed, first_report = run_batch({
        "events_010.json.tmp": json.dumps([VALID_EVENT]),
    })

    assert first_completed.returncode == 0, first_completed.stderr
    assert first_report["total_transactions"] == 0
    assert not (project / "results" / "events_010_cleaned.jsonl").exists()

    staging = project / "inbox" / "events_010.json.tmp"
    os.replace(staging, project / "inbox" / "events_010.json")

    _project, second_completed, second_report = run_batch()

    assert second_completed.returncode == 0, second_completed.stderr
    assert second_report["total_transactions"] == 1
    assert second_report["successful"] == 1
    assert (project / "results" / "events_010_cleaned.jsonl").read_text(encoding="utf-8").count("\n") == 1


@pytest.mark.parametrize(
    "timestamp",
    [1704067200, "not-a-timestamp", "0001-01-01T00:00:00+01:00"],
    ids=["epoch-int", "text", "utc-overflow"],
)
def test_truthy_invalid_timestamp_is_rejected_before_normalization(run_batch, timestamp) -> None:
    project, completed, report = run_batch({
        "events_001.json": json.dumps([VALID_EVENT]),
        "events_002.json": json.dumps([{**VALID_EVENT, "timestamp": timestamp}]),
    })

    assert completed.returncode == 0, completed.stderr
    failure = report["failures"][0]
    assert failure["transaction_reference"] == "json-file-events_002"
    assert failure["outcome_category"] == "business_failed"
    assert failure["retry_count"] == 0
    assert failure["failure_code"] == "json_event_log.validation.invalid_event"
    assert failure["failed_steps"][0]["step_name"] == "validate_events"
    assert _file_transactions(project)["json-file-events_002"].retry_count == 0
    assert (project / "results" / "events_001_cleaned.jsonl").exists()
    assert not (project / "results" / "events_002_cleaned.jsonl").exists()


def test_file_system_failure_exits_nonzero_and_keeps_other_outputs(run_batch) -> None:
    project, first_completed, _first_report = run_batch(
        {"events_001.json": json.dumps([VALID_EVENT])},
    )
    assert first_completed.returncode == 0, first_completed.stderr
    # A directory named like an input is unreadable: the entry is selected but
    # opening it fails, which stays a system failure rather than bad data.
    (project / "inbox" / "events_002.json").mkdir()

    _project, completed, report = run_batch()

    assert completed.returncode == 1, completed.stderr
    failure = report["failures"][0]
    assert failure["transaction_reference"] == "json-file-events_002"
    assert failure["outcome_category"] == "system_failed"
    assert failure["retry_count"] == 2
    assert failure["failure_code"] == "json_event_log.input.read_failed"
    assert (project / "results" / "events_001_cleaned.jsonl").exists()
    assert report["successful"] == 1
    assert report["failed"] == 1
    assert report["persistence_error_count"] == 0


def test_report_publication_failure_exits_nonzero_and_keeps_the_prior_report(run_batch) -> None:
    project, _, first_report = run_batch({})
    # A directory cannot be replaced by a file, so publishing the report fails
    # while the previous destination stays exactly as it was.
    prior = project / "results" / "error_report.json"
    prior.unlink()
    prior.mkdir()
    assert first_report["total_transactions"] == 0

    _project, completed, report = run_batch()

    assert completed.returncode == 1, completed.stdout
    # No report was published, so the fixture reports no report file at all.
    assert report is None
    assert prior.is_dir()
    assert not list((project / "results").glob(".*.tmp"))
    # The publication failure is itself durable: the failed attempt is a real
    # failed error-report transaction, so the missing report stays auditable.
    report_transactions = [
        tx
        for tx in list_transactions(str(project / "rpacore.db"))
        if tx.reference == "error-report"
    ]
    assert sorted(tx.status.name for tx in report_transactions) == ["FAILED", "SUCCESSFUL"]
    failed_report = [tx for tx in report_transactions if tx.status.name == "FAILED"][0]
    assert failed_report.outcome_category == "system_failed"
    assert failed_report.metadata["report_attempt"] == 1
