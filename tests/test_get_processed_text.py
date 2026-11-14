# SPDX-License-Identifier: MIT

import pytest
from bs4 import FeatureNotFound

import url_to_llm_text.get_llm_input_text as input_module
from url_to_llm_text import ScraperConfig
from url_to_llm_text.diagnostics import FailureCategory, ScrapeDiagnostics, Stage

URL = "https://example.com/products"

SAMPLE_HTML = """
<html>
  <body>
    <h1>Product list</h1>
    <p>A descriptive paragraph.</p>
    <a href="/detail/1">Product One</a>
    <img src="/img/p1.jpg" alt="p1">
    <table>
      <caption>Specs</caption>
      <tr><th>Name</th><th>Price</th></tr>
      <tr><td>Widget</td><td>9.99</td></tr>
    </table>
  </body>
</html>
"""


async def test_success_returns_text_and_records_success():
    diag = ScrapeDiagnostics()

    text = await input_module.get_processed_text(
        SAMPLE_HTML, URL, html_parser="html.parser", diagnostics=diag
    )

    assert "Product list" in text
    assert "Product One" in text
    # Image link is preserved and absolutized against base_url
    assert "https://example.com/img/p1.jpg" in text
    # Table rendered as markdown
    assert "Widget" in text and "9.99" in text
    assert diag.ok is True
    record = diag.last_record
    assert record.success is True
    assert record.stage == Stage.PROCESS
    assert record.url == URL


async def test_success_without_diagnostics_matches_diagnostic_output():
    without = await input_module.get_processed_text(
        SAMPLE_HTML, URL, html_parser="html.parser"
    )
    diag = ScrapeDiagnostics()
    with_diag = await input_module.get_processed_text(
        SAMPLE_HTML, URL, html_parser="html.parser", diagnostics=diag
    )

    assert without == with_diag
    assert diag.ok is True


async def test_unavailable_parser_is_classified():
    diag = ScrapeDiagnostics()

    text = await input_module.get_processed_text(
        SAMPLE_HTML, URL, html_parser="definitely-not-a-real-parser", diagnostics=diag
    )

    # Original behaviour: fallback parser still yields something, no exception
    assert isinstance(text, str)
    failure = diag.last_failure
    assert failure is not None
    assert failure.category == FailureCategory.HTML_PARSER_UNAVAILABLE
    assert failure.stage == Stage.PROCESS
    assert failure.exception_type == "FeatureNotFound"
    assert failure.url == URL
    # A failed stage must not also emit a misleading success record
    assert not any(r.success and r.stage == Stage.PROCESS for r in diag.records)


async def test_unavailable_parser_without_diagnostics_still_returns_text():
    text = await input_module.get_processed_text(
        SAMPLE_HTML, URL, html_parser="definitely-not-a-real-parser"
    )
    assert isinstance(text, str)


async def test_processing_exception_is_classified(monkeypatch):
    diag = ScrapeDiagnostics()

    def boom(*args, **kwargs):
        raise RuntimeError("soup exploded")

    monkeypatch.setattr(input_module, "BeautifulSoup", boom)

    text = await input_module.get_processed_text(
        SAMPLE_HTML, URL, html_parser="html.parser", diagnostics=diag
    )

    assert text == ""
    failure = diag.last_failure
    assert failure.category == FailureCategory.PROCESSING_ERROR
    assert failure.stage == Stage.PROCESS
    assert failure.exception_type == "RuntimeError"
    assert "soup exploded" in failure.message
    # Processing-stage URL may be provided but is allowed to be empty
    assert failure.url == URL


async def test_processing_exception_without_diagnostics_returns_empty(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("soup exploded")

    monkeypatch.setattr(input_module, "BeautifulSoup", boom)

    text = await input_module.get_processed_text(SAMPLE_HTML, URL, html_parser="html.parser")
    assert text == ""


async def test_processing_stage_url_may_be_empty(monkeypatch):
    diag = ScrapeDiagnostics()

    def boom(*args, **kwargs):
        raise ValueError("no base url")

    monkeypatch.setattr(input_module, "BeautifulSoup", boom)

    await input_module.get_processed_text(
        SAMPLE_HTML, "", html_parser="html.parser", diagnostics=diag
    )

    failure = diag.last_failure
    assert failure.category == FailureCategory.PROCESSING_ERROR
    assert failure.url is None


async def test_feature_not_found_is_real_bs4_exception():
    # Guards the assumption used by the parser-unavailable test.
    with pytest.raises(FeatureNotFound):
        import bs4

        bs4.BeautifulSoup(SAMPLE_HTML, "definitely-not-a-real-parser")
