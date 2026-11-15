"""Tests for per-competitor collection outcomes in the Tavily/CocoIndex path.

All searches are served by in-memory fake Tavily responses; no network calls
and no real API key are used.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import collection_outcomes

try:
    import main as pipeline_main
except Exception as exc:  # pragma: no cover - only when cocoindex is absent
    pipeline_main = None
    _IMPORT_ERROR = exc

import mcp_server


# Captured before any test patches ``subprocess.run`` (``mcp_server.subprocess``
# is the same module object), so the cross-process test can launch a real child.
_REAL_SUBPROCESS_RUN = subprocess.run


MISSING_DATE_RESULT = {
    "url": "https://news.example.com/alpha-hire",
    "title": "Alpha hires a new CTO",
    "content": "Alpha announced a key hire.",
    "raw_content": "Alpha announced a key hire this week.",
    "domain": "news.example.com",
    "score": 0.9,
    # published_date deliberately absent
}

DATED_RESULT = {
    "url": "https://news.example.com/alpha-funding",
    "title": "Alpha raises Series B",
    "content": "Alpha raised a new round.",
    "raw_content": "Alpha raised a $50M Series B round.",
    "domain": "news.example.com",
    "published_date": "2026-11-01T12:00:00",
    "score": 0.8,
}


class _FakeTavilyClient:
    """Minimal stand-in for ``tavily.TavilyClient`` driven by a URL/competitor map."""

    responses: dict[str, object] = {}
    calls: list[str] = []

    def __init__(self, api_key: str):
        self.api_key = api_key

    def search(self, query: str, **kwargs):
        _FakeTavilyClient.calls.append(query)
        for competitor, response in _FakeTavilyClient.responses.items():
            if query.startswith(competitor + " AND"):
                if isinstance(response, BaseException):
                    raise response
                return response
        raise AssertionError(f"unexpected query: {query}")


@unittest.skipIf(pipeline_main is None, "cocoindex is required for connector tests")
class TavilyConnectorOutcomeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.outcome_dir = Path(self._tmp.name) / "run-1"
        self.outcome_dir.mkdir()

        _FakeTavilyClient.responses = {
            "Alpha": {"results": [DATED_RESULT, MISSING_DATE_RESULT]},
            "Beta": RuntimeError("tavily request timed out after 30s"),
            "Gamma": {"results": []},
        }
        _FakeTavilyClient.calls = []

        patcher = mock.patch.object(pipeline_main, "TavilyClient", _FakeTavilyClient)
        patcher.start()
        self.addCleanup(patcher.stop)
        env_patcher = mock.patch.dict(
            "os.environ",
            {"TAVILY_API_KEY": "test-key", collection_outcomes.ENV_OUTCOME_DIR: str(self.outcome_dir)},
        )
        env_patcher.start()
        self.addCleanup(env_patcher.stop)

    async def _collect(self, competitor: str):
        spec = pipeline_main.TavilySearchSource(competitor=competitor, max_results=5)
        connector = await pipeline_main.TavilySearchConnector.create(spec)
        return connector, [row async for row in connector.list()]

    async def test_mixed_outcomes_keep_successful_competitor_data(self):
        alpha, alpha_rows = await self._collect("Alpha")
        beta, beta_rows = await self._collect("Beta")
        gamma, gamma_rows = await self._collect("Gamma")

        # The failing and empty competitors do not raise and yield nothing.
        self.assertEqual(beta_rows, [])
        self.assertEqual(gamma_rows, [])

        # The healthy competitor's articles still come through.
        urls = {row.key.url for row in alpha_rows}
        self.assertEqual(urls, {DATED_RESULT["url"], MISSING_DATE_RESULT["url"]})

        # A missing published_date degrades to NO_ORDINAL but is still ingested.
        ordinal_by_url = {row.key.url: row.data.ordinal for row in alpha_rows}
        self.assertEqual(ordinal_by_url[MISSING_DATE_RESULT["url"]], "NO_ORDINAL")
        self.assertIsInstance(ordinal_by_url[DATED_RESULT["url"]], int)
        self.assertEqual(alpha_rows[0].data.value.title, DATED_RESULT["title"])

    async def test_three_distinct_outcomes_are_recorded(self):
        await self._collect("Alpha")
        await self._collect("Beta")
        await self._collect("Gamma")

        outcomes = collection_outcomes.merge_with_expected(
            collection_outcomes.read_outcomes(self.outcome_dir),
            ["Alpha", "Beta", "Gamma"],
        )
        by_competitor = {outcome["competitor"]: outcome for outcome in outcomes}

        self.assertEqual(by_competitor["Alpha"]["status"], collection_outcomes.STATUS_COLLECTED)
        self.assertEqual(by_competitor["Alpha"]["result_count"], 2)
        self.assertEqual(by_competitor["Alpha"]["missing_published_date_count"], 1)

        self.assertEqual(by_competitor["Beta"]["status"], collection_outcomes.STATUS_FAILED)
        self.assertIn("timed out", by_competitor["Beta"]["failure_reason"])
        self.assertEqual(by_competitor["Beta"]["error_type"], "RuntimeError")

        self.assertEqual(by_competitor["Gamma"]["status"], collection_outcomes.STATUS_EMPTY)
        self.assertEqual(by_competitor["Gamma"]["result_count"], 0)
        self.assertIsNone(by_competitor["Gamma"]["failure_reason"])

        summary = collection_outcomes.summarize_outcomes(outcomes)
        self.assertEqual(summary["collected"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["empty"], 1)
        self.assertEqual(summary["failed_competitors"], ["Beta"])

    async def test_outcome_file_is_the_agreed_path(self):
        await self._collect("Beta")

        agreed = self.outcome_dir / collection_outcomes.AGREED_OUTCOME_FILE
        self.assertTrue(agreed.exists())
        payload = json.loads(agreed.read_text(encoding="utf-8"))
        self.assertEqual(payload["competitors"][0]["competitor"], "Beta")
        self.assertEqual(payload["competitors"][0]["status"], "failed")

    async def test_missing_published_date_record_is_queryable(self):
        alpha, _ = await self._collect("Alpha")
        # Simulate the engine skipping list() values by asking for one URL.
        value = await alpha.get_value(pipeline_main._ArticleKey(url=MISSING_DATE_RESULT["url"]))
        self.assertEqual(value.value.title, MISSING_DATE_RESULT["title"])
        self.assertEqual(value.value.source, "news.example.com")
        self.assertEqual(value.ordinal, "NO_ORDINAL")

    async def test_get_value_failure_does_not_raise_or_lose_list_outcome(self):
        alpha, _ = await self._collect("Alpha")
        # A brand-new connector has no cached values, so it must search again.
        spec = pipeline_main.TavilySearchSource(competitor="Beta", max_results=5)
        beta = await pipeline_main.TavilySearchConnector.create(spec)
        beta._articles.clear()

        value = await beta.get_value(pipeline_main._ArticleKey(url="https://x.example.com"))
        self.assertEqual(value.value, "NON_EXISTENCE")

    async def test_malformed_published_date_does_not_fail_the_competitor(self):
        _FakeTavilyClient.responses["Alpha"] = {
            "results": [{"url": "https://n.example.com/a", "title": "A", "published_date": "not-a-date"}]
        }
        _, rows = await self._collect("Alpha")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].data.ordinal, "NO_ORDINAL")
        outcome = collection_outcomes.read_outcomes(self.outcome_dir)[0]
        self.assertEqual(outcome["status"], collection_outcomes.STATUS_COLLECTED)


class ParentOutcomeMergeTests(unittest.TestCase):
    """The parent only sees returncode/stdout/stderr, so it reads the file."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.outcome_dir = Path(self._tmp.name) / "run-parent"
        self.outcome_dir.mkdir()

    def _fake_child(self, returncode: int = 0):
        def run(command, **kwargs):
            env = kwargs["env"]
            target = env[collection_outcomes.ENV_OUTCOME_DIR]
            collection_outcomes.write_outcome(
                "Alpha",
                collection_outcomes.STATUS_COLLECTED,
                result_count=2,
                missing_published_date_count=1,
                directory=target,
            )
            collection_outcomes.write_outcome(
                "Beta",
                collection_outcomes.STATUS_FAILED,
                failure_reason=RuntimeError("boom: 503 from Tavily"),
                error_type="RuntimeError",
                directory=target,
            )
            return mock.Mock(returncode=returncode, stdout="updated", stderr="")

        return run

    def test_parent_reads_competitor_outcomes_from_result_file(self):
        env = {
            "COCOINDEX_DATABASE_URL": "postgresql://example/db",
            "TAVILY_API_KEY": "test-key",
            "OPENAI_API_KEY": "test-key",
            collection_outcomes.ENV_OUTCOME_DIR: str(self.outcome_dir),
        }
        with mock.patch.dict("os.environ", env, clear=False):
            with mock.patch.object(
                mcp_server.subprocess, "run", side_effect=self._fake_child(returncode=0)
            ):
                result = mcp_server.run_cocoindex_update(
                    live=True,
                    competitors="Alpha,Beta,Gamma",
                    event_query="(funding OR launch)",
                    max_results=2,
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["partial"])
        self.assertEqual(result["failed_competitors"], ["Beta"])
        self.assertEqual(result["empty_competitors"], [])
        self.assertEqual(result["not_run_competitors"], ["Gamma"])

        self.assertEqual(result["effective_config"]["competitors"], "Alpha,Beta,Gamma")
        self.assertEqual(result["effective_config"]["max_results_per_competitor"], "2")

        by_competitor = {row["competitor"]: row for row in result["competitors"]}
        self.assertEqual([row["competitor"] for row in result["competitors"]][:3], ["Alpha", "Beta", "Gamma"])
        self.assertEqual(by_competitor["Alpha"]["status"], "collected")
        self.assertEqual(by_competitor["Beta"]["status"], "failed")
        self.assertIn("503", by_competitor["Beta"]["failure_reason"])
        self.assertEqual(by_competitor["Gamma"]["status"], "not_run")

        self.assertEqual(result["collection_summary"]["collected"], 1)
        self.assertEqual(result["collection_summary"]["failed"], 1)
        self.assertFalse(result["collection_complete"])

        outcome_file = Path(result["outcome_file"])
        self.assertTrue(outcome_file.exists())
        self.assertEqual(outcome_file.name, collection_outcomes.AGREED_OUTCOME_FILE)
        persisted = json.loads(outcome_file.read_text(encoding="utf-8"))
        self.assertEqual(persisted["summary"]["failed"], 1)

    def test_nonzero_returncode_is_still_reported_as_failure(self):
        def fake_run(command, **kwargs):
            return mock.Mock(returncode=2, stdout="", stderr="cocoindex crashed")

        env = {
            "COCOINDEX_DATABASE_URL": "postgresql://example/db",
            "TAVILY_API_KEY": "test-key",
            "OPENAI_API_KEY": "test-key",
            collection_outcomes.ENV_OUTCOME_DIR: str(self.outcome_dir),
        }
        with mock.patch.dict("os.environ", env, clear=False):
            with mock.patch.object(mcp_server.subprocess, "run", side_effect=fake_run):
                result = mcp_server.run_cocoindex_update(live=True, competitors="Alpha,Beta")

        self.assertFalse(result["ok"])
        self.assertEqual(result["returncode"], 2)
        self.assertEqual(result["status"], "failed")
        self.assertIn("cocoindex crashed", result["stderr"])
        # No fabricated events: competitors that never recorded anything are not_run.
        self.assertEqual(result["not_run_competitors"], ["Alpha", "Beta"])
        self.assertEqual(result["collection_summary"]["collected"], 0)

    def test_partial_failure_is_visible_and_other_competitors_survive(self):
        env = {
            "COCOINDEX_DATABASE_URL": "postgresql://example/db",
            "TAVILY_API_KEY": "test-key",
            "OPENAI_API_KEY": "test-key",
            collection_outcomes.ENV_OUTCOME_DIR: str(self.runs_root()),
        }
        with mock.patch.dict("os.environ", env, clear=False):
            with mock.patch.object(
                mcp_server.subprocess, "run", side_effect=self._fake_child(returncode=0)
            ):
                result = mcp_server.run_cocoindex_update(
                    live=True, competitors="Alpha,Beta,Gamma"
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "partial")
        self.assertIn("Beta", result["message"])
        self.assertEqual(result["collection_summary"]["collected"], 1)

    def test_all_competitors_empty_is_not_reported_as_success(self):
        def fake_run(command, **kwargs):
            target = kwargs["env"][collection_outcomes.ENV_OUTCOME_DIR]
            for name in ("Alpha", "Beta"):
                collection_outcomes.write_outcome(
                    name, collection_outcomes.STATUS_EMPTY, directory=target
                )
            return mock.Mock(returncode=0, stdout="", stderr="")

        env = {
            "COCOINDEX_DATABASE_URL": "postgresql://example/db",
            "TAVILY_API_KEY": "test-key",
            "OPENAI_API_KEY": "test-key",
            collection_outcomes.ENV_OUTCOME_DIR: str(self.runs_root()),
        }
        with mock.patch.dict("os.environ", env, clear=False):
            with mock.patch.object(mcp_server.subprocess, "run", side_effect=fake_run):
                result = mcp_server.run_cocoindex_update(live=True, competitors="Alpha,Beta")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "empty")
        self.assertFalse(result["partial"])
        self.assertEqual(result["empty_competitors"], ["Alpha", "Beta"])

    def runs_root(self) -> Path:
        """A dedicated root for run directories inside this test's temp dir."""
        root = Path(self._tmp.name) / "runs"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def test_guarded_mode_unchanged_without_live(self):
        result = mcp_server.run_cocoindex_update(live=False, competitors="Alpha,Beta")

        self.assertFalse(result["ok"])
        self.assertEqual(result["effective_config"]["competitors"], "Alpha,Beta")
        self.assertNotIn("competitors", result)


class CrossProcessOutcomeTests(unittest.TestCase):
    """The file contract is exercised across a real process boundary."""

    CHILD_SCRIPT = textwrap.dedent(
        """
        import json, os, sys
        sys.path.insert(0, os.environ["CI_REPO_DIR"])
        import collection_outcomes

        target = os.environ[collection_outcomes.ENV_OUTCOME_DIR]
        collection_outcomes.write_outcome(
            "Alpha",
            collection_outcomes.STATUS_COLLECTED,
            result_count=3,
            missing_published_date_count=1,
            directory=target,
        )
        collection_outcomes.write_outcome(
            "Beta",
            collection_outcomes.STATUS_FAILED,
            failure_reason=RuntimeError("Tavily request failed: 503 Service Unavailable"),
            error_type="RuntimeError",
            directory=target,
        )
        collection_outcomes.write_outcome(
            "Gamma",
            collection_outcomes.STATUS_EMPTY,
            directory=target,
        )
        print(json.dumps({"wrote": ["Alpha", "Beta", "Gamma"]}))
        """
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.script = self.root / "child_collector.py"
        self.script.write_text(self.CHILD_SCRIPT, encoding="utf-8")
        self.repo_dir = str(Path(__file__).resolve().parent)

    def test_parent_reads_outcomes_written_by_a_separate_process(self):
        def run_child(command, **kwargs):
            env = dict(kwargs["env"])
            env["CI_REPO_DIR"] = self.repo_dir
            completed = _REAL_SUBPROCESS_RUN(
                [sys.executable, str(self.script)],
                capture_output=True,
                text=True,
                env=env,
                cwd=self.repo_dir,
            )
            return subprocess.CompletedProcess(
                args=command,
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )

        env = {
            "COCOINDEX_DATABASE_URL": "postgresql://example/db",
            "TAVILY_API_KEY": "test-key",
            "OPENAI_API_KEY": "test-key",
            collection_outcomes.ENV_OUTCOME_DIR: str(self.root / "runs"),
        }
        with mock.patch.dict("os.environ", env, clear=False):
            with mock.patch.object(mcp_server.subprocess, "run", side_effect=run_child):
                result = mcp_server.run_cocoindex_update(
                    live=True, competitors="Alpha,Beta,Gamma,Delta"
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["failed_competitors"], ["Beta"])
        self.assertEqual(result["empty_competitors"], ["Gamma"])
        self.assertEqual(result["not_run_competitors"], ["Delta"])

        by_competitor = {row["competitor"]: row for row in result["competitors"]}
        self.assertEqual(by_competitor["Alpha"]["result_count"], 3)
        self.assertEqual(by_competitor["Alpha"]["missing_published_date_count"], 1)
        self.assertIn("503", by_competitor["Beta"]["failure_reason"])
        self.assertEqual(by_competitor["Gamma"]["failure_reason"], None)

        # Each run gets its own directory under the configured root, so a
        # later run cannot read an earlier run's records as its own.
        self.assertEqual(Path(result["outcome_dir"]).parent, self.root / "runs")
        self.assertTrue(Path(result["outcome_file"]).exists())


class OutcomeFileRobustnessTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.directory = Path(self._tmp.name) / "run"
        self.directory.mkdir()

    def test_missing_published_date_is_not_a_failure(self):
        record = collection_outcomes.write_outcome(
            "Alpha",
            collection_outcomes.STATUS_COLLECTED,
            result_count=2,
            missing_published_date_count=2,
            directory=self.directory,
        )
        payload = json.loads(record.read_text(encoding="utf-8"))
        stored = payload["competitors"][0]
        self.assertEqual(stored["status"], "collected")
        self.assertEqual(stored["missing_published_date_count"], 2)
        self.assertIsNone(stored["failure_reason"])

    def test_corrupt_file_does_not_break_reading(self):
        target = self.directory / collection_outcomes.AGREED_OUTCOME_FILE
        target.write_text("{not valid json", encoding="utf-8")

        self.assertEqual(collection_outcomes.read_outcomes(self.directory), [])

        collection_outcomes.write_outcome(
            "Alpha", collection_outcomes.STATUS_EMPTY, directory=self.directory
        )
        outcomes = collection_outcomes.read_outcomes(self.directory)
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0]["status"], "empty")

    def test_entries_without_competitor_are_ignored(self):
        target = self.directory / collection_outcomes.AGREED_OUTCOME_FILE
        target.write_text(
            json.dumps({"competitors": [{"status": "collected"}, {"competitor": "Alpha", "status": "empty"}]}),
            encoding="utf-8",
        )
        outcomes = collection_outcomes.read_outcomes(self.directory)
        self.assertEqual([outcome["competitor"] for outcome in outcomes], ["Alpha"])

    def test_get_value_failure_keeps_collected_status(self):
        collection_outcomes.write_outcome(
            "Alpha",
            collection_outcomes.STATUS_COLLECTED,
            result_count=2,
            directory=self.directory,
        )
        collection_outcomes.write_outcome(
            "Alpha",
            collection_outcomes.STATUS_FAILED,
            failure_reason=RuntimeError("lookup failed"),
            phase="get_value",
            directory=self.directory,
        )
        outcome = collection_outcomes.read_outcomes(self.directory)[0]
        self.assertEqual(outcome["status"], "collected")
        self.assertEqual(outcome["result_count"], 2)
        self.assertEqual(outcome["value_error_count"], 1)
        self.assertIsNone(outcome["failure_reason"])
        self.assertIn("lookup failed", outcome["value_failure_reason"])

    def test_reason_is_truncated_and_single_line(self):
        long_reason = "line one\n" + "x" * 2000
        reason = collection_outcomes.summarize_reason(long_reason)
        self.assertNotIn("\n", reason)
        self.assertLessEqual(len(reason), collection_outcomes.MAX_REASON_LENGTH)
        self.assertTrue(reason.endswith("..."))

    def test_unknown_status_is_preserved_not_silently_successful(self):
        target = self.directory / collection_outcomes.AGREED_OUTCOME_FILE
        target.write_text(
            json.dumps({"competitors": [{"competitor": "Alpha", "status": "weird"}]}),
            encoding="utf-8",
        )
        outcomes = collection_outcomes.read_outcomes(self.directory)
        self.assertEqual(outcomes[0]["status"], "weird")
        summary = collection_outcomes.summarize_outcomes(outcomes)
        self.assertEqual(summary["collected"], 0)

    def test_default_location_is_scoped_per_process_run(self):
        # Direct `cocoindex update main` runs set no env var; a shared rolling
        # file would let a previous run's `collected` be read as this round's.
        with mock.patch.dict("os.environ", {}, clear=False):
            os.environ.pop(collection_outcomes.ENV_OUTCOME_DIR, None)
            path = collection_outcomes.outcome_file()
            self.assertEqual(path, collection_outcomes.outcome_file())
            self.assertEqual(path.name, collection_outcomes.AGREED_OUTCOME_FILE)
            self.assertIn("runs", path.parent.parts)

            try:
                collection_outcomes.write_outcome("Alpha", collection_outcomes.STATUS_EMPTY)
                outcomes = collection_outcomes.read_outcomes()
                self.assertEqual(outcomes[0]["competitor"], "Alpha")
                self.assertEqual(outcomes[0]["status"], "empty")
            finally:
                import shutil

                shutil.rmtree(path.parent, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
