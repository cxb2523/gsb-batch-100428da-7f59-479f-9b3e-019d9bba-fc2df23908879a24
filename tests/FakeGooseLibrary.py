"""Test-only helper library for the Robot Framework offline outcome tests.

It replaces subprocess.Popen inside AIAgentLibrary with scripted fake Goose
processes so the whole Robot keyword chain can be exercised without goose,
docker or ollama installed. Not used by any production workflow.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTOMATION_DIR = os.path.join(REPO_ROOT, "automation-resources")
if AUTOMATION_DIR not in sys.path:
    sys.path.insert(0, AUTOMATION_DIR)

import AIAgentLibrary  # noqa: E402
from goose_outcome import KNOWN_ERROR, RETRY_EXHAUSTED, SUCCESS  # noqa: E402


class _FakeProcess:
    def __init__(self, stdout="", stderr="", returncode=0, fail_with_timeout=False):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._fail_with_timeout = fail_with_timeout
        self._communicate_calls = 0
        self.killed = False

    def communicate(self, timeout=None):
        self._communicate_calls += 1
        if self._fail_with_timeout and self._communicate_calls == 1:
            raise AIAgentLibrary.subprocess.TimeoutExpired(cmd="goose", timeout=timeout)
        if self._fail_with_timeout:
            self.returncode = -15
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True
        self.returncode = -9


class FakeGooseLibrary:
    """Swaps the real subprocess for scripted fake processes.

    Every retry spawns a fresh fake process of the same scripted kind, which
    mirrors how the real library launches a new Goose process per attempt.
    """

    def __init__(self):
        self._original_popen = None

    def use_fake_goose_output(self, stdout, stderr="", returncode=0):
        self._install(lambda *a, **k: _FakeProcess(stdout, stderr, int(returncode)))

    def use_fake_goose_known_error(self, stdout):
        self._install(lambda *a, **k: _FakeProcess(stdout, returncode=0))

    def use_fake_goose_timeout(self, partial_stdout):
        self._install(
            lambda *a, **k: _FakeProcess(partial_stdout, fail_with_timeout=True)
        )

    def use_fake_goose_missing_process(self):
        def boom(*args, **kwargs):
            raise OSError("goose not found")
        self._install(boom)

    def restore_real_subprocess(self):
        if self._original_popen is not None:
            AIAgentLibrary.subprocess.Popen = self._original_popen
            self._original_popen = None

    def _install(self, popen_callable):
        if self._original_popen is None:
            self._original_popen = AIAgentLibrary.subprocess.Popen
        AIAgentLibrary.subprocess.Popen = popen_callable

    def outcome_status_constants(self):
        return {"success": SUCCESS, "known_error": KNOWN_ERROR, "retry_exhausted": RETRY_EXHAUSTED}
