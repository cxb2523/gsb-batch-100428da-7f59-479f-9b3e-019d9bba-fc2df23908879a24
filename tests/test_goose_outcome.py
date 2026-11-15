"""Offline tests for the shared Goose agent outcome pipeline.

These tests never invoke goose, docker or ollama and never touch the network:
the Goose process is faked through scripted AttemptResult objects / monkeypatched
subprocess calls. Run them with:

    python -I -m unittest tests.test_goose_outcome -v
"""

import os
import sys
import tempfile
import unittest

# Make automation-resources importable regardless of the current working dir.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTOMATION_DIR = os.path.join(REPO_ROOT, "automation-resources")
if AUTOMATION_DIR not in sys.path:
    sys.path.insert(0, AUTOMATION_DIR)

import goose_outcome  # noqa: E402
from goose_outcome import (  # noqa: E402
    KNOWN_ERROR,
    RETRY_EXHAUSTED,
    SUCCESS,
    AgentOutcome,
    AttemptResult,
    build_non_clobbering_path,
    classify_attempt,
    detect_known_errors,
    outcome_to_dict,
    run_agent_with_retry,
    write_local_agent_report_file,
    write_outcome_file,
)
import AIAgentLibrary  # noqa: E402


class ScriptedRunner:
    """Fake Goose process: returns scripted attempts in order, records sleeps."""

    def __init__(self, attempts):
        self.attempts = list(attempts)
        self.calls = 0
        self.sleeps = []

    def __call__(self):
        self.sleeps  # noqa: B018 - attribute exists for assertions
        index = min(self.calls, len(self.attempts) - 1)
        self.calls += 1
        return self.attempts[index]

    def sleep(self, seconds):
        self.sleeps.append(seconds)


def runner_for(*attempts):
    scripted = ScriptedRunner(attempts)
    return scripted, lambda: scripted()


class DetectKnownErrorsTests(unittest.TestCase):
    def test_clean_output_has_no_markers(self):
        self.assertEqual(detect_known_errors("Edgar Allan Poe was an American writer."), [])

    def test_empty_output_has_no_markers(self):
        self.assertEqual(detect_known_errors(""), [])
        self.assertEqual(detect_known_errors(None), [])

    def test_finds_rate_limit_marker(self):
        markers = detect_known_errors("Rate limit hit. Retrying in 4s")
        self.assertIn("Rate limit hit", markers)

    def test_finds_truncation_and_missing_tool_markers(self):
        output = (
            "thread 'main' panicked at crates/goose/src/agents/truncate.rs:88:9\n"
            "Tool not found: remember_memory\n"
        )
        markers = detect_known_errors(output)
        self.assertIn("crates/goose/src/agents/truncate.rs", markers)
        self.assertIn("Tool not found", markers)
        self.assertIn("remember_memory", markers)

    def test_markers_match_in_stderr_too(self):
        attempt = AttemptResult(stdout="partial output", stderr="400 Bad Request from provider")
        self.assertEqual(classify_attempt(attempt), KNOWN_ERROR)


class RunAgentWithRetryTests(unittest.TestCase):
    def test_success_on_first_attempt_keeps_output(self):
        scripted, runner = runner_for(AttemptResult(stdout="clean answer", returncode=0))
        outcome = run_agent_with_retry(
            runner, retries=3, retry_interval=0, sleep_fn=scripted.sleep
        )
        self.assertEqual(outcome.status, SUCCESS)
        self.assertEqual(outcome.output, "clean answer")
        self.assertEqual(scripted.calls, 1)
        self.assertEqual(outcome.attempts[0].returncode, 0)

    def test_success_after_transient_known_error_still_succeeds(self):
        scripted, runner = runner_for(
            AttemptResult(stdout="Rate limit hit. Retrying in 4s", returncode=0),
            AttemptResult(stdout="Rate limit hit. Retrying in 6s", returncode=0),
            AttemptResult(stdout="final clean answer", returncode=0),
        )
        outcome = run_agent_with_retry(
            runner, retries=5, retry_interval=0.25, sleep_fn=scripted.sleep
        )
        self.assertEqual(outcome.status, SUCCESS)
        self.assertEqual(outcome.output, "final clean answer")
        self.assertEqual(scripted.calls, 3)
        # The configured interval is honoured between attempts, not after the last.
        self.assertEqual(scripted.sleeps, [0.25, 0.25])

    def test_known_error_after_retry_budget_exhausted_is_classified(self):
        scripted, runner = runner_for(
            AttemptResult(stdout="Rate limit hit. Retrying in 4s", returncode=0),
            AttemptResult(stdout="Rate limit hit. Retrying in 10s", returncode=0),
        )
        outcome = run_agent_with_retry(
            runner, retries=2, retry_interval=0, sleep_fn=scripted.sleep
        )
        self.assertEqual(outcome.status, KNOWN_ERROR)
        self.assertIn("Rate limit hit", outcome.markers)
        self.assertIn("Rate limit hit", outcome.reason)
        # The raw output of the final attempt is preserved.
        self.assertEqual(outcome.output, "Rate limit hit. Retrying in 10s")
        self.assertEqual(scripted.calls, 2)

    def test_retry_exhausted_on_nonzero_exit_without_error_marker(self):
        scripted, runner = runner_for(
            AttemptResult(stdout="", stderr="docker exec failed", returncode=125),
        )
        outcome = run_agent_with_retry(
            runner, retries=3, retry_interval=0, sleep_fn=scripted.sleep
        )
        self.assertEqual(outcome.status, RETRY_EXHAUSTED)
        self.assertEqual(outcome.output, "")
        self.assertEqual(outcome.stderr, "docker exec failed")
        self.assertIn("return code 125", outcome.reason)
        self.assertEqual(scripted.calls, 3)

    def test_retry_exhausted_on_timeout_keeps_partial_output(self):
        partial = AttemptResult(
            stdout="work in progress before the hang",
            stderr="",
            returncode=None,
            timed_out=True,
        )
        scripted, runner = runner_for(partial)
        outcome = run_agent_with_retry(
            runner,
            retries=2,
            retry_interval=0,
            call_timeout=240,
            sleep_fn=scripted.sleep,
        )
        self.assertEqual(outcome.status, RETRY_EXHAUSTED)
        self.assertIn("timed out", outcome.reason)
        self.assertEqual(outcome.output, "work in progress before the hang")
        self.assertTrue(outcome.attempts[-1].timed_out)

    def test_retry_exhausted_on_missing_process(self):
        scripted, runner = runner_for(
            AttemptResult(error="failed to start Goose process: [WinError 2] file not found"),
        )
        outcome = run_agent_with_retry(
            runner, retries=2, retry_interval=0, sleep_fn=scripted.sleep
        )
        self.assertEqual(outcome.status, RETRY_EXHAUSTED)
        self.assertIn("could not be run", outcome.reason)

    def test_retry_exhausted_on_empty_output(self):
        scripted, runner = runner_for(
            AttemptResult(stdout="", stderr="", returncode=0),
        )
        outcome = run_agent_with_retry(
            runner, retries=1, retry_interval=0, sleep_fn=scripted.sleep
        )
        # Empty output is not a verifiable success: the exhausted budget is
        # reported as RETRY_EXHAUSTED and the (empty) raw output is preserved.
        self.assertEqual(outcome.status, RETRY_EXHAUSTED)
        self.assertEqual(outcome.output, "")

    def test_known_error_from_any_attempt_surfaces_when_last_is_blank(self):
        # Even if the very last attempt is blank, an error marker seen earlier
        # must not be silently relabelled as retry-exhausted.
        scripted, runner = runner_for(
            AttemptResult(stdout="ERROR: something broke", returncode=1),
            AttemptResult(stdout="", stderr="", returncode=0),
        )
        outcome = run_agent_with_retry(
            runner, retries=2, retry_interval=0, sleep_fn=scripted.sleep
        )
        self.assertEqual(outcome.status, KNOWN_ERROR)
        self.assertIn("ERROR", outcome.markers)

    def test_outcome_dict_is_robot_framework_friendly(self):
        scripted, runner = runner_for(
            AttemptResult(stdout="Rate limit hit", returncode=0),
        )
        outcome = run_agent_with_retry(
            runner, retries=1, retry_interval=0, route="docker",
            report_stem="docker-agent1-report1", sleep_fn=scripted.sleep,
        )
        data = outcome_to_dict(outcome)
        self.assertEqual(data["status"], KNOWN_ERROR)
        self.assertEqual(data["route"], "docker")
        self.assertEqual(data["report_stem"], "docker-agent1-report1")
        self.assertEqual(data["attempt_count"], 1)
        self.assertIn("Rate limit hit", data["attempts"][0]["markers"])
        self.assertTrue(data["attempts"][0]["timed_out"] is False)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="goose-outcome-test-")

    def test_outcome_file_persists_raw_output_and_reason(self):
        outcome = AgentOutcome(
            status=KNOWN_ERROR,
            reason="Known Goose error signature(s) found: Rate limit hit",
            output="partial stdout with Rate limit hit inside",
            stderr="",
            attempts=[AttemptResult(stdout="partial stdout with Rate limit hit inside")],
            markers=["Rate limit hit"],
            report_stem="local-agent-report2",
            route="local",
            started_at="2026-11-15T10:00:00",
            finished_at="2026-11-15T10:05:00",
            retries_configured=100,
            call_timeout=240,
        )
        path = write_outcome_file(outcome, self.temp_dir, "local-agent-report2", "TS")
        self.assertTrue(os.path.exists(path))
        self.assertTrue(os.path.basename(path).startswith("local-agent-report2-outcome-TS"))
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn("KNOWN_ERROR", content)
        self.assertIn("Rate limit hit", content)
        self.assertIn("partial stdout with Rate limit hit inside", content)
        self.assertIn("## Attempt history", content)

    def test_empty_output_is_recorded_as_empty_not_crashed(self):
        outcome = AgentOutcome(
            status=RETRY_EXHAUSTED,
            reason="no usable Goose output",
            output="",
            stderr="",
            attempts=[AttemptResult(stdout="", returncode=1)],
            report_stem="docker-agent2-report3",
            route="docker",
            retries_configured=100,
        )
        path = write_outcome_file(outcome, self.temp_dir, "docker-agent2-report3", "TS")
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn("(empty)", content)
        self.assertIn("RETRY_EXHAUSTED", content)

    def test_existing_files_are_never_overwritten(self):
        first = build_non_clobbering_path(self.temp_dir, "local-agent-report1-TS.md")
        with open(first, "w", encoding="utf-8") as handle:
            handle.write("original")
        second = build_non_clobbering_path(self.temp_dir, "local-agent-report1-TS.md")
        self.assertNotEqual(first, second)
        with open(second, "w", encoding="utf-8") as handle:
            handle.write("new")
        with open(first, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "original")

    def test_report_and_outcome_use_distinct_non_clobbering_names(self):
        report_path = write_local_agent_report_file(
            self.temp_dir, 7, "TS", "\x1b[31mred but clean\x1b[0m output"
        )
        self.assertEqual(os.path.basename(report_path),
                         "local-agent-report7-TS.md")
        # ANSI sequences are stripped from reports.
        with open(report_path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "red but clean output")
        outcome = AgentOutcome(
            status=RETRY_EXHAUSTED,
            reason="boom",
            output="x",
            attempts=[AttemptResult(stdout="x", error="boom")],
            report_stem="local-agent-report7",
            route="local",
            retries_configured=100,
        )
        outcome_path = write_outcome_file(outcome, self.temp_dir, "local-agent-report7", "TS")
        self.assertNotEqual(report_path, outcome_path)
        self.assertIn("-outcome-", outcome_path)


class AIAgentLibraryIntegrationTests(unittest.TestCase):
    """Drive the real library keyword with a faked subprocess module."""

    def setUp(self):
        self.library = AIAgentLibrary.AIAgentLibrary(
            default_retries=3, default_retry_interval=0, default_call_timeout=5
        )
        self.temp_dir = tempfile.mkdtemp(prefix="goose-library-test-")

    def _fake_popen_factory(self, scripted_processes):
        queue = list(scripted_processes)

        class FakePopen:
            def __init__(self, *args, **kwargs):
                self.process = queue.pop(0)
                self.returncode = self.process.returncode

            def communicate(self, timeout=None):
                return self.process.communicate()

            def kill(self):
                self.process.killed = True

        return FakePopen

    def test_local_prompt_success_path_through_library(self):
        class FakeProcess:
            returncode = 0
            killed = False

            def communicate(self, timeout=None):
                return "clean local answer", ""

        original_popen = AIAgentLibrary.subprocess.Popen
        AIAgentLibrary.subprocess.Popen = self._fake_popen_factory([FakeProcess()])
        try:
            outcome = self.library.collect_local_ai_agent_prompt_outcome(
                "Who was Poe?", "local-agent-report1"
            )
        finally:
            AIAgentLibrary.subprocess.Popen = original_popen
        self.assertEqual(outcome["status"], SUCCESS)
        self.assertEqual(outcome["output"], "clean local answer")
        self.assertEqual(outcome["attempt_count"], 1)

    def test_docker_known_error_path_persists_outcome_file(self):
        class FakeProcess:
            returncode = 0
            killed = False

            def communicate(self, timeout=None):
                return "Rate limit hit. Retrying in 4s", ""

        original_popen = AIAgentLibrary.subprocess.Popen
        AIAgentLibrary.subprocess.Popen = self._fake_popen_factory(
            [FakeProcess() for _ in range(3)]
        )
        try:
            outcome = self.library.collect_docker_ai_agent_outcome(
                "docker-goose-cli-1", "prompt", "ext1", "ext2", "docker-agent1-report3"
            )
            path = self.library.persist_agent_outcome_file(
                outcome, self.temp_dir, "docker-agent1-report3", "TS"
            )
        finally:
            AIAgentLibrary.subprocess.Popen = original_popen
        self.assertEqual(outcome["status"], KNOWN_ERROR)
        self.assertEqual(outcome["attempt_count"], 3)
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as handle:
            self.assertIn("Rate limit hit", handle.read())

    def test_docker_timeout_path_is_retry_exhausted_with_partial_output(self):
        class TimeoutProcess:
            def __init__(self):
                self.calls = 0
                self.returncode = None
                self.killed = False

            def communicate(self, timeout=None):
                self.calls += 1
                if self.calls == 1:
                    raise AIAgentLibrary.subprocess.TimeoutExpired(cmd="goose", timeout=5)
                self.returncode = -15
                return "partial docker output", ""

            def kill(self):
                self.killed = True
                self.returncode = -9

        original_popen = AIAgentLibrary.subprocess.Popen
        AIAgentLibrary.subprocess.Popen = self._fake_popen_factory(
            [TimeoutProcess() for _ in range(3)]
        )
        try:
            outcome = self.library.collect_docker_ai_agent_outcome(
                "docker-goose-cli-1", "prompt", "ext1", "ext2", "docker-agent1-report4"
            )
        finally:
            AIAgentLibrary.subprocess.Popen = original_popen
        self.assertEqual(outcome["status"], RETRY_EXHAUSTED)
        self.assertTrue(outcome["attempts"][0]["timed_out"])
        self.assertEqual(outcome["output"], "partial docker output")
        self.assertIn("timed out", outcome["reason"])

    def test_legacy_single_call_keywords_return_stdout_without_raising(self):
        class FakeProcess:
            returncode = 1
            killed = False

            def communicate(self, timeout=None):
                return "some stdout", "some stderr"

        original_popen = AIAgentLibrary.subprocess.Popen
        AIAgentLibrary.subprocess.Popen = self._fake_popen_factory([FakeProcess()])
        try:
            stdout = self.library.send_docker_ai_agent_prompt_message(
                "container", "prompt", "ext1", "ext2"
            )
        finally:
            AIAgentLibrary.subprocess.Popen = original_popen
        self.assertEqual(stdout, "some stdout")


if __name__ == "__main__":
    unittest.main()
