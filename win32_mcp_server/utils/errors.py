"""
Error types and structured error responses.
"""

from typing import Any


class ToolError(Exception):
    """Raised when a tool encounters a known, actionable error.

    Attributes:
        suggestion: Optional guidance for the caller on how to fix the issue.
        details: Optional structured payload (e.g. candidate controls) that
            gives the caller machine-readable context for retrying.
    """

    def __init__(
        self,
        message: str,
        suggestion: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.suggestion = suggestion
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        d = {"error": True, "message": str(self)}
        if self.suggestion:
            d["suggestion"] = self.suggestion
        if self.details:
            d["details"] = self.details
        return d
