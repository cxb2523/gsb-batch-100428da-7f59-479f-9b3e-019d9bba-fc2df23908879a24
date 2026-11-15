import os
import subprocess

try:
    # Normal Robot Framework usage: AIAgentLibrary.py and goose_outcome.py
    # live side by side in automation-resources/.
    from goose_outcome import (
        AgentOutcome,
        AttemptResult,
        KNOWN_ERROR,
        RETRY_EXHAUSTED,
        SUCCESS,
        outcome_to_dict,
        run_agent_with_retry,
        write_local_agent_report_file,
        write_outcome_file,
    )
except ImportError:  # pragma: no cover - exercised by the offline test suite
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from goose_outcome import (
        AgentOutcome,
        AttemptResult,
        KNOWN_ERROR,
        RETRY_EXHAUSTED,
        SUCCESS,
        outcome_to_dict,
        run_agent_with_retry,
        write_local_agent_report_file,
        write_outcome_file,
    )


class AIAgentLibrary:
    """Robot Framework library wrapping the local and containerized Goose agents.

    Every public collection keyword ends in a verifiable outcome produced by
    :mod:`goose_outcome`, so the local (Ollama) path and the Docker
    (``docker exec``) path share one set of success/failure rules. A failed
    call never aborts the run: the raw output is persisted into an outcome
    file alongside the regular Markdown reports.
    """

    def __init__(self, default_retries=100, default_retry_interval=2, default_call_timeout=240):
        # Mirror the existing ${RETRY} / ${DELAY} Robot Framework settings.
        self.default_retries = int(default_retries)
        self.default_retry_interval = float(default_retry_interval)
        # Per-invocation hard cap, mirroring the local suite's
        # ${LLM_TIME_CONSTRAINT} of 240 seconds.
        self.default_call_timeout = float(default_call_timeout)

    # ------------------------------------------------------------------
    # Subprocess runners - one per existing Goose invocation style
    # ------------------------------------------------------------------
    def _run_process(self, command, shell=False, call_timeout=None):
        """Run one Goose process and convert every terminal state into an
        :class:`AttemptResult` instead of raising, so partial output survives."""
        try:
            process = subprocess.Popen(
                command,
                shell=shell,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            return AttemptResult(error="failed to start Goose process: {}".format(exc))
        try:
            stdout, stderr = process.communicate(timeout=call_timeout)
        except subprocess.TimeoutExpired:
            # Keep whatever the process had already emitted before killing it.
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
            return AttemptResult(
                stdout=stdout or "",
                stderr=stderr or "",
                returncode=process.returncode,
                timed_out=True,
            )
        return AttemptResult(
            stdout=stdout or "",
            stderr=stderr or "",
            returncode=process.returncode,
        )

    def _docker_runner(self, container_name, prompt_message, first_extension, second_extension):
        command = [
            "docker",
            "exec",
            container_name,
            "goose",
            "run",
            "--with-extension",
            first_extension,
            "--with-extension",
            second_extension,
            "--with-builtin",
            "memory,developer,computercontroller",
            "-t",
            prompt_message,
        ]
        return self._run_process(command, call_timeout=self.default_call_timeout)

    def _local_prompt_runner(self, goose_ai_prompt):
        # Preserve the original shell pipeline exactly.
        command = 'echo "{}" | goose run -i - && sleep 2'.format(goose_ai_prompt)
        return self._run_process(
            command, shell=True, call_timeout=self.default_call_timeout
        )

    def _local_instruction_file_runner(self, instruction_file):
        command = ["goose", "run", "--instructions", instruction_file]
        return self._run_process(command, call_timeout=self.default_call_timeout)

    # ------------------------------------------------------------------
    # Single-call keywords (legacy names retained for backwards compatibility)
    # ------------------------------------------------------------------
    def send_docker_ai_agent_prompt_message(
        self, container_name, prompt_message, first_extension, second_extension
    ):
        """Send one prompt to a Goose agent in a Docker container.

        Returns the raw stdout (possibly empty). Never raises: a failed or
        timed-out process returns whatever output was captured.
        """
        attempt = self._docker_runner(
            container_name, prompt_message, first_extension, second_extension
        )
        return attempt.stdout

    def send_local_ai_agent_prompt_message(self, goose_ai_prompt):
        """Send one prompt to the local Ollama-backed Goose agent.

        Returns the raw stdout (possibly empty). Never raises.
        """
        attempt = self._local_prompt_runner(goose_ai_prompt)
        return attempt.stdout

    def send_local_ai_agent_instruction_file(self, instruction_file):
        """Send a Markdown instruction file to the local Goose agent.

        Returns the raw stdout (possibly empty). Never raises.
        """
        attempt = self._local_instruction_file_runner(instruction_file)
        return attempt.stdout

    # ------------------------------------------------------------------
    # Retried, outcome-producing collection keywords
    # ------------------------------------------------------------------
    def collect_docker_ai_agent_outcome(
        self,
        container_name,
        prompt_message,
        first_extension,
        second_extension,
        report_stem,
        retries=None,
        retry_interval=None,
    ):
        """Run the Docker Goose path with retries and return a serialised outcome."""
        retries = self.default_retries if retries is None else int(retries)
        retry_interval = (
            self.default_retry_interval
            if retry_interval is None
            else float(retry_interval)
        )
        outcome = run_agent_with_retry(
            lambda: self._docker_runner(
                container_name, prompt_message, first_extension, second_extension
            ),
            retries=retries,
            retry_interval=retry_interval,
            route="docker",
            report_stem=report_stem,
            call_timeout=self.default_call_timeout,
        )
        return outcome_to_dict(outcome)

    def collect_local_ai_agent_prompt_outcome(
        self, goose_ai_prompt, report_stem, retries=None, retry_interval=None
    ):
        """Run the local prompt path with retries and return a serialised outcome."""
        retries = self.default_retries if retries is None else int(retries)
        retry_interval = (
            self.default_retry_interval
            if retry_interval is None
            else float(retry_interval)
        )
        outcome = run_agent_with_retry(
            lambda: self._local_prompt_runner(goose_ai_prompt),
            retries=retries,
            retry_interval=retry_interval,
            route="local",
            report_stem=report_stem,
            call_timeout=self.default_call_timeout,
        )
        return outcome_to_dict(outcome)

    def collect_local_ai_agent_instruction_file_outcome(
        self, instruction_file, report_stem, retries=None, retry_interval=None
    ):
        """Run the local instruction-file path with retries and return an outcome."""
        retries = self.default_retries if retries is None else int(retries)
        retry_interval = (
            self.default_retry_interval
            if retry_interval is None
            else float(retry_interval)
        )
        outcome = run_agent_with_retry(
            lambda: self._local_instruction_file_runner(instruction_file),
            retries=retries,
            retry_interval=retry_interval,
            route="local",
            report_stem=report_stem,
            call_timeout=self.default_call_timeout,
        )
        return outcome_to_dict(outcome)

    # ------------------------------------------------------------------
    # Persistence helpers callable directly from Robot Framework
    # ------------------------------------------------------------------
    def persist_agent_outcome_file(self, outcome_dict, output_dir, report_stem, timestamp):
        """Rehydrate an outcome dict and persist its auditable outcome file."""
        attempts = [
            AttemptResult(
                stdout=attempt.get("stdout", ""),
                stderr=attempt.get("stderr", ""),
                returncode=attempt.get("returncode"),
                timed_out=bool(attempt.get("timed_out", False)),
                error=attempt.get("error"),
            )
            for attempt in outcome_dict.get("attempts", [])
        ]
        outcome = AgentOutcome(
            status=outcome_dict.get("status", RETRY_EXHAUSTED),
            reason=outcome_dict.get("reason", ""),
            output=outcome_dict.get("output", ""),
            stderr=outcome_dict.get("stderr", ""),
            attempts=attempts,
            markers=outcome_dict.get("markers", []),
            report_stem=outcome_dict.get("report_stem", report_stem),
            route=outcome_dict.get("route", ""),
            started_at=outcome_dict.get("started_at", ""),
            finished_at=outcome_dict.get("finished_at", ""),
            retries_configured=outcome_dict.get("retries_configured", len(attempts)),
            call_timeout=outcome_dict.get("call_timeout"),
        )
        return write_outcome_file(outcome, output_dir, report_stem, timestamp)

    def persist_local_agent_report(self, output_dir, report_number, timestamp, content):
        """Write a successful local-agent report without overwriting existing ones."""
        return write_local_agent_report_file(output_dir, report_number, timestamp, content)
