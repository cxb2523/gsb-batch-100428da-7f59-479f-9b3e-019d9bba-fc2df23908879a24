"""Per-competitor collection outcome bookkeeping.

The CocoIndex flow runs one search source per competitor.  A search failure
must not abort the whole update, but the failure has to stay visible to the
caller: ``run_cocoindex_update`` only sees the child process'
``returncode``/``stdout``/``stderr``.  This module defines the small file
contract both sides use:

* the child process (the flow's source connector) writes one record per
  competitor into the run directory named by ``COMPETITOR_OUTCOME_DIR``;
* the parent process reads that file back after the child exits and merges the
  records into its return value.

The agreed file name is ``outcomes.json``.  It holds a ``competitors`` list so
the parent can read the whole round with a single read.  Writes are serialized
with a lock file and committed with an atomic replace, because source
connectors for different competitors may run in different processes.

Every competitor ends up with exactly one of three collection states:

``collected``  the search succeeded and yielded at least one usable result
``empty``      the search succeeded but returned nothing usable (no news)
``failed``     the search raised; ``failure_reason`` holds a short summary

``not_run`` is used by the parent for a configured competitor that has no
record at all (for example the update aborted before that source ran).  It is
deliberately distinct from ``failed`` so callers never mistake "never tried"
for "collected".

A competitor whose request failed yields no source rows.  CocoIndex treats a
source's row set as authoritative, so that competitor's previously indexed
articles are dropped for this round while every other competitor is unaffected.
The ``failed`` status plus ``failure_reason`` is what tells the analyst this was
a transient request problem, not a real "no longer covered" signal.

This module intentionally has no third-party imports so the MCP server can
read outcomes even in environments where cocoindex is not installed.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

ENV_OUTCOME_DIR = "COMPETITOR_OUTCOME_DIR"
DEFAULT_OUTCOME_DIR = Path(__file__).resolve().parent / ".cocoindex" / "collection_outcomes"
DEFAULT_RUNS_DIR = DEFAULT_OUTCOME_DIR / "runs"
AGREED_OUTCOME_FILE = "outcomes.json"
SCHEMA_VERSION = 1
MAX_REASON_LENGTH = 500
LOCK_TIMEOUT_SECONDS = 30.0

STATUS_COLLECTED = "collected"
STATUS_EMPTY = "empty"
STATUS_FAILED = "failed"
STATUS_NOT_RUN = "not_run"

VALID_STATUSES = (STATUS_COLLECTED, STATUS_EMPTY, STATUS_FAILED, STATUS_NOT_RUN)

_STATUS_ALIASES = {
    "collected": STATUS_COLLECTED,
    "ok": STATUS_COLLECTED,
    "success": STATUS_COLLECTED,
    "succeeded": STATUS_COLLECTED,
    "empty": STATUS_EMPTY,
    "no_results": STATUS_EMPTY,
    "no_result": STATUS_EMPTY,
    "none": STATUS_EMPTY,
    "failed": STATUS_FAILED,
    "failure": STATUS_FAILED,
    "error": STATUS_FAILED,
    "errored": STATUS_FAILED,
    "exception": STATUS_FAILED,
    "not_run": STATUS_NOT_RUN,
    "skipped": STATUS_NOT_RUN,
    "not_attempted": STATUS_NOT_RUN,
}

_NOT_RUN_REASON = "no per-competitor outcome was recorded for this run"


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def resolve_outcome_dir(configured: str | Path | None = None) -> Path:
    """Return the run directory outcomes are written to, creating it if needed."""
    raw = configured or os.environ.get(ENV_OUTCOME_DIR) or DEFAULT_OUTCOME_DIR
    directory = Path(raw)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def new_run_dir(base: str | Path | None = None, run_id: str | None = None) -> Path:
    """Create a fresh, inspectable directory for one collection run.

    The run id is timestamp based plus the pid, so sequential runs never reuse
    a directory and stale records can never be attributed to a new run.  If the
    preferred location is not writable the system temp directory is used, since
    losing the audit trail is worse than an unusual location.
    """
    root = Path(base) if base else DEFAULT_RUNS_DIR
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    directory = root / (run_id or f"{stamp}-{os.getpid()}")
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        import tempfile

        directory = Path(tempfile.mkdtemp(prefix="ci-collection-outcomes-"))
    return directory


def outcome_file(directory: str | Path | None = None) -> Path:
    """Path of the agreed per-run result file.

    ``directory`` wins, then ``COMPETITOR_OUTCOME_DIR`` (set by the MCP parent),
    then a run directory created once per process.  That last case covers a
    direct ``cocoindex update main -f`` invocation, where nobody sets the env
    var: without it every run would share one rolling file and an old
    ``collected`` record could be read as this round's result.
    """
    if directory is None and not os.environ.get(ENV_OUTCOME_DIR):
        return _process_run_dir() / AGREED_OUTCOME_FILE
    return resolve_outcome_dir(directory) / AGREED_OUTCOME_FILE


_PROCESS_RUN_DIR: Path | None = None


def _process_run_dir() -> Path:
    """Return this process' run directory, creating it on first use."""
    global _PROCESS_RUN_DIR
    if _PROCESS_RUN_DIR is None or not _PROCESS_RUN_DIR.is_dir():
        _PROCESS_RUN_DIR = new_run_dir()
    return _PROCESS_RUN_DIR


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def summarize_reason(exc: BaseException | str | None, limit: int = MAX_REASON_LENGTH) -> str | None:
    """Build a short, single-line failure summary from an exception or string."""
    if exc is None:
        return None
    if isinstance(exc, BaseException):
        message = str(exc).strip()
        text = f"{type(exc).__name__}: {message}" if message else type(exc).__name__
    else:
        text = str(exc).strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return None
    if len(text) > limit:
        text = text[: limit - 3].rstrip() + "..."
    return text


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_status(value: Any) -> str:
    if value is None:
        return STATUS_FAILED
    key = str(value).strip().lower()
    return _STATUS_ALIASES.get(key, key)


def normalize_outcome(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize a loosely-shaped record from disk into the canonical schema."""
    competitor = raw.get("competitor", raw.get("name", ""))
    competitor = str(competitor).strip() if competitor is not None else ""

    reason = raw.get("failure_reason")
    if reason is None:
        for alias in ("error", "reason", "message", "detail"):
            if raw.get(alias):
                reason = raw.get(alias)
                break

    error_type = raw.get("error_type")
    if error_type is None and raw.get("error"):
        reason_text = str(raw.get("error"))
        if ":" in reason_text:
            error_type = reason_text.split(":", 1)[0].strip() or None

    missing_dates = raw.get("missing_published_date_count")
    if missing_dates is None:
        missing_dates = raw.get("missing_dates", raw.get("missing_date_count", 0))

    result_count = raw.get("result_count")
    if result_count is None:
        result_count = raw.get("results", raw.get("count", 0))
    if isinstance(result_count, list):
        result_count = len(result_count)

    return {
        "schema_version": _coerce_int(raw.get("schema_version"), SCHEMA_VERSION),
        "competitor": competitor,
        "status": _coerce_status(raw.get("status", raw.get("outcome", raw.get("result")))),
        "failure_reason": summarize_reason(reason) if reason else None,
        "error_type": str(error_type) if error_type else None,
        "result_count": _coerce_int(result_count),
        "missing_published_date_count": _coerce_int(missing_dates),
        "phase": str(raw.get("phase") or "list"),
        "query": raw.get("query"),
        "value_error_count": _coerce_int(raw.get("value_error_count")),
        "value_failure_reason": summarize_reason(raw.get("value_failure_reason")),
        "recorded_at": raw.get("recorded_at"),
    }


def _merge(existing: Mapping[str, Any], new: Mapping[str, Any]) -> dict[str, Any]:
    """Merge a fresh outcome into an already recorded one.

    A failed ``get_value`` lookup must not erase the fact that ``list()``
    collected articles, so it only bumps a counter on an already-collected
    record.  A failed listing always wins: it describes the request itself.
    """
    existing = normalize_outcome(existing)
    new = normalize_outcome(new)
    merged = dict(existing)
    merged.update(
        {
            "schema_version": new["schema_version"],
            "competitor": new["competitor"],
            "recorded_at": new["recorded_at"],
        }
    )
    if new["phase"] == "get_value" and existing["status"] == STATUS_COLLECTED:
        merged["phase"] = new["phase"]
        merged["value_error_count"] = existing.get("value_error_count", 0) + 1
        # Keep a per-article lookup failure separate from the competitor-level
        # outcome: the listing still succeeded and its status stays collected.
        if new.get("failure_reason"):
            merged["value_failure_reason"] = new["failure_reason"]
        return normalize_outcome(merged)

    merged.update(new)
    if new["phase"] == "get_value":
        # Keep what the listing already learned about the competitor.
        merged["result_count"] = max(new["result_count"], existing["result_count"])
        merged["missing_published_date_count"] = max(
            new["missing_published_date_count"],
            existing["missing_published_date_count"],
        )
        merged["value_error_count"] = existing.get("value_error_count", 0) + 1
        if new.get("failure_reason"):
            merged["value_failure_reason"] = new["failure_reason"]
    return normalize_outcome(merged)


# ---------------------------------------------------------------------------
# Locked read-modify-write of the agreed file
# ---------------------------------------------------------------------------

class _FileLock:
    """Portable, bounded exclusive lock based on atomic directory creation."""

    def __init__(self, target: Path, timeout: float = LOCK_TIMEOUT_SECONDS):
        self._lock_path = target.with_name(f".{target.name}.lock")
        self._timeout = timeout

    def __enter__(self) -> "_FileLock":
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                self._lock_path.mkdir()
                return self
            except FileExistsError:
                if time.monotonic() >= deadline:
                    # A crashed writer must not wedge all later updates.
                    try:
                        self._lock_path.rmdir()
                    except OSError:
                        pass
                    deadline = time.monotonic() + self._timeout
                time.sleep(0.01)
            except OSError:
                # Locking is best effort; proceed and rely on atomic replace.
                return self

    def __exit__(self, *exc_info: Any) -> None:
        try:
            self._lock_path.rmdir()
        except OSError:
            pass


def _load_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, Mapping) else {}


def _records_from_payload(payload: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if isinstance(payload, Mapping):
        bucket = payload.get("competitors")
        if isinstance(bucket, list):
            items: Iterable[Any] = bucket
        else:
            items = [payload]
        for item in items:
            if isinstance(item, Mapping):
                records.append(normalize_outcome(item))
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, Mapping):
                records.append(normalize_outcome(item))
    return [record for record in records if record["competitor"]]


def _atomic_write_json(path: Path, payload: Any) -> None:
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_path, path)


def write_outcome(
    competitor: str,
    status: str,
    *,
    failure_reason: Any = None,
    error_type: str | None = None,
    result_count: int = 0,
    missing_published_date_count: int = 0,
    phase: str = "list",
    query: str | None = None,
    recorded_at: str | None = None,
    directory: str | Path | None = None,
) -> Path:
    """Write (or merge) one competitor's record into the agreed result file."""
    stamp = recorded_at or datetime.now(timezone.utc).isoformat()
    record = normalize_outcome(
        {
            "schema_version": SCHEMA_VERSION,
            "competitor": competitor,
            "status": status,
            "failure_reason": failure_reason,
            "error_type": error_type,
            "result_count": result_count,
            "missing_published_date_count": missing_published_date_count,
            "phase": phase,
            "query": query,
            "recorded_at": stamp,
        }
    )

    path = outcome_file(directory)
    with _FileLock(path):
        existing = {
            item["competitor"]: item for item in _records_from_payload(_load_payload(path))
        }
        previous = existing.get(record["competitor"])
        if previous is not None:
            record = _merge(previous, record)
        existing[record["competitor"]] = record
        payload = {
            "schema_version": SCHEMA_VERSION,
            "run": path.parent.name,
            "updated_at": stamp,
            "competitors": list(existing.values()),
        }
        _atomic_write_json(path, payload)
    return path


def write_summary(outcomes: Iterable[Mapping[str, Any]], directory: str | Path | None = None) -> Path:
    """Persist the final ordered outcome list (including ``not_run``) for review.

    The run directory is created if missing so a run that produced nothing at
    all still leaves an inspectable file behind.
    """
    ordered = [normalize_outcome(outcome) for outcome in outcomes]
    path = outcome_file(directory)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "run": path.parent.name,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summarize_outcomes(ordered),
        "competitors": ordered,
    }
    with _FileLock(path):
        _atomic_write_json(path, payload)
    return path


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def read_outcomes(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Read every outcome record from the agreed file, a directory, or JSONL.

    ``path`` should normally be an explicit run directory (the MCP parent always
    passes one).  When omitted, ``COMPETITOR_OUTCOME_DIR`` from the environment
    is used; if that is unset this reads the same per-process run file that
    ``write_outcome()`` would write.  Later records for a competitor replace
    earlier ones.
    """
    if path is None:
        raw = os.environ.get(ENV_OUTCOME_DIR)
        target = Path(raw) / AGREED_OUTCOME_FILE if raw else outcome_file()
    else:
        target = Path(path)

    if target.is_dir():
        files = sorted(
            p
            for p in target.iterdir()
            if p.is_file() and p.suffix in (".json", ".jsonl")
        )
    elif target.is_file():
        files = [target]
    else:
        return []

    records: list[dict[str, Any]] = []
    for file_path in files:
        try:
            text = file_path.read_text(encoding="utf-8")
        except OSError:
            continue
        if file_path.suffix == ".jsonl":
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    records.extend(_records_from_payload(json.loads(line)))
                except ValueError:
                    continue
        else:
            try:
                records.extend(_records_from_payload(json.loads(text)))
            except ValueError:
                continue

    by_competitor: dict[str, dict[str, Any]] = {}
    for record in records:
        by_competitor[record["competitor"]] = record
    return list(by_competitor.values())


def merge_with_expected(
    recorded: Iterable[Mapping[str, Any]],
    competitors: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Order records by the configured competitor list and fill gaps.

    A configured competitor with no record becomes ``not_run`` rather than
    being silently dropped, so a truncated run cannot look complete.
    """
    normalized = [normalize_outcome(record) for record in recorded]
    by_competitor: dict[str, dict[str, Any]] = {
        record["competitor"]: record for record in normalized if record["competitor"]
    }

    expected = [str(name).strip() for name in (competitors or []) if str(name).strip()]
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name in expected:
        record = by_competitor.get(name)
        if record is None:
            record = normalize_outcome(
                {
                    "competitor": name,
                    "status": STATUS_NOT_RUN,
                    "failure_reason": _NOT_RUN_REASON,
                    "phase": "not_run",
                }
            )
        ordered.append(record)
        seen.add(name)
    for record in normalized:
        if record["competitor"] and record["competitor"] not in seen:
            ordered.append(record)
            seen.add(record["competitor"])
    return ordered


def summarize_outcomes(outcomes: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Count outcomes per status plus the names that failed."""
    counts = {status: 0 for status in VALID_STATUSES}
    failed: list[str] = []
    total = 0
    for outcome in outcomes:
        total += 1
        status = _coerce_status(outcome.get("status"))
        counts[status] = counts.get(status, 0) + 1
        if status == STATUS_FAILED and outcome.get("competitor"):
            failed.append(str(outcome["competitor"]))
    return {"total": total, **counts, "failed_competitors": failed}
