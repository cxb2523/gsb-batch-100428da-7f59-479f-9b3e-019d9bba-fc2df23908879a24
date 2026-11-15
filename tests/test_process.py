from __future__ import annotations

import signal
import subprocess
import sys

import pytest

from proxyrotate.process import AppProcess, normalize_exit_code


class FakePopen:
    """Scriptable stand-in for subprocess.Popen."""

    instances: list[FakePopen] = []

    def __init__(self, command, env=None, **kwargs):
        self.command = command
        self.env = env
        self.kwargs = kwargs
        self.returncode = None
        self.terminated = False
        self.killed = False
        # Each entry is ("exit", code) or ("timeout",)
        self.script: list[tuple] = []
        FakePopen.instances.append(self)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        action = self.script.pop(0) if self.script else ("exit", 0)
        if action[0] == "timeout":
            raise subprocess.TimeoutExpired(self.command, timeout)
        self.returncode = action[1]
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


@pytest.fixture
def fake_popen(monkeypatch):
    FakePopen.instances = []
    monkeypatch.setattr(
        "proxyrotate.process.subprocess.Popen",
        lambda command, **kwargs: FakePopen(command, **kwargs),
    )
    return FakePopen


class TestNormalizeExitCode:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (None, 1),
            (0, 0),
            (7, 7),
            (-2, 130),  # SIGINT
            (-15, 143),  # SIGTERM
            (-9, 137),  # SIGKILL
        ],
    )
    def test_mapping(self, raw, expected):
        assert normalize_exit_code(raw) == expected


class TestAppProcess:
    def test_start_passes_env_overrides(self, fake_popen, monkeypatch):
        monkeypatch.setenv("HOME", "/home/tester")
        monkeypatch.setenv("PATH", "/usr/bin")

        app = AppProcess(["opencode"], env_overrides={"HTTP_PROXY": "http://127.0.0.1:1234"})
        app.start()

        proc = fake_popen.instances[-1]
        assert proc.command == ["opencode"]
        assert proc.env["HTTP_PROXY"] == "http://127.0.0.1:1234"
        assert proc.env["HOME"] == "/home/tester"
        assert proc.env["PATH"] == "/usr/bin"

    def test_exit_code_propagated(self, fake_popen):
        app = AppProcess(["whatever"])
        app.start()
        proc = fake_popen.instances[-1]
        proc.script = [("exit", 5)]

        assert app.running  # poll() -> None until wait
        assert app.wait_and_finalize() == 5
        assert app.returncode == 5
        assert not app.running

    def test_escalation_terminates_then_kills(self, fake_popen):
        app = AppProcess(["stubborn"])
        app.start()
        proc = fake_popen.instances[-1]
        proc.script = [("timeout",), ("timeout",), ("exit", -9)]

        code = app.wait_and_finalize(grace=0.01)
        assert code == -9
        assert proc.terminated
        assert proc.killed

    def test_request_stop_idempotent(self, fake_popen):
        app = AppProcess(["x"])
        app.start()
        proc = fake_popen.instances[-1]

        app.request_stop()
        app.request_stop()
        assert proc.terminated

    def test_request_stop_after_exit_noop(self, fake_popen):
        app = AppProcess(["x"])
        app.start()
        proc = fake_popen.instances[-1]
        proc.returncode = 0

        app.request_stop()
        assert not proc.terminated


class TestRealSubprocess:
    """A few tests against the real interpreter to validate wiring."""

    def test_real_exit_code(self):
        app = AppProcess([sys.executable, "-c", "import sys; sys.exit(7)"])
        app.start()
        assert app.wait_and_finalize() == 7

    def test_real_signal_death_maps_to_128_plus_signal(self):
        app = AppProcess(
            [sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"]
        )
        app.start()
        raw = app.wait_and_finalize()
        assert normalize_exit_code(raw) == 128 + int(signal.SIGTERM)

    def test_missing_binary_raises_file_not_found(self):
        app = AppProcess(["definitely-not-a-real-binary-xyz"])
        with pytest.raises(FileNotFoundError):
            app.start()

    def test_empty_command_rejected(self):
        with pytest.raises(ValueError):
            AppProcess([])
