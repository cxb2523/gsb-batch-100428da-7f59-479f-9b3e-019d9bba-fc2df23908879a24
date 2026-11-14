from __future__ import annotations

"""Pytest fixtures for JSON Event Log Processor tests."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import rpacore

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_CONFIG = {
    "max_retries": 2,
    "log_level": "WARNING",
    "transaction_db_path": "rpacore.db",
    "inbox_dir": "inbox",
    "results_dir": "results",
}

# A hung child would otherwise hang the whole suite with no diagnostic.
SUBPROCESS_TIMEOUT_SECONDS = 300

# The subprocess must import the same rpacore build the tests are running
# against, so its package root goes on PYTHONPATH explicitly.
RPACORE_PARENT = Path(rpacore.__file__).resolve().parent.parent


@pytest.fixture
def run_batch(tmp_path: Path):
    """Run the real ``__main__`` entrypoint in a disposable project copy.

    Returns ``(project, completed, report)`` so tests can assert the process
    exit code alongside the durable SQLite rows, JSONL bytes, and report
    contents.  ``report`` is ``None`` when no report was published.  Passing
    ``files=None`` reuses the existing project and inbox, which lets a test
    stage a producer handoff across two runs.
    """

    def _run(files: dict[str, str] | None = None, **config_overrides):
        project = tmp_path / "project"
        inbox = project / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        if files is not None:
            for name, content in files.items():
                (inbox / name).write_text(content, encoding="utf-8")
            shutil.copy2(PROJECT_ROOT / "main.py", project / "main.py")
            shutil.copytree(
                PROJECT_ROOT / "steps",
                project / "steps",
                ignore=shutil.ignore_patterns("__pycache__"),
                dirs_exist_ok=True,
            )

        config = {**DEFAULT_CONFIG, **config_overrides}
        (project / "config.toml").write_text(
            "".join(f"{key} = {json.dumps(value)}\n" for key, value in config.items()),
            encoding="utf-8",
        )

        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            filter(None, [str(RPACORE_PARENT), env.get("PYTHONPATH", "")])
        )
        completed = subprocess.run(
            [sys.executable, "main.py"],
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
        report_path = project / "results" / "error_report.json"
        report = (
            json.loads(report_path.read_text(encoding="utf-8"))
            if report_path.is_file()
            else None
        )
        return project, completed, report

    return _run
