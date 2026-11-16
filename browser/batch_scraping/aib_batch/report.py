"""Settlement report rendering: human-readable text and JSON export."""

from __future__ import annotations

import json
from typing import Any, Dict


def format_report(report: Dict[str, Any]) -> str:
    """Render a batch settlement report as a reviewable plain-text block."""
    lines = []
    summary = report["summary"]
    lines.append("=" * 68)
    lines.append("Batch settlement report")
    lines.append("=" * 68)
    lines.append(
        f"total={report['total']}  success={summary['success']}  "
        f"failed={summary['failed']}  skipped={summary['skipped']}"
    )
    lines.append("-" * 68)

    for record in report["steps"]:
        head = (
            f"[{record['seq']:>2}] {record['op']:<14} "
            f"{record['status'].upper():<7}"
        )
        if record.get("name"):
            head += f" name={record['name']}"
        if record.get("retried"):
            head += "  (re-find + retry)"
        lines.append(head)

        if record["status"] == "failed":
            lines.append(f"      reason: {record.get('reason')}")
            lines.append(f"      error : {record.get('error')}")
        elif record["status"] == "skipped":
            lines.append(
                f"      reason: skipped due to failed/skipped deps "
                f"{record.get('skipped_due_to')}"
            )
        elif record.get("attempts"):
            lines.append(f"      attempts: {record['attempts']}  "
                         f"duration: {record.get('duration_ms', 0)} ms")

    lines.append("-" * 68)
    if report["failure_reasons"]:
        lines.append("Failure reason summary:")
        for bucket in report["failure_reasons"]:
            lines.append(
                f"  - {bucket['reason']:<20} x{bucket['count']} "
                f"at steps {bucket['seqs']}"
            )
            lines.append(f"      sample: {bucket['sample_error']}")
    else:
        lines.append("Failure reason summary: none")

    lines.append("-" * 68)
    lines.append("Collected data (retained even when later steps fail):")
    if report["results"]:
        for key, value in report["results"].items():
            rendered = _preview(value)
            lines.append(f"  {key} = {rendered}")
    else:
        lines.append("  (nothing collected)")
    lines.append("=" * 68)
    return "\n".join(lines)


def _preview(value: Any, limit: int = 120) -> str:
    if isinstance(value, str):
        text = value.replace("\n", "\\n")
    else:
        text = json.dumps(value, ensure_ascii=False)
    if len(text) > limit:
        text = text[:limit] + f"... (+{len(text) - limit} chars)"
    return text


def write_json_report(report: Dict[str, Any], path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
