"""Unit tests for browser-use per-page capture records.

These tests use fake agent histories modeled on browser-use's
``AgentHistoryList`` (``.history`` items, each with ``state.url`` /
``state.title`` and ``.result[].extracted_content``, plus ``final_result()``).
No real browser, LLM, or browser-use install is required.
"""

from __future__ import annotations

import pytest

from clawbridge.engines.browser_use_engine import BrowserUseEngine, build_page_records
from clawbridge.shared.page_records import PAGE_SNIPPET_MAX_CHARS
from clawbridge.shared.schemas import (
    EngineName,
    EngineStatus,
    PageRecord,
    Task,
    TaskStatus,
)

# ── Fake browser-use history ─────────────────────────────────────────────────


class FakeState:
    def __init__(self, url: str = "", title: str = "") -> None:
        self.url = url
        self.title = title


class FakeActionResult:
    def __init__(
        self,
        extracted_content: str | None = None,
        long_term_memory: str | None = None,
        error: str | None = None,
    ) -> None:
        self.extracted_content = extracted_content
        self.long_term_memory = long_term_memory
        self.error = error


class FakeHistoryItem:
    """One agent step: browser state after the step plus that step's results."""

    def __init__(self, url: str = "", title: str = "", results=None) -> None:
        self.state = FakeState(url=url, title=title)
        self.result = list(results or [])


class FakeHistory:
    """Minimal stand-in for ``AgentHistoryList``."""

    def __init__(self, items=None, final_result: str | None = None) -> None:
        self.history = list(items or [])
        self._final_result = final_result

    def final_result(self) -> str | None:
        return self._final_result


def _step(url, title="", content=None):
    results = [FakeActionResult(extracted_content=content)] if content is not None else []
    return FakeHistoryItem(url=url, title=title, results=results)


# ── 1. Normal extraction ─────────────────────────────────────────────────────


class TestNormalExtraction:
    def test_single_page_captured(self):
        history = FakeHistory(
            items=[_step("https://example.com/a", "Page A", "Alpha body text")],
            final_result="Alpha body text",
        )

        records = build_page_records(history)

        assert len(records) == 1
        record = records[0]
        assert isinstance(record, PageRecord)
        assert record.url == "https://example.com/a"
        assert record.title == "Page A"
        assert record.snippet == "Alpha body text"
        assert record.captured is True
        assert record.reason == ""
        assert record.step == 1

    def test_records_follow_first_visit_order(self):
        history = FakeHistory(
            items=[
                _step("https://example.com/first", "First", "First page content"),
                _step("https://example.com/second", "Second", "Second page content"),
                _step("https://example.com/third", "Third", "Third page content"),
            ],
            final_result="Third page content",
        )

        records = build_page_records(history)

        assert [r.url for r in records] == [
            "https://example.com/first",
            "https://example.com/second",
            "https://example.com/third",
        ]
        assert [r.step for r in records] == [1, 2, 3]
        assert all(r.captured for r in records)
        assert [r.title for r in records] == ["First", "Second", "Third"]

    def test_page_without_step_content_uses_final_result(self):
        """The last page may be reported only in the final answer."""
        history = FakeHistory(
            items=[
                _step("https://example.com/listing", "Listing"),
            ],
            final_result="The listing shows 3 bedrooms.",
        )

        records = build_page_records(history)

        assert len(records) == 1
        assert records[0].captured is True
        assert records[0].snippet == "The listing shows 3 bedrooms."
        assert records[0].reason == ""

    def test_snippet_is_capped(self):
        long_text = "x" * (PAGE_SNIPPET_MAX_CHARS + 400)
        history = FakeHistory(
            items=[_step("https://example.com/long", "Long", long_text)],
            final_result=long_text,
        )

        records = build_page_records(history)

        assert len(records[0].snippet) == PAGE_SNIPPET_MAX_CHARS
        assert records[0].captured is True

    def test_agent_narration_is_not_page_content(self):
        """long_term_memory / error text must never be reported as an extraction."""
        item = FakeHistoryItem(
            url="https://example.com/memory",
            title="Memory",
            results=[
                FakeActionResult(
                    extracted_content=None,
                    long_term_memory="Navigated to the page",
                    error="click missed",
                )
            ],
        )
        history = FakeHistory(items=[item], final_result="")

        records = build_page_records(history)

        assert len(records) == 1
        assert records[0].captured is False
        assert records[0].snippet == ""
        assert "Final result was empty" in records[0].reason


# ── 2. Empty final result ────────────────────────────────────────────────────


class TestEmptyFinalResult:
    def test_empty_final_result_is_not_faked_as_captured(self):
        history = FakeHistory(
            items=[_step("https://example.com/empty", "Empty Page")],
            final_result=None,
        )

        records = build_page_records(history)

        assert len(records) == 1
        record = records[0]
        assert record.captured is False
        assert record.snippet == ""
        assert record.reason, "A not-captured record must state why"
        assert "final result was empty" in record.reason.lower()

    def test_blank_final_result_string_is_treated_as_empty(self):
        history = FakeHistory(
            items=[_step("https://example.com/blank", "Blank")],
            final_result="   ",
        )

        records = build_page_records(history)

        assert len(records) == 1
        assert records[0].captured is False
        assert records[0].reason

    def test_empty_final_result_keeps_earlier_extracted_pages_captured(self):
        """Only pages lacking real content are marked not captured."""
        history = FakeHistory(
            items=[
                _step("https://example.com/ok", "OK", "Genuine extracted content"),
                _step("https://example.com/failed", "Failed"),
            ],
            final_result="",
        )

        records = build_page_records(history)

        assert len(records) == 2
        assert records[0].captured is True
        assert records[0].snippet == "Genuine extracted content"
        assert records[1].captured is False
        assert "final result was empty" in records[1].reason.lower()

    def test_no_usable_url_is_recorded_as_not_captured(self):
        """No URL at all -> one explicit not-captured record, never a fake page."""
        history = FakeHistory(
            items=[
                FakeHistoryItem(url="", title="", results=[FakeActionResult(extracted_content="orphan")]),
                FakeHistoryItem(url="about:blank", title="New Tab", results=[]),
            ],
            final_result="",
        )

        records = build_page_records(history)

        assert len(records) == 1
        record = records[0]
        assert record.url == ""
        assert record.captured is False
        assert "no usable url" in record.reason.lower()

    def test_empty_history_yields_single_not_captured_record(self):
        records = build_page_records(FakeHistory(items=[], final_result=None))

        assert len(records) == 1
        assert records[0].captured is False
        assert records[0].url == ""
        assert records[0].reason


# ── 3. Duplicate URL ─────────────────────────────────────────────────────────


class TestDuplicateUrl:
    def test_repeated_url_adds_no_record_and_counts_skip(self):
        history = FakeHistory(
            items=[
                _step("https://example.com/a", "Page A", "First visit content"),
                _step("https://example.com/b", "Page B", "Page B content"),
                _step("https://example.com/a", "Page A", "Second visit content"),
            ],
            final_result="Second visit content",
        )

        records = build_page_records(history)

        assert len(records) == 2
        assert [r.url for r in records] == ["https://example.com/a", "https://example.com/b"]
        assert records[0].duplicate_skips == 1
        assert records[1].duplicate_skips == 0
        # The original record keeps its first-visit position and step number.
        assert records[0].step == 1
        assert records[1].step == 2

    def test_multiple_duplicates_accumulate_on_the_single_record(self):
        history = FakeHistory(
            items=[
                _step("https://example.com/a", "Page A", "content"),
                _step("https://example.com/a", "Page A", "content"),
                _step("https://example.com/a", "Page A", "content"),
            ],
            final_result="content",
        )

        records = build_page_records(history)

        assert len(records) == 1
        assert records[0].duplicate_skips == 2

    def test_revisit_supplies_content_for_a_page_that_was_empty(self):
        """A duplicate never creates a record, but it can fill the existing one."""
        history = FakeHistory(
            items=[
                _step("https://example.com/a", "Page A"),
                _step("https://example.com/a", "Page A", "Content found on revisit"),
            ],
            final_result="",
        )

        records = build_page_records(history)

        assert len(records) == 1
        assert records[0].captured is True
        assert records[0].snippet == "Content found on revisit"
        assert records[0].duplicate_skips == 1

    def test_duplicate_after_final_result_does_not_eat_final_text(self):
        """Revisits are skipped even when the final result is the only content."""
        history = FakeHistory(
            items=[
                _step("https://example.com/a", "Page A", "Page A content"),
                _step("https://example.com/a", "Page A"),
            ],
            final_result="Page A content",
        )

        records = build_page_records(history)

        assert len(records) == 1
        assert records[0].captured is True
        assert records[0].snippet == "Page A content"
        assert records[0].duplicate_skips == 1


# ── Engine integration ───────────────────────────────────────────────────────


class TestEngineResultIntegration:
    """The records must ride along in the TaskResult returned to the caller."""

    def test_build_task_result_carries_page_records(self):
        history = FakeHistory(
            items=[
                _step("https://example.com/one", "One", "First content"),
                _step("https://example.com/two", "Two", "Second content"),
                _step("https://example.com/one", "One", "again"),
            ],
            final_result="Second content",
        )

        result = BrowserUseEngine()._build_task_result(history, duration_ms=1234)

        assert result.engine_used == EngineName.BROWSER_USE
        assert result.total_duration_ms == 1234
        assert len(result.page_records) == 2
        assert [r.url for r in result.page_records] == [
            "https://example.com/one",
            "https://example.com/two",
        ]
        assert [r.captured for r in result.page_records] == [True, True]
        assert result.page_records[0].duplicate_skips == 1

    def test_build_task_result_empty_final_result_marks_pages_uncaptured(self):
        history = FakeHistory(
            items=[_step("https://example.com/none", "None")],
            final_result=None,
        )

        result = BrowserUseEngine()._build_task_result(history, duration_ms=10)

        assert result.summary == ""
        assert len(result.page_records) == 1
        assert result.page_records[0].captured is False
        assert result.page_records[0].reason

    def test_build_task_result_survives_broken_history_accessors(self):
        """Missing urls()/action_results() must not drop the page records."""

        class PartialHistory:
            def __init__(self):
                self.history = [_step("https://example.com/x", "X", "Body")]

            def urls(self):
                raise RuntimeError("boom")

            def action_results(self):
                raise RuntimeError("boom")

            def final_result(self):
                return "Body"

        result = BrowserUseEngine()._build_task_result(PartialHistory(), duration_ms=5)

        assert len(result.page_records) == 1
        assert result.page_records[0].captured is True
        assert result.page_records[0].url == "https://example.com/x"

    def test_page_records_serialize_into_payload(self):
        """Records must appear in the JSON payload returned by the API."""
        history = FakeHistory(
            items=[_step("https://example.com/a", "A", "content")],
            final_result="content",
        )
        result = BrowserUseEngine()._build_task_result(history, duration_ms=1)

        payload = result.model_dump(mode="json")

        assert payload["page_records"] == [
            {
                "url": "https://example.com/a",
                "title": "A",
                "snippet": "content",
                "captured": True,
                "reason": "",
                "step": 1,
                "duplicate_skips": 0,
            }
        ]


# ── End-to-end run_task with a fake agent (still no browser) ─────────────────


class _FakeBrowser:
    """Placeholder so run_task skips the real BrowserProfile import."""

    async def close(self):
        pass


class _FakeAgent:
    """Stands in for browser_use.Agent; returns a canned history from run()."""

    history_to_return: FakeHistory | None = None

    def __init__(self, task, llm, browser):
        self.task = task
        self.llm = llm
        self.browser = browser

    async def run(self, max_steps=100, on_step_end=None):
        return self.history_to_return


class TestRunTaskReturnsPageRecords:
    def _engine(self) -> BrowserUseEngine:
        engine = BrowserUseEngine()
        engine._status = EngineStatus.AVAILABLE
        engine._browser = _FakeBrowser()
        engine._agent_class = _FakeAgent
        engine._llm = object()
        return engine

    @pytest.mark.asyncio
    async def test_run_task_result_carries_ordered_deduplicated_records(self, monkeypatch):
        import clawbridge.engines.browser_use_engine as engine_module

        class _Settings:
            max_actions_per_task = 10
            browser_headless = True

        monkeypatch.setattr(engine_module, "get_settings", lambda: _Settings())

        _FakeAgent.history_to_return = FakeHistory(
            items=[
                _step("https://example.com/one", "One", "First body"),
                _step("https://example.com/two", "Two", "Second body"),
                _step("https://example.com/one", "One", "again"),
            ],
            final_result="Second body",
        )

        task = await self._engine().run_task(Task(prompt="collect pages"))

        assert task.status == TaskStatus.COMPLETE
        assert task.result is not None
        records = task.result.page_records
        assert len(records) == 2
        assert [r.url for r in records] == [
            "https://example.com/one",
            "https://example.com/two",
        ]
        assert [r.captured for r in records] == [True, True]
        assert [r.duplicate_skips for r in records] == [1, 0]

    @pytest.mark.asyncio
    async def test_run_task_empty_final_result_marks_records_uncaptured(self, monkeypatch):
        import clawbridge.engines.browser_use_engine as engine_module

        class _Settings:
            max_actions_per_task = 10
            browser_headless = True

        monkeypatch.setattr(engine_module, "get_settings", lambda: _Settings())

        _FakeAgent.history_to_return = FakeHistory(
            items=[_step("https://example.com/empty", "Empty")],
            final_result=None,
        )

        task = await self._engine().run_task(Task(prompt="collect pages"))

        assert task.status == TaskStatus.COMPLETE
        records = task.result.page_records
        assert len(records) == 1
        assert records[0].captured is False
        assert records[0].reason
