"""Pytest fixtures for Database Reconciliation tests."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import rpacore

sys.path.insert(0, str(Path(__file__).parent.parent))

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FAULT_CHILD = Path(__file__).resolve().parent / "fault_child.py"

# A hung child would otherwise hang the whole suite with no diagnostic.
SUBPROCESS_TIMEOUT_SECONDS = 300

# The subprocess must import the same rpacore build the tests run against, so
# the directory holding the ``rpacore`` package goes on PYTHONPATH explicitly.
# That is the repo root for a source checkout and site-packages once installed.
RPACORE_IMPORT_ROOT = Path(rpacore.__file__).resolve().parent.parent

MATCHING_INTERNAL = (
    "payment_id,date,reference,amount,vendor\n"
    "PAY-1,2024-04-01,INV-1,100.00,Vendor A\n"
)
MATCHING_BANK = (
    "posted_date,reference,amount,description\n"
    "2024-04-01,INV-1,100.00,ACH Vendor A\n"
)
MISMATCHED_BANK = (
    "posted_date,reference,amount,description\n"
    "2024-04-01,INV-1,125.00,ACH Vendor A\n"
)
PRIOR_REPORT = b"payment_id,status\nprior-run,matched\n"


@pytest.fixture
def run_reconciliation(tmp_path):
    """Run the real ``__main__`` entrypoint in a disposable project copy.

    Returns ``(project, completed, report_file)`` so a test can assert the
    process exit code next to the durable SQLite rows and the report bytes.
    ``report_file`` is the path even when nothing was published there, so a
    test can compare the bytes that stayed. The committed ``config.toml`` is
    copied unchanged, so the example ships and runs its own retry setting.
    """

    def _run(*, internal=MATCHING_INTERNAL, bank=MATCHING_BANK, prior_report=None, fault=None):
        project = tmp_path / "project"
        (project / "sample_data").mkdir(parents=True)
        (project / "output").mkdir()
        shutil.copy2(PROJECT_ROOT / "main.py", project / "main.py")
        shutil.copy2(PROJECT_ROOT / "config.toml", project / "config.toml")
        shutil.copytree(
            PROJECT_ROOT / "steps",
            project / "steps",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        (project / "sample_data" / "internal_payments.csv").write_text(
            internal, encoding="utf-8"
        )
        (project / "sample_data" / "bank_statement.csv").write_text(
            bank, encoding="utf-8"
        )

        report_file = project / "output" / "reconciliation_report.csv"
        if prior_report is not None:
            report_file.write_bytes(prior_report)

        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            filter(None, [str(RPACORE_IMPORT_ROOT), env.get("PYTHONPATH", "")])
        )
        # Without a fault the entrypoint runs exactly as documented; a fault
        # needs the child module to install it before main.py starts.
        if fault is None:
            command = [sys.executable, "main.py"]
        else:
            env["RPACORE_FAULT"] = fault
            command = [sys.executable, str(FAULT_CHILD)]
        completed = subprocess.run(
            command,
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
        return project, completed, report_file

    return _run
