from __future__ import annotations

"""Unit tests for main.py helper functions."""

import json
from pathlib import Path
from unittest.mock import patch

from rpacore import Engine, Status, Transaction, list_transactions

from main import (
    _make_error_report_tx,
    _record_persistence_error,
    _run_error_report,
    _save_transaction_safely,
)
from steps.write_error_report import WriteErrorReport


def test_make_error_report_tx_sets_report_metadata() -> None:
    tx = _make_error_report_tx("run-1", report_attempt=2)

    assert tx.reference == "error-report"
    assert tx.state == {"run_id": "run-1"}
    assert tx.metadata["example"] == "json_event_log_processor"
    assert tx.metadata["run_id"] == "run-1"
    assert tx.metadata["transaction_kind"] == "error-report"
    assert tx.metadata["report_attempt"] == 2
    assert len(tx.steps) == 1
    assert isinstance(tx.steps[0], WriteErrorReport)


def test_record_persistence_error_appends_json_safe_details() -> None:
    config = {}
    tx = Transaction(reference="json-file-events_001")

    _record_persistence_error(config, tx, OSError("disk full"))

    assert config["persistence_errors"] == [
        {
            "transaction_reference": "json-file-events_001",
            "transaction_id": tx.id,
            "status": tx.status.name,
            "exception_type": "OSError",
            "message": "disk full",
        },
    ]


def test_save_transaction_safely_records_failure(tmp_path: Path) -> None:
    config = {}
    tx = Transaction(reference="json-file-events_001")

    with patch("main.save_transaction", side_effect=OSError("locked")):
        assert _save_transaction_safely(tx, str(tmp_path / "rpacore.db"), config) is False

    assert config["persistence_errors"][0]["transaction_reference"] == "json-file-events_001"
    assert config["persistence_errors"][0]["message"] == "locked"


def test_run_error_report_refreshes_after_persistence_failure(tmp_path: Path) -> None:
    config = {
        "transaction_db_path": str(tmp_path / "rpacore.db"),
        "results_dir": str(tmp_path / "results"),
        "run_id": "run-1",
    }
    engine = Engine(max_retries=0)
    calls = []

    def fake_save_transaction(tx, db_path):
        calls.append(tx.metadata["report_attempt"])
        if tx.metadata["report_attempt"] == 1:
            raise OSError("report persistence failed")

    with patch("main.save_transaction", side_effect=fake_save_transaction):
        published = _run_error_report(engine, config, config["transaction_db_path"], "run-1")

    assert published is False
    assert calls == [1]
    assert config["persistence_errors"][0]["transaction_reference"] == "error-report"

    report = (tmp_path / "results" / "error_report.json").read_text(encoding="utf-8")
    assert "report persistence failed" in report


def test_run_error_report_pins_the_refreshed_attempt_as_unsaved(tmp_path: Path) -> None:
    """A report whose own save keeps failing is refreshed but never persisted.

    The refreshed report is what audits the missing record; the refresh itself
    has no durable record because the database is what is failing.
    """
    config = {
        "transaction_db_path": str(tmp_path / "rpacore.db"),
        "results_dir": str(tmp_path / "results"),
        "run_id": "run-1",
    }
    attempts = []

    def always_failing_save(tx, db_path):
        attempts.append(tx.metadata["report_attempt"])
        raise OSError("report persistence failed")

    with patch("main.save_transaction", side_effect=always_failing_save):
        assert _run_error_report(
            Engine(max_retries=0), config, config["transaction_db_path"], "run-1",
        ) is False

    # Only the first attempt is offered to the database; the refresh rewrites
    # the report file and is deliberately not saved.
    assert attempts == [1]
    assert len(config["persistence_errors"]) == 1
    assert list_transactions(config["transaction_db_path"]) == []

    report = json.loads(
        (tmp_path / "results" / "error_report.json").read_text(encoding="utf-8")
    )
    assert report["persistence_error_count"] == 1
    assert report["persistence_errors"][0]["message"] == "report persistence failed"


def test_run_error_report_reports_success_when_published_and_persisted(tmp_path: Path) -> None:
    config = {
        "transaction_db_path": str(tmp_path / "rpacore.db"),
        "results_dir": str(tmp_path / "results"),
        "run_id": "run-1",
    }

    assert _run_error_report(Engine(max_retries=0), config, config["transaction_db_path"], "run-1") is True
    assert (tmp_path / "results" / "error_report.json").exists()
    assert config.get("persistence_errors") is None


def test_run_error_report_reports_failure_when_publication_fails(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    (results_dir / "error_report.json").write_text("previous", encoding="utf-8")
    db_path = str(tmp_path / "rpacore.db")
    config = {
        "transaction_db_path": db_path,
        "results_dir": str(results_dir),
        "run_id": "run-1",
    }

    with patch("rpacore.paths.os.replace", side_effect=OSError("publication failed")):
        published = _run_error_report(Engine(max_retries=0), config, db_path, "run-1")

    assert published is False
    assert (results_dir / "error_report.json").read_text(encoding="utf-8") == "previous"
    assert list(results_dir.glob(".*.tmp")) == []
    assert "persistence_errors" not in config

    # The missing report is still auditable as a failed transaction.
    report_transactions = [
        tx for tx in list_transactions(db_path) if tx.reference == "error-report"
    ]
    assert len(report_transactions) == 1
    assert report_transactions[0].status == Status.FAILED
    assert report_transactions[0].metadata["report_attempt"] == 1
