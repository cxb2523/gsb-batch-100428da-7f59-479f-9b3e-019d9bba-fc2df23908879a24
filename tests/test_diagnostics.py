# SPDX-License-Identifier: MIT

from url_to_llm_text.diagnostics import (
    DiagnosticRecord,
    FailureCategory,
    ScrapeDiagnostics,
    Stage,
)


def test_fresh_collector_is_ok_and_empty():
    diag = ScrapeDiagnostics()
    assert diag.ok is True
    assert diag.failed is False
    assert diag.records == []
    assert diag.last_record is None
    assert diag.last_failure is None
    assert diag.failures() == []
    assert diag.has_failure() is False


def test_record_failure_captures_context_from_exception():
    diag = ScrapeDiagnostics()
    try:
        raise TimeoutError("navigation exceeded 30000ms")
    except TimeoutError as exc:
        record = diag.record_failure(
            Stage.FETCH,
            FailureCategory.NAVIGATION_TIMEOUT,
            url="https://example.com",
            exception=exc,
        )

    assert isinstance(record, DiagnosticRecord)
    assert record.success is False
    assert record.stage == Stage.FETCH
    assert record.category == FailureCategory.NAVIGATION_TIMEOUT
    assert record.exception_type == "TimeoutError"
    assert "30000ms" in record.message
    assert record.url == "https://example.com"

    assert diag.ok is False
    assert diag.last_failure is record
    assert diag.failures(Stage.FETCH) == [record]
    assert diag.failures(Stage.PROCESS) == []
    assert diag.has_failure(Stage.FETCH) is True


def test_record_failure_without_exception_uses_explicit_message():
    diag = ScrapeDiagnostics()
    record = diag.record_failure(
        Stage.PROCESS,
        FailureCategory.HTML_PARSER_UNAVAILABLE,
        message="unknown parser",
        url=None,
    )
    assert record.success is False
    assert record.exception_type is None
    assert record.message == "unknown parser"
    assert record.url is None  # processing stage may omit the URL


def test_record_success_and_stage_filtering():
    diag = ScrapeDiagnostics()
    success = diag.record_success(Stage.FETCH, url="https://example.com")
    assert success.success is True
    assert success.category is None
    assert success.url == "https://example.com"
    assert diag.ok is True

    diag.record_failure(Stage.PROCESS, FailureCategory.PROCESSING_ERROR, url="https://example.com")
    assert diag.ok is False
    # The earlier success record is retained but failures() hides it.
    assert len(diag.records) == 2
    assert len(diag.failures()) == 1
    assert diag.failures(Stage.PROCESS)[0].category == FailureCategory.PROCESSING_ERROR


def test_empty_url_is_normalized_to_none():
    diag = ScrapeDiagnostics()
    record = diag.record_failure(
        Stage.PROCESS,
        FailureCategory.PROCESSING_ERROR,
        message="boom",
        url="",
    )
    assert record.url is None


def test_to_dict_is_serializable():
    diag = ScrapeDiagnostics()
    diag.record_failure(
        Stage.FETCH,
        FailureCategory.EMPTY_PAGE_SOURCE,
        message="empty",
        url="https://example.com",
    )
    payload = diag.to_dict()
    assert payload["ok"] is False
    assert payload["records"][0]["category"] == FailureCategory.EMPTY_PAGE_SOURCE
    assert payload["records"][0]["stage"] == Stage.FETCH
    assert set(payload["records"][0].keys()) == {
        "stage",
        "success",
        "category",
        "message",
        "exception_type",
        "url",
    }
