# SPDX-License-Identifier: MIT

"""Optional diagnostics collection for the scraping pipeline.

The scraping functions (:func:`get_page_source`, :func:`get_processed_text`
and :func:`url_to_llm_text`) historically swallow every failure and return an
empty string, leaving callers unable to tell an empty page apart from a
timeout, a missing Playwright install or a bad proxy.

Passing a :class:`ScrapeDiagnostics` instance through the optional
``diagnostics`` argument makes failures programmatically inspectable without
changing any return value. When the argument is omitted, behaviour is
identical to the old code.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class FailureCategory:
    """Machine-readable failure reason categories used across the pipeline."""

    #: Playwright (or its async API) is not importable in the current environment.
    PLAYWRIGHT_NOT_INSTALLED = "playwright_not_installed"
    #: Navigation to the target URL timed out.
    NAVIGATION_TIMEOUT = "navigation_timeout"
    #: Proxy argument/configuration could not be parsed or was rejected.
    INVALID_PROXY = "invalid_proxy"
    #: Requested HTML parser is unavailable / BeautifulSoup rejected the markup.
    HTML_PARSER_UNAVAILABLE = "html_parser_unavailable"
    #: The fetch stage completed but returned an empty page source.
    EMPTY_PAGE_SOURCE = "empty_page_source"
    #: An unexpected exception happened while processing the HTML into text.
    PROCESSING_ERROR = "processing_error"
    #: Any other fetch-stage failure not covered by the categories above.
    FETCH_ERROR = "fetch_error"


class Stage:
    """Pipeline stage at which a diagnostic record was produced."""

    #: ``get_page_source`` — browser launch / navigation / source extraction.
    FETCH = "fetch"
    #: ``get_processed_text`` — HTML parsing and text extraction.
    PROCESS = "process"
    #: ``url_to_llm_text`` — end-to-end orchestration.
    PIPELINE = "pipeline"


@dataclass
class DiagnosticRecord:
    """A single diagnostic event emitted by one pipeline stage."""

    #: Pipeline stage, one of :class:`Stage` values.
    stage: str
    #: ``True`` when the stage completed successfully, ``False`` on failure.
    success: bool
    #: Failure category (one of :class:`FailureCategory`); ``None`` on success.
    category: Optional[str] = None
    #: Short human-readable message / exception message.
    message: str = ""
    #: Exception class name when the record was created from an exception.
    exception_type: Optional[str] = None
    #: Target URL. May be ``None`` (or empty) for pure processing-stage events.
    url: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage,
            "success": self.success,
            "category": self.category,
            "message": self.message,
            "exception_type": self.exception_type,
            "url": self.url,
        }


class ScrapeDiagnostics:
    """Collector passed via the optional ``diagnostics`` parameter.

    A fresh collector is empty and :attr:`ok` is ``True``. Every failure is
    appended to :attr:`records`; successful stages append a success record
    unless the stage already recorded a failure.

    The class only relies on two public methods — :meth:`record_failure` and
    :meth:`record_success` — so lightweight duck-typed stand-ins can be used
    in tests.
    """

    def __init__(self) -> None:
        self.records: List[DiagnosticRecord] = []

    # ------------------------------------------------------------------ write
    def record_failure(
        self,
        stage: str,
        category: str,
        message: str = "",
        url: Optional[str] = None,
        exception: Optional[BaseException] = None,
    ) -> DiagnosticRecord:
        """Record a failed stage.

        Args:
            stage: One of :class:`Stage`.
            category: One of :class:`FailureCategory`.
            message: Short description; defaults to the exception message.
            url: Target URL, may be ``None``/empty at the processing stage.
            exception: Original exception, if any. Its class name and message
                are captured for ``exception_type`` / ``message``.
        """
        exception_type = type(exception).__name__ if exception is not None else None
        if not message:
            message = str(exception) if exception is not None else ""
            if not message and exception is not None:
                message = repr(exception)

        record = DiagnosticRecord(
            stage=stage,
            success=False,
            category=category,
            message=message,
            exception_type=exception_type,
            url=url or None,
        )
        self.records.append(record)
        return record

    def record_success(
        self,
        stage: str,
        url: Optional[str] = None,
        message: str = "success",
    ) -> DiagnosticRecord:
        """Record a successfully completed stage."""
        record = DiagnosticRecord(
            stage=stage,
            success=True,
            category=None,
            message=message,
            exception_type=None,
            url=url or None,
        )
        self.records.append(record)
        return record

    # ------------------------------------------------------------------- read
    @property
    def ok(self) -> bool:
        """``True`` when no failure has been recorded."""
        return all(record.success for record in self.records)

    @property
    def failed(self) -> bool:
        """Inverse of :attr:`ok`."""
        return not self.ok

    @property
    def last_record(self) -> Optional[DiagnosticRecord]:
        return self.records[-1] if self.records else None

    @property
    def last_failure(self) -> Optional[DiagnosticRecord]:
        for record in reversed(self.records):
            if not record.success:
                return record
        return None

    def failures(self, stage: Optional[str] = None) -> List[DiagnosticRecord]:
        """Return all failure records, optionally filtered by stage."""
        return [
            record
            for record in self.records
            if not record.success and (stage is None or record.stage == stage)
        ]

    def has_failure(self, stage: Optional[str] = None) -> bool:
        """Whether any failure (optionally restricted to ``stage``) was recorded."""
        return bool(self.failures(stage))

    def to_dict(self) -> Dict[str, Any]:
        """Plain-dict snapshot, handy for logging or JSON serialization."""
        return {
            "ok": self.ok,
            "records": [record.to_dict() for record in self.records],
        }

    def __repr__(self) -> str:  # pragma: no cover - trivial
        status = "ok" if self.ok else f"{len(self.failures())} failure(s)"
        return f"ScrapeDiagnostics({status}, records={len(self.records)})"
