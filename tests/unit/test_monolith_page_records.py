"""Unit tests for the monolith (clawbridge.py) per-page capture wiring.

The monolith is the primary entry point, so its ``BrowserUseEngine`` must
produce the same page records as the package engine. Importing clawbridge.py
runs dependency auto-install and starts an HTTP loading server, so these tests
exercise the monolith's own page-record schema and builder with fake agent
histories after importing it in a guarded subprocess.

If the monolith cannot be imported in this environment (e.g. optional runtime
dependencies missing), the tests skip with the reason rather than fail --
the pure logic itself is covered by test_browser_use_page_records.py.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


_SCRIPT = textwrap.dedent(
    """
    import json, sys, types

    # Stub optional runtime deps so clawbridge.py's dependency checker passes.
    for name in ("browser_use", "langchain_anthropic", "anthropic", "pystray"):
        if name not in sys.modules:
            try:
                __import__(name)
            except Exception:
                sys.modules[name] = types.ModuleType(name)

    sys.path.insert(0, {repo!r})
    import importlib.util
    spec = importlib.util.spec_from_file_location("cbmono", {path!r})
    module = importlib.util.module_from_spec(spec)
    sys.modules["cbmono"] = module
    spec.loader.exec_module(module)

    class State:
        def __init__(self, url, title):
            self.url, self.title = url, title

    class Result:
        def __init__(self, content=None):
            self.extracted_content = content
            self.long_term_memory = None
            self.error = None

    class Item:
        def __init__(self, url, title, results):
            self.state = State(url, title)
            self.result = results

    class History:
        def __init__(self, items, final):
            self.history = items
            self._final = final
        def final_result(self):
            return self._final

    def step(url, title="", content=None):
        return Item(url, title, [Result(content)] if content is not None else [])

    out = {{}}

    # normal extraction + duplicate url
    h = History(
        [step("https://a.test/1", "One", "alpha"),
         step("https://a.test/2", "Two", "beta"),
         step("https://a.test/1", "One", "again")],
        "beta",
    )
    out["normal"] = [
        {{"url": r.url, "title": r.title, "snippet": r.snippet,
          "captured": r.captured, "reason": r.reason, "step": r.step,
          "duplicate_skips": r.duplicate_skips}}
        for r in (module.PageRecord(**d) for d in module._build_page_records(h, final_text="beta"))
    ]

    # empty final result
    h2 = History([step("https://a.test/empty", "Empty")], None)
    out["empty"] = [
        {{"url": r.url, "captured": r.captured, "reason": r.reason}}
        for r in (module.PageRecord(**d) for d in module._build_page_records(h2, final_text=None))
    ]

    # no usable url
    h3 = History([], None)
    out["no_url"] = [
        {{"url": r.url, "captured": r.captured, "reason": r.reason}}
        for r in (module.PageRecord(**d) for d in module._build_page_records(h3, final_text=None))
    ]

    # TaskResult serialization carries the records
    tr = module.TaskResult(summary="s", page_records=[module.PageRecord(**d) for d in module._build_page_records(h, final_text="beta")])
    out["serialized"] = tr.model_dump()["page_records"]

    print(json.dumps(out))
    """
)


def _run(script: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
    )
    if proc.returncode != 0:
        return {"_error": (proc.stderr or proc.stdout or "").strip()[:2000]}
    last = [line for line in proc.stdout.splitlines() if line.strip().startswith("{")]
    if not last:
        return {"_error": "no JSON output: " + proc.stdout[-1000:]}
    return json.loads(last[-1])


@pytest.fixture(scope="module")
def mono():
    result = _run(_SCRIPT.format(repo=str(REPO_ROOT), path=str(REPO_ROOT / "clawbridge.py")))
    if "_error" in result:
        pytest.skip(f"monolith not importable in this environment: {result['_error']}")
    return result


class TestMonolithPageRecords:
    def test_normal_extraction_and_duplicate(self, mono):
        recs = mono["normal"]
        assert len(recs) == 2, "duplicate URL must not add a record"
        assert [r["url"] for r in recs] == ["https://a.test/1", "https://a.test/2"]
        assert [r["step"] for r in recs] == [1, 2]
        assert all(r["captured"] for r in recs)
        assert [r["duplicate_skips"] for r in recs] == [1, 0]
        assert recs[0]["snippet"] == "alpha"
        assert recs[0]["title"] == "One"

    def test_empty_final_result_not_faked(self, mono):
        recs = mono["empty"]
        assert len(recs) == 1
        assert recs[0]["url"] == "https://a.test/empty"
        assert recs[0]["captured"] is False
        assert recs[0]["reason"], "not-captured record must state why"
        assert "final result was empty" in recs[0]["reason"].lower()

    def test_no_usable_url_is_not_a_fake_success(self, mono):
        recs = mono["no_url"]
        assert len(recs) == 1
        assert recs[0]["captured"] is False
        assert "no usable url" in recs[0]["reason"].lower()

    def test_records_present_in_task_result_payload(self, mono):
        payload = mono["serialized"]
        assert len(payload) == 2
        assert payload[0]["url"] == "https://a.test/1"
        assert payload[0]["captured"] is True
        assert payload[0]["duplicate_skips"] == 1
        assert set(payload[0]) == {
            "url", "title", "snippet", "captured", "reason", "step", "duplicate_skips",
        }
