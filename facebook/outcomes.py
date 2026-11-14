"""Per-page crawl outcome recording.

Every fan-out page gets one :class:`PageOutcome`, so an operator can
reconstruct what happened to each ``FB_PAGE_ID`` entry after a run:
which page produced posts, which timeline had no story id, where the
session expired, and which page hit an unrelated parse error.

The recorder writes the whole report to JSON after every change, so the
outcomes already gathered survive the spider being stopped (including a
mid-run session expiry).
"""

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from typing import Any

# Outcome statuses.
SUCCESS = "success"
NO_POST_ID = "no_post_id"
SESSION_EXPIRED = "session_expired"
PARSE_ERROR = "parse_error"

DEFAULT_OUTPUT = "fb_outcomes.json"


@dataclass
class PageOutcome:
    """The verifiable ending of one page id in a run."""

    page_id: str
    page_url: str
    status: str
    detail: str = ""
    # Populated on success so an outcome can be traced back to the posts
    # it produced; empty for every other status.
    post_ids: list = field(default_factory=list)
    post_urls: list = field(default_factory=list)

    def as_dict(self) -> dict:
        """Plain-materialized representation for JSON serialization."""
        return asdict(self)


class OutcomeRecorder:
    """Collects page outcomes and persists them as one JSON report."""

    def __init__(self, output_path: str = DEFAULT_OUTPUT) -> None:
        self.output_path = output_path or DEFAULT_OUTPUT
        self.outcomes: list[PageOutcome] = []
        self.stop_reason: str | None = None

    def record(self, outcome: PageOutcome) -> None:
        """Append one page outcome and flush the report to disk."""
        self.outcomes.append(outcome)
        self.write()

    def stop(self, reason: str) -> None:
        """Record why the remaining pages were not requested."""
        self.stop_reason = reason
        self.write()

    def to_dict(self) -> dict[str, Any]:
        """Full report payload written to the JSON file."""
        return {
            "stop_reason": self.stop_reason,
            "outcomes": [outcome.as_dict() for outcome in self.outcomes],
        }

    def write(self) -> None:
        """Atomically write the report so a killed run keeps its data."""
        directory = os.path.dirname(os.path.abspath(self.output_path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=".fb_outcomes.", suffix=".tmp", dir=directory
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(tmp_path, self.output_path)
        except BaseException:
            # Never leave the temp file behind on failure.
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
