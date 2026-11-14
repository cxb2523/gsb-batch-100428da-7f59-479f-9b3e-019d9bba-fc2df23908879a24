"""Unit tests for ``BrowserExecutor.fill_form`` structured results.

These tests never touch a real LLM, browser, network or API key. The heavy
third-party stack used by the pipeline (``browser_use``,
``langchain_google_genai``, ...) is replaced with in-process stubs before the
pipeline packages are imported, and the executor's browser/agent factories are
overridden with fakes that record every interaction.

Covered paths:
1. Normal mapping -> every field filled.
2. Over-long value -> truncated against the field constraint (original and
   filled value both retained).
3. Mapped label missing from the page -> ``label_not_found``, never guessed
   onto another field.
4. Mapping stage returns an empty object / unparseable JSON -> the filling
   stage is skipped and a failure result is returned with the browser closed.
"""

import asyncio
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Make the repo root importable even under `python -I` (isolated mode).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


class _FakeAsyncLogFile:
    """Async context manager stand-in so no browser_output.log is written."""

    def __init__(self):
        self.writes = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def write(self, text):
        self.writes.append(text)


# ---------------------------------------------------------------------------
# Stub out third-party packages that are optional at test time.
# ---------------------------------------------------------------------------

def _install_module_stubs() -> None:
    """Insert lightweight fake modules for unavailable third-party deps."""

    def ensure_pkg(name: str) -> types.ModuleType:
        if name in sys.modules:
            return sys.modules[name]
        module = types.ModuleType(name)
        module.__path__ = []  # mark as package
        sys.modules[name] = module
        return module

    # Pipeline __init__ -> processor -> generators -> gemini needs this.
    ensure_pkg("langchain_google_genai").ChatGoogleGenerativeAI = MagicMock()

    # browser_executor only imports browser_use lazily/optionally, but provide
    # stubs too so the module-level optional-import branch is deterministic.
    browser_use = ensure_pkg("browser_use")

    class _BrowserProfileStub:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _Unused:  # pragma: no cover - factories are overridden in tests
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    browser_use.Agent = _Unused
    browser_use.Browser = _Unused
    browser_use.BrowserProfile = _BrowserProfileStub

    browser_pkg = ensure_pkg("browser_use.browser")
    events_pkg = ensure_pkg("browser_use.browser.events")
    views_pkg = ensure_pkg("browser_use.browser.views")
    watchdogs_pkg = ensure_pkg("browser_use.browser.watchdogs")
    dom_wd = ensure_pkg("browser_use.browser.watchdogs.dom_watchdog")
    local_wd = ensure_pkg("browser_use.browser.watchdogs.local_browser_watchdog")
    security_wd = ensure_pkg("browser_use.browser.watchdogs.security_watchdog")
    llm_pkg = ensure_pkg("browser_use.llm")
    google_pkg = ensure_pkg("browser_use.llm.google")
    google_chat = ensure_pkg("browser_use.llm.google.chat")
    bu_utils = ensure_pkg("browser_use.utils")

    events_pkg.BrowserStateRequestEvent = type("BrowserStateRequestEvent", (), {})
    views_pkg.BrowserStateSummary = type("BrowserStateSummary", (), {})
    views_pkg.PageInfo = type("PageInfo", (), {})
    views_pkg.SerializedDOMState = type("SerializedDOMState", (), {})

    class _Watchdog:
        pass

    dom_wd.DOMWatchdog = type("DOMWatchdog", (_Watchdog,), {})
    local_wd.LocalBrowserWatchdog = type("LocalBrowserWatchdog", (_Watchdog,), {})

    class _SecurityWatchdog:
        @staticmethod
        def _is_url_allowed(url):  # pragma: no cover - replaced by patch
            return not url.startswith("file:///")

    security_wd.SecurityWatchdog = _SecurityWatchdog
    google_chat.ChatGoogle = MagicMock()
    bu_utils.create_task_with_error_handling = MagicMock()


_install_module_stubs()

from pipeline.config import PipelineConfig  # noqa: E402
from pipeline.utils import browser_executor as be  # noqa: E402
from pipeline.utils.browser_executor import (  # noqa: E402
    REASON_MAPPING_EMPTY,
    REASON_MAPPING_PARSE_ERROR,
    STATUS_FILLED,
    STATUS_LABEL_NOT_FOUND,
    STATUS_NOT_MAPPED,
    STATUS_TRUNCATED,
    BrowserExecutor,
    MappingError,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeAgentResult:
    def __init__(self, text: str) -> None:
        self._text = text

    def final_result(self) -> str:
        return self._text


class FakeAgent:
    """Records the task it was given and returns a canned final result."""

    instances: list["FakeAgent"] = []

    def __init__(self, task: str, llm, browser, final_text: str = "done") -> None:
        self.task = task
        self.llm = llm
        self.browser = browser
        self.final_text = final_text
        self.run_calls: list[int] = []
        FakeAgent.instances.append(self)

    async def run(self, max_steps: int) -> FakeAgentResult:
        self.run_calls.append(max_steps)
        return FakeAgentResult(self.final_text)


class FakeBrowser:
    """Browser stand-in that tracks open/close lifecycle."""

    instances: list["FakeBrowser"] = []

    def __init__(self, browser_profile=None) -> None:
        self.browser_profile = browser_profile
        self.killed = False
        FakeBrowser.instances.append(self)

    async def kill(self) -> None:
        self.killed = True


class FakeLLMResponse:
    def __init__(self, completion: str) -> None:
        self.completion = completion


class FakeLLM:
    """Stand-in for ChatGoogle: returns queued completion strings."""

    def __init__(self, completions):
        self._completions = list(completions)
        self.invocations = []

    async def ainvoke(self, messages):
        self.invocations.append(messages)
        if not self._completions:  # pragma: no cover - defensive
            raise AssertionError("FakeLLM ran out of canned responses")
        return FakeLLMResponse(self._completions.pop(0))


def make_schema_json(fields):
    """Serialize schema fields the same way Pass 1 is asked to."""
    return json.dumps(
        [
            {
                "label": f["label"],
                "field_type": f.get("field_type", "text"),
                "constraint": f.get("constraint"),
            }
            for f in fields
        ]
    )


class StubbedExecutor(BrowserExecutor):
    """BrowserExecutor with browser/agent factories replaced by fakes."""

    def __init__(self, config, llm, schema_text, fill_text="form filled ok"):
        # Bypass the real ChatGoogle construction entirely.
        self.config = config
        self.llm = llm
        self._schema_text = schema_text
        self._fill_text = fill_text

    def _create_browser(self, browser_config):
        return FakeBrowser(browser_config)

    def _create_agent(self, task, browser):
        # Pass 1 (schema) tasks mention "report the schema"; Pass 2 tasks are
        # the deterministic filling instructions.
        if "report the schema" in task:
            return FakeAgent(task, self.llm, browser, self._schema_text)
        return FakeAgent(task, self.llm, browser, self._fill_text)


def build_executor(mapping_completion, schema_fields, fill_text="form filled ok"):
    llm = FakeLLM([mapping_completion])
    schema_text = make_schema_json(schema_fields)
    executor = StubbedExecutor(PipelineConfig(), llm, schema_text, fill_text)
    return executor, llm


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFillFormStructuredResults(unittest.TestCase):
    def setUp(self):
        FakeAgent.instances.clear()
        FakeBrowser.instances.clear()
        # Redirect the browser_output.log writes to an in-memory fake so the
        # test run never creates files on disk.
        self.fake_log = _FakeAsyncLogFile()
        self._aio_patch = patch(
            "pipeline.utils.browser_executor.aiofiles.open",
            return_value=self.fake_log,
        )
        self._aio_patch.start()
        self.addCleanup(self._aio_patch.stop)

    def _run(self, coro):
        return asyncio.run(coro)

    # -- Path 1: normal mapping, all fields filled --------------------------

    def test_normal_mapping_all_fields_filled(self):
        schema_fields = [
            {"label": "Full Name", "field_type": "text", "constraint": None},
            {"label": "Email", "field_type": "email", "constraint": None},
        ]
        mapping = {"Full Name": "Ada Lovelace", "Email": "ada@example.com"}
        executor, llm = build_executor(json.dumps(mapping), schema_fields)

        result = self._run(executor.fill_form("https://form.example/apply", {"x": 1}))

        self.assertTrue(result["success"])
        self.assertIsNone(result["failure_reason"])
        self.assertEqual(result["url"], "https://form.example/apply")
        # Order follows the mapping order exactly.
        self.assertEqual([f["label"] for f in result["field_results"]],
                         ["Full Name", "Email"])
        statuses = {f["label"]: f["status"] for f in result["field_results"]}
        self.assertEqual(statuses, {"Full Name": STATUS_FILLED, "Email": STATUS_FILLED})
        for f in result["field_results"]:
            self.assertEqual(f["original_value"], f["filled_value"])
            self.assertIsNone(f["error"])
        # Result is JSON-serializable (stable parsing contract).
        json.dumps(result)
        # Two browser sessions (Pass 1 + Pass 2), both closed.
        self.assertEqual(len(FakeBrowser.instances), 2)
        self.assertTrue(all(b.killed for b in FakeBrowser.instances))
        # The LLM was really used for the mapping stage.
        self.assertEqual(len(llm.invocations), 1)

    # -- Path 2: over-long value truncated to the constraint ----------------

    def test_overlong_value_is_truncated_and_both_values_kept(self):
        schema_fields = [
            {"label": "Bio", "field_type": "textarea",
             "constraint": "Max 10 characters"},
            {"label": "Nickname", "field_type": "text", "constraint": None},
        ]
        long_value = "abcdefghijklmnopqrstuvwxyz"
        mapping = {"Bio": long_value, "Nickname": "Ada"}
        executor, _ = build_executor(json.dumps(mapping), schema_fields)

        result = self._run(executor.fill_form("https://form.example/apply", {}))

        self.assertTrue(result["success"], msg=result.get("error"))
        bio = result["field_results"][0]
        self.assertEqual(bio["status"], STATUS_TRUNCATED)
        self.assertEqual(bio["original_value"], long_value)
        self.assertEqual(bio["filled_value"], long_value[:10])
        self.assertEqual(len(bio["filled_value"]), 10)
        self.assertEqual(bio["max_length"], 10)
        self.assertIsNotNone(bio["error"])
        # The truncated string is what the fill agent actually received.
        fill_agent = FakeAgent.instances[-1]
        self.assertIn(long_value[:10], fill_agent.task)
        self.assertNotIn(long_value, fill_agent.task)
        # The other field is untouched.
        self.assertEqual(result["field_results"][1]["status"], STATUS_FILLED)

    # -- Path 3: label missing from the page --------------------------------

    def test_missing_label_is_reported_and_not_guessed(self):
        schema_fields = [
            {"label": "Full Name", "field_type": "text", "constraint": None},
        ]
        mapping = {
            "Full Name": "Ada Lovelace",
            "Phone Number": "+1 555 0100",  # no such label on the page
        }
        executor, _ = build_executor(json.dumps(mapping), schema_fields)

        result = self._run(executor.fill_form("https://form.example/apply", {}))

        self.assertFalse(result["success"])
        self.assertEqual(result["failure_reason"], "field_failures")
        labels = [f["label"] for f in result["field_results"]]
        self.assertEqual(labels, ["Full Name", "Phone Number"])
        missing = result["field_results"][1]
        self.assertEqual(missing["status"], STATUS_LABEL_NOT_FOUND)
        self.assertEqual(missing["original_value"], "+1 555 0100")
        self.assertIsNone(missing["filled_value"])
        # The unknown label must never reach the fill agent's mapping.
        fill_agent = FakeAgent.instances[-1]
        self.assertNotIn("Phone Number", fill_agent.task)
        self.assertNotIn("+1 555 0100", fill_agent.task)

    # -- Path 4a: mapping returns an empty object ---------------------------

    def test_empty_mapping_object_aborts_before_filling(self):
        schema_fields = [{"label": "Full Name", "constraint": None}]
        executor, llm = build_executor("{}", schema_fields)

        result = self._run(executor.fill_form("https://form.example/apply", {}))

        self.assertFalse(result["success"])
        self.assertEqual(result["failure_reason"], REASON_MAPPING_EMPTY)
        self.assertIsNotNone(result["error"])
        self.assertEqual(result["mapping"], {})
        self.assertEqual(result["field_results"], [])
        # Only the Pass 1 schema browser ever existed, and it is closed; no
        # Pass 2 fill browser was launched.
        self.assertEqual(len(FakeBrowser.instances), 1)
        self.assertTrue(FakeBrowser.instances[0].killed)
        self.assertEqual(len(FakeAgent.instances), 1)  # schema agent only

    # -- Path 4b: mapping returns unparseable JSON --------------------------

    def test_unparseable_mapping_json_aborts_before_filling(self):
        schema_fields = [{"label": "Full Name", "constraint": None}]
        executor, _ = build_executor("this is not json at all", schema_fields)

        result = self._run(executor.fill_form("https://form.example/apply", {}))

        self.assertFalse(result["success"])
        self.assertEqual(result["failure_reason"], REASON_MAPPING_PARSE_ERROR)
        self.assertEqual(result["field_results"], [])
        # Filling stage never started: schema browser only, and closed.
        self.assertEqual(len(FakeBrowser.instances), 1)
        self.assertTrue(all(b.killed for b in FakeBrowser.instances))
        self.assertEqual(len(FakeAgent.instances), 1)

    # -- Bonus: not_mapped fields and mapping-order contract ----------------

    def test_unmapped_schema_fields_are_reported_after_mapped_ones(self):
        schema_fields = [
            {"label": "Full Name", "constraint": None},
            {"label": "Referral Code", "constraint": "Max 8 characters"},
        ]
        mapping = {"Full Name": "Ada Lovelace"}
        executor, _ = build_executor(json.dumps(mapping), schema_fields)

        result = self._run(executor.fill_form("https://form.example/apply", {}))

        self.assertTrue(result["success"])
        self.assertEqual([f["label"] for f in result["field_results"]],
                         ["Full Name", "Referral Code"])
        unmapped = result["field_results"][1]
        self.assertEqual(unmapped["status"], STATUS_NOT_MAPPED)
        self.assertIsNone(unmapped["original_value"])
        self.assertIsNone(unmapped["filled_value"])
        self.assertEqual(unmapped["max_length"], 8)


class TestMappingStageGuards(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def _executor_with_llm(self, completion):
        llm = FakeLLM([completion])
        executor = BrowserExecutor.__new__(BrowserExecutor)
        executor.config = PipelineConfig()
        executor.llm = llm
        return executor, llm

    def test_markdown_fenced_json_is_still_parsed(self):
        executor, _ = self._executor_with_llm('```json\n{"Name": "Ada"}\n```')
        mapping = self._run(executor._map_data_to_schema("schema", {"a": 1}))
        self.assertEqual(mapping, {"Name": "Ada"})

    def test_parse_error_raises_mapping_error_with_reason(self):
        executor, _ = self._executor_with_llm("not-json{")
        with self.assertRaises(MappingError) as ctx:
            self._run(executor._map_data_to_schema("schema", {}))
        self.assertEqual(ctx.exception.reason, REASON_MAPPING_PARSE_ERROR)

    def test_empty_object_raises_mapping_error_with_reason(self):
        executor, _ = self._executor_with_llm("{}")
        with self.assertRaises(MappingError) as ctx:
            self._run(executor._map_data_to_schema("schema", {}))
        self.assertEqual(ctx.exception.reason, REASON_MAPPING_EMPTY)

    def test_non_object_json_raises_parse_error(self):
        executor, _ = self._executor_with_llm('["a", "b"]')
        with self.assertRaises(MappingError) as ctx:
            self._run(executor._map_data_to_schema("schema", {}))
        self.assertEqual(ctx.exception.reason, REASON_MAPPING_PARSE_ERROR)


class TestSchemaAndConstraintHelpers(unittest.TestCase):
    def test_parse_schema_fields_from_json_array(self):
        schema = json.dumps([
            {"label": "Name", "field_type": "text", "constraint": None},
            {"label": "Bio", "field_type": "textarea",
             "constraint": "Maximum 20 characters"},
        ])
        fields = be._parse_schema_fields(schema)
        self.assertEqual([f["label"] for f in fields], ["Name", "Bio"])
        self.assertIsNone(fields[0]["max_length"])
        self.assertEqual(fields[1]["max_length"], 20)

    def test_parse_schema_fields_free_text_fallback(self):
        text = "1. Full Name (text)\n2. Tagline: Max 5 characters\n"
        fields = be._parse_schema_fields(text)
        labels = {f["label"]: f for f in fields}
        self.assertIn("Full Name", labels)
        self.assertEqual(labels["Tagline"]["max_length"], 5)

    def test_normalize_label_matches_case_insensitively_but_exactly(self):
        self.assertEqual(be._normalize_label("  Full   Name "),
                         be._normalize_label("full name"))
        self.assertNotEqual(be._normalize_label("Name"),
                            be._normalize_label("First Name"))

    def test_parse_max_length_variants(self):
        self.assertEqual(be._parse_max_length("Max 50 characters"), 50)
        self.assertEqual(be._parse_max_length("30 chars max"), 30)
        self.assertEqual(be._parse_max_length("limit of 12"), 12)
        self.assertEqual(be._parse_max_length(8), 8)
        self.assertIsNone(be._parse_max_length("no constraint here"))
        self.assertIsNone(be._parse_max_length(None))


if __name__ == "__main__":
    unittest.main()
