"""Target application process management.

The application inherits the terminal directly (stdout/stderr/stdin) and
ProxyRotate never writes to those streams while it runs. Exit codes are
propagated faithfully; deaths by signal map to the conventional
``128 + signal`` codes.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
from pathlib import Path

from .logging import get_logger

log = get_logger("process")


def _cgroup_joiner(procs_path: Path):
    """Build the pre-exec hook that moves the child into its cgroup.

    Runs in the forked child before exec: the child joins the routing
    group before it can create any socket, closing the classification
    race entirely. Only self-migration (writing our own PID) is used, so
    no privileges are needed beyond the delegated cgroup directory.
    """

    def _join() -> None:
        fd = os.open(procs_path, os.O_WRONLY)
        try:
            os.write(fd, f"{os.getpid()}\n".encode())
        finally:
            os.close(fd)

    return _join


class AppProcess:
    """Runs the target command as an independent child process."""

    def __init__(self, command: list[str], env_overrides: dict[str, str] | None = None,
                 cgroup_procs: Path | None = None):
        if not command:
            raise ValueError("command must not be empty")
        self.command = list(command)
        self.env_overrides = env_overrides or {}
        self.cgroup_procs = cgroup_procs
        self._process: subprocess.Popen | None = None
        self._stop_requested = False

    def start(self) -> None:
        """Start the application. It immediately owns the terminal."""
        log.info("starting: %s", " ".join(self.command))
        env = {**os.environ, **self.env_overrides}
        kwargs = {}
        if self.cgroup_procs is not None:
            kwargs["preexec_fn"] = _cgroup_joiner(self.cgroup_procs)
        # Inherit stdio so the application owns the terminal completely.
        self._process = subprocess.Popen(self.command, env=env, **kwargs)

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def returncode(self) -> int | None:
        return None if self._process is None else self._process.returncode

    def request_stop(self) -> None:
        """Ask the application to terminate (idempotent, non-blocking)."""
        process = self._process
        if process is None or self._stop_requested or process.poll() is not None:
            return
        self._stop_requested = True
        with contextlib.suppress(ProcessLookupError):
            process.terminate()

    def wait_and_finalize(self, grace: float = 2.0) -> int | None:
        """Wait for exit; escalate to terminate/kill when needed.

        Returns the final return code (negative value means killed by
        that signal; see :func:`normalize_exit_code`).
        """
        process = self._process
        if process is None:
            return None

        try:
            process.wait(timeout=grace)
            return process.returncode
        except subprocess.TimeoutExpired:
            pass

        self.request_stop()
        try:
            process.wait(timeout=3)
            return process.returncode
        except subprocess.TimeoutExpired:
            pass

        process.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=2)
        return process.returncode


def normalize_exit_code(returncode: int | None) -> int:
    """Map a subprocess return code to a shell-style exit code."""
    if returncode is None:
        return 1
    if returncode < 0:
        # Killed by signal N -> conventional 128 + N.
        return 128 + (-returncode)
    return returncode
