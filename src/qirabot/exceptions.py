"""Exceptions for Qirabot SDK."""

from __future__ import annotations


class QirabotError(Exception):
    """Base exception for all Qirabot SDK errors."""

    def __init__(
        self,
        message: str,
        code: str | None = None,
        status_code: int | None = None,
    ):
        self.message = message
        self.code = code
        self.status_code = status_code
        super().__init__(message)

    def __str__(self) -> str:
        if self.code:
            return f"[{self.code}] {self.message}"
        return self.message


class AuthenticationError(QirabotError):
    """Model-provider credentials are missing, unusable, or ambiguous."""


class ActionError(QirabotError):
    """AI action failed."""


class NoOpenPageError(ActionError):
    """No open page remains in the browser context to operate on.

    Raised by the Playwright adapter when the current tab was closed (by the
    page itself, a script, or an action) and the context has no other open
    page to fall back to. Continuing would mean driving a closed page, which
    only fails later with a far less clear error.
    """


class QirabotTimeoutError(QirabotError):
    """Operation timed out (client-side)."""


class MissingDependencyError(QirabotError, ImportError):
    """An optional backend dependency (e.g. playwright, pyautogui) is not installed.

    Raised by :func:`qirabot._optional.require` with an actionable ``python -m pip
    install "qirabot[<extra>]"`` hint instead of a bare ``ModuleNotFoundError``
    traceback.
    """


# A credential problem is a verdict, not a transient failure — every retry
# would hit the same gate.
_NON_RETRYABLE = (AuthenticationError,)


def _is_retryable(error: QirabotError) -> bool:
    """Return True if the error is worth retrying."""
    if isinstance(error, _NON_RETRYABLE):
        return False
    if error.status_code and error.status_code < 500 and error.status_code not in (408, 429):
        return False
    return True
