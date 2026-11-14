# SPDX-License-Identifier: MIT

import pytest

import url_to_llm_text.get_llm_ready_text as ready_module
from url_to_llm_text import ScraperConfig
from url_to_llm_text.diagnostics import FailureCategory, ScrapeDiagnostics, Stage

URL = "https://example.com/products"

SAMPLE_HTML = """
<html><body>
<h1>Headline</h1>
<p>Body text for the LLM.</p>
<a href="/other">Other page</a>
</body></html>
"""


async def test_pipeline_success_records_all_stages(monkeypatch):
    async def fake_fetch(url, **kwargs):
        kwargs["diagnostics"].record_success(Stage.FETCH, url=url)
        return SAMPLE_HTML

    async def fake_process(page_source, base_url, **kwargs):
        kwargs["diagnostics"].record_success(Stage.PROCESS, url=base_url)
        return "Headline\nBody text for the LLM."

    monkeypatch.setattr(ready_module, "get_page_source", fake_fetch)
    monkeypatch.setattr(ready_module, "get_processed_text", fake_process)
    diag = ScrapeDiagnostics()

    text = await ready_module.url_to_llm_text(
        URL, html_parser="html.parser", config=ScraperConfig(), diagnostics=diag
    )

    assert "Headline" in text
    assert "Body text for the LLM." in text
    assert diag.ok is True
    stages = {r.stage: r for r in diag.records if r.success}
    assert set(stages) == {Stage.FETCH, Stage.PROCESS, Stage.PIPELINE}
    assert all(r.url == URL for r in diag.records)


async def test_pipeline_success_without_diagnostics_unchanged(monkeypatch):
    async def fake_fetch(url, **kwargs):
        return SAMPLE_HTML

    monkeypatch.setattr(ready_module, "get_page_source", fake_fetch)

    text = await ready_module.url_to_llm_text(
        URL, html_parser="html.parser", config=ScraperConfig()
    )
    assert "Headline" in text


async def test_empty_page_source_is_a_fetch_stage_failure_not_processing(monkeypatch):
    async def fake_fetch(url, **kwargs):
        # Simulate the fetch layer reporting its own concrete reason.
        kwargs["diagnostics"].record_failure(
            Stage.FETCH,
            FailureCategory.EMPTY_PAGE_SOURCE,
            message="Page source returned by the browser is empty.",
            url=url,
        )
        return ""

    monkeypatch.setattr(ready_module, "get_page_source", fake_fetch)

    process_called = {"value": False}

    async def fake_process(page_source, base_url, **kwargs):
        process_called["value"] = True
        return "should never be produced"

    monkeypatch.setattr(ready_module, "get_processed_text", fake_process)

    diag = ScrapeDiagnostics()
    text = await ready_module.url_to_llm_text(URL, diagnostics=diag)

    assert text == ""
    assert process_called["value"] is False  # processing stage never runs
    assert not diag.ok

    fetch_failures = diag.failures(Stage.FETCH)
    assert len(fetch_failures) == 1
    assert fetch_failures[0].category == FailureCategory.EMPTY_PAGE_SOURCE

    pipeline_failures = diag.failures(Stage.PIPELINE)
    assert len(pipeline_failures) == 1
    assert pipeline_failures[0].category == FailureCategory.EMPTY_PAGE_SOURCE
    assert pipeline_failures[0].url == URL

    # No processing-stage record exists at all.
    assert diag.failures(Stage.PROCESS) == []
    assert not any(r.stage == Stage.PROCESS for r in diag.records)
    # No success records on a failed run
    assert not any(r.success for r in diag.records)


async def test_empty_page_source_without_diagnostics_returns_empty_string(monkeypatch):
    async def fake_fetch(url, **kwargs):
        return ""

    monkeypatch.setattr(ready_module, "get_page_source", fake_fetch)

    text = await ready_module.url_to_llm_text(URL)
    assert text == ""


async def test_fetch_exception_propagates_reason_through_pipeline(monkeypatch):
    async def fake_fetch(url, **kwargs):
        raise TimeoutError("navigation timed out after 30000ms")

    monkeypatch.setattr(ready_module, "get_page_source", fake_fetch)
    diag = ScrapeDiagnostics()

    # The real get_page_source swallows the error itself; emulate that wrapper
    # behavior here by going through the actual fetch module function instead.
    # Here we assert the pipeline's own guard: an uncaught fetch error is
    # reported as a pipeline failure and still yields "".
    text = await ready_module.url_to_llm_text(URL, diagnostics=diag)

    assert text == ""
    failure = diag.last_failure
    assert failure.stage == Stage.PIPELINE
    assert failure.category == FailureCategory.PROCESSING_ERROR
    assert failure.exception_type == "TimeoutError"
    assert failure.url == URL


async def test_processing_failure_does_not_get_blamed_on_fetch(monkeypatch):
    async def fake_fetch(url, **kwargs):
        kwargs["diagnostics"].record_success(Stage.FETCH, url=url)
        return SAMPLE_HTML

    async def fake_process(page_source, base_url, **kwargs):
        kwargs["diagnostics"].record_failure(
            Stage.PROCESS,
            FailureCategory.PROCESSING_ERROR,
            message="extraction blew up",
            url=base_url,
        )
        return ""

    monkeypatch.setattr(ready_module, "get_page_source", fake_fetch)
    monkeypatch.setattr(ready_module, "get_processed_text", fake_process)

    diag = ScrapeDiagnostics()
    text = await ready_module.url_to_llm_text(URL, diagnostics=diag)

    assert text == ""
    assert diag.failures(Stage.FETCH) == []  # fetch itself succeeded
    process_failures = diag.failures(Stage.PROCESS)
    assert len(process_failures) == 1
    assert process_failures[0].category == FailureCategory.PROCESSING_ERROR
    # Pipeline does not overwrite the processing failure with a success
    assert not any(r.success and r.stage == Stage.PIPELINE for r in diag.records)


async def test_playwright_missing_end_to_end(monkeypatch):
    """Full pipeline with the real fetch layer, playwright forced absent."""
    import url_to_llm_text.get_html_text as html_module

    monkeypatch.setattr(html_module, "async_playwright", None)
    diag = ScrapeDiagnostics()

    text = await ready_module.url_to_llm_text(URL, diagnostics=diag)

    assert text == ""
    categories = {(r.stage, r.category) for r in diag.failures()}
    assert (Stage.FETCH, FailureCategory.PLAYWRIGHT_NOT_INSTALLED) in categories
    assert (Stage.PIPELINE, FailureCategory.EMPTY_PAGE_SOURCE) in categories
    # Processing must never be entered when fetching fails
    assert not any(r.stage == Stage.PROCESS for r in diag.records)


async def test_diagnostics_argument_is_forwarded_to_fetch(monkeypatch):
    seen = {}

    async def fake_fetch(url, **kwargs):
        seen["diagnostics"] = kwargs.get("diagnostics")
        return SAMPLE_HTML

    monkeypatch.setattr(ready_module, "get_page_source", fake_fetch)
    diag = ScrapeDiagnostics()

    await ready_module.url_to_llm_text(URL, diagnostics=diag)

    assert seen["diagnostics"] is diag


async def test_default_signatures_accept_no_diagnostics():
    # Compatibility guard: calling with positional args only still works.
    import inspect

    for func in (
        ready_module.url_to_llm_text,
        ready_module.get_page_source,
        ready_module.get_processed_text,
    ):
        sig = inspect.signature(func)
        assert "diagnostics" in sig.parameters
        assert sig.parameters["diagnostics"].default is None
