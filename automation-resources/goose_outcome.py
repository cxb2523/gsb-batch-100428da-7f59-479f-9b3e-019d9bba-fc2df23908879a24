"""Shared Goose agent outcome classification and retry orchestration.

Both collection paths (the local Ollama-backed Goose agent and the Goose agent
running inside a Docker container) feed their raw subprocess results through
the functions in this module so that success/failure rules cannot drift
between the two paths.

Every agent call ends in one of three verifiable outcomes:

* ``SUCCESS``          - clean output, kept and reported exactly as before.
* ``KNOWN_ERROR``      - the (possibly empty) output contains one of the
                         known Goose error signatures (rate limiting,
                         truncation, missing tools, ...). The raw output is
                         preserved together with the reason.
* ``RETRY_EXHAUSTED``  - the retry budget (or a call timeout) was consumed
                         without ever producing clean output and no known
                         error signature was found. The raw output is
                         preserved together with the reason.
"""

import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List, Optional

# Outcome status constants, shared by the local and the Docker collection paths.
SUCCESS = "SUCCESS"
KNOWN_ERROR = "KNOWN_ERROR"
RETRY_EXHAUSTED = "RETRY_EXHAUSTED"

# Known Goose error signatures.
#
# This is the single source of truth for both collection paths. It is the
# union of the markers previously asserted separately in the local and Docker
# Robot Framework keywords, so neither path can silently accept output the
# other one rejects.
KNOWN_ERROR_SIGNATURES = (
    # Rate limiting / transient provider failures.
    "Rate limit hit",
    "Please retry if you think this is a transient or recoverable error.",
    # Output truncation failures.
    "crates/goose/src/agents/truncate.rs",
    "goose::agents::truncate:",
    # Provider / request failures.
    "UNAVAILABLE",
    "ERROR",
    "Request failed",
    "400 Bad Request",
    "Server error",
    "Ran into this error",
    # Model / RPC capability failures.
    "I do not have the ability",
    "RPC",
    "GenerateContentRequest",
    # Missing / misconfigured tools and extensions.
    "extension_name:",
    "uri:",
    "Tool not found",
    "platform",
    "search_available_extensions",
    "remember_memory",
    # Garbled CJK output emitted by some local models.
    "。",
    "、",
    "大",
    "澳",
    "中",
    "一",
    "·",
    "文",
)

_ANSI_ESCAPE = re.compile(r"\x1B\[[0-9;]*[mK]")


@dataclass
class AttemptResult:
    """The raw result of a single Goose subprocess invocation."""

    stdout: str = ""
    stderr: str = ""
    returncode: Optional[int] = None
    timed_out: bool = False
    error: Optional[str] = None

    def combined_output(self) -> str:
        """stdout and stderr joined for marker inspection."""
        return "{}\n{}".format(self.stdout or "", self.stderr or "")


@dataclass
class AgentOutcome:
    """The verifiable final result of a retried Goose agent call."""

    status: str
    reason: str
    output: str
    stderr: str = ""
    attempts: List[AttemptResult] = field(default_factory=list)
    markers: List[str] = field(default_factory=list)
    report_stem: str = ""
    route: str = ""
    started_at: str = ""
    finished_at: str = ""
    retries_configured: int = 0
    call_timeout: Optional[float] = None

    @property
    def succeeded(self) -> bool:
        return self.status == SUCCESS


def detect_known_errors(output: Optional[str]) -> List[str]:
    """Return every known error signature present in ``output``.

    Matching is case-sensitive and deliberately identical for the local and
    Docker collection paths.
    """
    if not output:
        return []
    return [signature for signature in KNOWN_ERROR_SIGNATURES if signature in output]


def classify_attempt(attempt: AttemptResult) -> str:
    """Classify a single attempt as SUCCESS, KNOWN_ERROR or RETRY_EXHAUSTED.

    The RETRY_EXHAUSTED status at attempt level means "this attempt is not a
    success and carries no known error signature" (timeout, non-zero exit,
    missing binary, empty output, ...); the surrounding retry loop decides
    whether another attempt is worth making.
    """
    markers = detect_known_errors(attempt.combined_output())
    if markers:
        return KNOWN_ERROR
    if attempt.timed_out or attempt.error is not None or attempt.returncode not in (0, None):
        return RETRY_EXHAUSTED
    if not (attempt.stdout or "").strip():
        # An empty response is not a verifiable success: keep retrying and, if
        # the budget is exhausted, report it as such rather than as success.
        return RETRY_EXHAUSTED
    return SUCCESS


def run_agent_with_retry(
    runner: Callable[[], AttemptResult],
    retries: int,
    retry_interval: float,
    route: str = "",
    report_stem: str = "",
    call_timeout: Optional[float] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> AgentOutcome:
    """Invoke ``runner`` until it succeeds or the retry budget is exhausted.

    ``retries`` is the maximum number of attempts (matching the existing
    ``${RETRY}`` configuration) and ``retry_interval`` the delay in seconds
    between attempts (matching the existing ``${DELAY}`` configuration).

    The function never raises: every terminal state is returned as an
    :class:`AgentOutcome` whose raw output can be persisted.
    """
    attempts: List[AttemptResult] = []
    started_at = datetime.now().isoformat(timespec="seconds")
    max_attempts = max(1, int(retries))
    last_markers: List[str] = []

    for attempt_number in range(1, max_attempts + 1):
        try:
            attempt = runner()
        except Exception as exc:  # defensive: runners normally catch this themselves
            attempt = AttemptResult(error=repr(exc))
        if not isinstance(attempt, AttemptResult):
            attempt = AttemptResult(error="runner returned {}".format(type(attempt).__name__))
        attempts.append(attempt)

        markers = detect_known_errors(attempt.combined_output())
        if markers:
            last_markers = markers

        attempt_status = classify_attempt(attempt)
        if attempt_status == SUCCESS:
            return AgentOutcome(
                status=SUCCESS,
                reason="Clean Goose output received on attempt {} of {}".format(
                    attempt_number, max_attempts
                ),
                output=attempt.stdout,
                stderr=attempt.stderr,
                attempts=attempts,
                markers=[],
                report_stem=report_stem,
                route=route,
                started_at=started_at,
                finished_at=datetime.now().isoformat(timespec="seconds"),
                retries_configured=max_attempts,
                call_timeout=call_timeout,
            )

        if attempt_number < max_attempts and retry_interval > 0:
            sleep_fn(retry_interval)

    last_attempt = attempts[-1]
    final_markers = detect_known_errors(last_attempt.combined_output()) or last_markers
    if final_markers:
        status = KNOWN_ERROR
        reason = (
            "Known Goose error signature(s) found in output after {n} attempt(s): {markers}"
        ).format(n=len(attempts), markers=", ".join(final_markers))
    else:
        status = RETRY_EXHAUSTED
        reason = _retry_exhausted_reason(attempts, call_timeout)

    return AgentOutcome(
        status=status,
        reason=reason,
        output=last_attempt.stdout,
        stderr=last_attempt.stderr,
        attempts=attempts,
        markers=final_markers,
        report_stem=report_stem,
        route=route,
        started_at=started_at,
        finished_at=datetime.now().isoformat(timespec="seconds"),
        retries_configured=max_attempts,
        call_timeout=call_timeout,
    )


def _retry_exhausted_reason(attempts: List[AttemptResult], call_timeout: Optional[float]) -> str:
    last_attempt = attempts[-1]
    if last_attempt.timed_out:
        return (
            "Retry budget exhausted after {n} attempt(s): the last Goose call timed out "
            "after {timeout} seconds without completing"
        ).format(n=len(attempts), timeout=call_timeout)
    if last_attempt.error is not None:
        return (
            "Retry budget exhausted after {n} attempt(s): the Goose process could not be "
            "run: {error}"
        ).format(n=len(attempts), error=last_attempt.error)
    if last_attempt.returncode not in (0, None):
        return (
            "Retry budget exhausted after {n} attempt(s): the Goose process exited with "
            "return code {code} and no known error signature"
        ).format(n=len(attempts), code=last_attempt.returncode)
    return (
        "Retry budget exhausted after {n} attempt(s): no usable Goose output was produced "
        "and no known error signature was detected"
    ).format(n=len(attempts))


def outcome_to_dict(outcome: AgentOutcome) -> dict:
    """Serialise an outcome (including every attempt) for Robot Framework."""
    return {
        "status": outcome.status,
        "reason": outcome.reason,
        "output": outcome.output,
        "stderr": outcome.stderr,
        "markers": list(outcome.markers),
        "attempts": [
            {
                "attempt_number": index,
                "returncode": attempt.returncode,
                "timed_out": attempt.timed_out,
                "error": attempt.error,
                "markers": detect_known_errors(attempt.combined_output()),
                "stdout": attempt.stdout,
                "stderr": attempt.stderr,
            }
            for index, attempt in enumerate(outcome.attempts, start=1)
        ],
        "attempt_count": len(outcome.attempts),
        "report_stem": outcome.report_stem,
        "route": outcome.route,
        "started_at": outcome.started_at,
        "finished_at": outcome.finished_at,
        "retries_configured": outcome.retries_configured,
        "call_timeout": outcome.call_timeout,
    }


def strip_ansi(text: Optional[str]) -> str:
    """Remove ANSI colour/control sequences, mirroring the previous sed filter."""
    if not text:
        return ""
    return _ANSI_ESCAPE.sub("", text)


def build_non_clobbering_path(directory: str, filename: str) -> str:
    """Return a path inside ``directory`` that never overwrites an existing file.

    If ``filename`` already exists a ``-1``, ``-2`` ... counter is inserted
    before the extension.
    """
    os.makedirs(directory, exist_ok=True)
    candidate = os.path.join(directory, filename)
    if not os.path.exists(candidate):
        return candidate
    base, extension = os.path.splitext(filename)
    counter = 1
    while True:
        candidate = os.path.join(directory, "{}-{}{}".format(base, counter, extension))
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def write_local_agent_report_file(
    output_dir: str, report_number, timestamp: str, content: str
) -> str:
    """Persist a successful local-agent report using the existing name rule.

    The report is named ``local-agent-report<N>-<timestamp>.md`` and existing
    reports are never overwritten. Returns the path that was written.
    """
    filename = "local-agent-report{}-{}.md".format(report_number, timestamp)
    path = build_non_clobbering_path(output_dir, filename)
    with open(path, "w", encoding="utf-8", newline="") as report_file:
        report_file.write(strip_ansi(content))
    return path


def render_outcome_markdown(outcome: AgentOutcome) -> str:
    """Render a human-auditable Markdown record of a failed outcome."""
    lines = [
        "# Goose agent outcome record - {}".format(outcome.report_stem or outcome.route),
        "",
        "- Route: `{}`".format(outcome.route or "unknown"),
        "- Status: `{}`".format(outcome.status),
        "- Reason: {}".format(outcome.reason),
        "- Report stem: `{}`".format(outcome.report_stem),
        "- Attempts: {} (retry budget: {})".format(
            len(outcome.attempts), outcome.retries_configured
        ),
        "- Per-call timeout: {} seconds".format(outcome.call_timeout),
        "- Started at: {}".format(outcome.started_at),
        "- Finished at: {}".format(outcome.finished_at),
        "- Known error signatures: {}".format(
            ", ".join(outcome.markers) if outcome.markers else "none"
        ),
        "",
        "## Attempt history",
        "",
        "| Attempt | Return code | Timed out | Status | Markers |",
        "| --- | --- | --- | --- | --- |",
    ]
    for index, attempt in enumerate(outcome.attempts, start=1):
        markers = detect_known_errors(attempt.combined_output())
        lines.append(
            "| {} | {} | {} | {} | {} |".format(
                index,
                attempt.returncode if attempt.returncode is not None else "n/a",
                "yes" if attempt.timed_out else "no",
                classify_attempt(attempt),
                ", ".join(markers) if markers else "none",
            )
        )

    lines.extend(["", "## Raw Goose output (stdout, may be empty)", ""])
    lines.append(outcome.output if outcome.output else "(empty)")
    lines.extend(["", "## Raw Goose error output (stderr, may be empty)", ""])
    lines.append(outcome.stderr if outcome.stderr else "(empty)")
    if any(attempt.error for attempt in outcome.attempts):
        lines.extend(["", "## Process errors", ""])
        for index, attempt in enumerate(outcome.attempts, start=1):
            if attempt.error:
                lines.append("- Attempt {}: {}".format(index, attempt.error))
    lines.append("")
    return "\n".join(lines)


def write_outcome_file(
    outcome: AgentOutcome, output_dir: str, report_stem: str, timestamp: str
) -> str:
    """Persist a failed outcome next to the reports without overwriting anything.

    Outcome files follow the report naming rule with an ``-outcome-`` infix,
    e.g. ``local-agent-report1-outcome-<timestamp>.md`` or
    ``docker-agent1-report3-outcome-<timestamp>.md``. Returns the written path.
    """
    filename = "{}-outcome-{}.md".format(report_stem, timestamp)
    path = build_non_clobbering_path(output_dir, filename)
    with open(path, "w", encoding="utf-8", newline="") as outcome_file:
        outcome_file.write(render_outcome_markdown(outcome))
    return path
