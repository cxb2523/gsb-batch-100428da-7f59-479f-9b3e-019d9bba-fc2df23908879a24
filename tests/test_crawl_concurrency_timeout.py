# -*- coding: utf-8 -*-
"""
Tests for two crawl lifecycle gaps:

1. The concurrency gate counted entries whose ``returncode`` was still None.
   A process that had already exited but not been reaped yet kept occupying
   a slot, so a new crawl was rejected while nothing was actually running.
   The gate now decides on real process liveness and cleans up stale entries
   before admitting a new crawl, and the rejection reports the active count
   and the limit.

2. The 2-hour timeout path only said "terminated" and told the caller to use
   list_crawls. It now reuses the same completion checks as a normal exit
   (FATAL, save failure, memory-mode .seospider file) and gives the same
   follow-up guidance.

Run with: uv run python -m pytest tests/test_crawl_concurrency_timeout.py -v
"""

import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from screaming_frog_mcp import server


# ---------------------------------------------------------------------------
# Fake process objects
# ---------------------------------------------------------------------------


class _ExitedProc:
    """Process that exited cleanly and was reaped."""

    def __init__(self, code: int = 0):
        self.returncode = code

    def poll(self):
        return self.returncode


class _ExitedButUnreapedProc:
    """Process that already exited, but whose exit notification has not been
    delivered to the event loop yet -- ``returncode`` is still None while
    ``poll()`` shows the OS state."""

    def __init__(self, code: int = 0):
        self.returncode = None
        self._code = code
        self.terminated = False

    def poll(self):
        return self._code

    async def wait(self):
        return self._code

    def terminate(self):
        self.terminated = True


class _RunningProc:
    """Process that stays alive until terminate/kill is called."""

    def __init__(self):
        self.returncode = None
        self.terminate_calls = 0
        self.kill_calls = 0

    def poll(self):
        return self.returncode

    async def wait(self):
        while self.returncode is None:
            await asyncio.sleep(0.01)
        return self.returncode

    def terminate(self):
        self.terminate_calls += 1
        self.returncode = -15

    def kill(self):
        self.kill_calls += 1
        self.returncode = -9


class _StubbornProc(_RunningProc):
    """Ignores terminate(); only kill() ends it."""

    def terminate(self):
        self.terminate_calls += 1  # stays running

    def kill(self):
        self.kill_calls += 1
        self.returncode = -9


class _SurvivorProc(_RunningProc):
    """Ignores both terminate() and kill() -- still running afterwards."""

    def terminate(self):
        self.terminate_calls += 1

    def kill(self):
        self.kill_calls += 1


class _FakeTransport:
    """Minimal asyncio transport stand-in exposing the underlying Popen."""

    class _Popen:
        def __init__(self, code):
            self._code = code

        def poll(self):
            return self._code

    def __init__(self, code):
        self._proc = self._Popen(code)

    def get_returncode(self):
        return None


class _TransportOnlyProc:
    """Liveness is only visible through the asyncio transport's Popen."""

    def __init__(self, code: int = 0):
        self.returncode = None
        self._transport = _FakeTransport(code)

    def poll(self):
        raise RuntimeError("no poll on this stand-in")


# ---------------------------------------------------------------------------
# Liveness helpers
# ---------------------------------------------------------------------------


class TestProcLiveness:
    def test_reaped_process_is_not_active(self):
        assert server._crawl_is_active({"proc": _ExitedProc(0)}) is False
        assert server._crawl_is_active({"proc": _ExitedProc(1)}) is False

    def test_exited_but_unreaped_process_is_not_active(self):
        """The regression: returncode is None but the process is gone."""
        proc = _ExitedButUnreapedProc(0)
        assert server._crawl_is_active({"proc": proc}) is False

    def test_running_process_is_active(self):
        assert server._crawl_is_active({"proc": _RunningProc()}) is True

    def test_missing_proc_is_not_active(self):
        assert server._crawl_is_active({}) is False

    def test_poll_falls_back_to_transport_proc(self):
        """When the wrapper cannot poll, the underlying Popen still can."""
        proc = _TransportOnlyProc(0)
        assert server._proc_exit_code(proc) == 0
        assert server._crawl_is_active({"proc": proc}) is False


# ---------------------------------------------------------------------------
# Concurrency gate
# ---------------------------------------------------------------------------


@pytest.fixture
def crawl_env(tmp_path, monkeypatch):
    """Point crawl paths at tmp dirs and neutralize SF checks."""
    sf_path = tmp_path / "ScreamingFrogSEOSpiderLauncher"
    sf_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(server, "SF_CLI_PATH", str(sf_path))
    monkeypatch.setattr(server, "CRAWL_OUTPUT_BASE", tmp_path / "crawl-output")
    monkeypatch.setattr(server, "TEMP_EXPORT_BASE", tmp_path / "exports")
    monkeypatch.setattr(server, "_validate_url", lambda url: None)
    monkeypatch.setattr(server, "_sf_gui_is_running", lambda: False)

    async def fake_create(*args, **kwargs):
        return _RunningProc()

    monkeypatch.setattr(server.asyncio, "create_subprocess_exec", fake_create)
    server._running_crawls.clear()
    yield tmp_path
    server._running_crawls.clear()


def _register(crawl_id, proc, tmp_path, memory_file=False):
    log_dir = tmp_path / f"{crawl_id}-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    output_dir = tmp_path / crawl_id
    output_dir.mkdir(parents=True, exist_ok=True)
    if memory_file:
        (output_dir / "crawl.seospider").write_bytes(b"data")
    server._running_crawls[crawl_id] = {
        "proc": proc,
        "url": "https://example.com",
        "label": "example.com",
        "started": time.time(),
        "log_dir": log_dir,
        "output_dir": output_dir,
        "stdout_log": None,
        "stderr_log": None,
    }


class TestConcurrencyGate:
    def test_stale_entries_do_not_block_admission(self, crawl_env):
        """Exited-but-uncleaned entries must not occupy concurrency slots."""
        # Shrink the brief startup wait path by making created procs "active";
        # the point here is that the stale entries are evicted.
        for i in range(server.MAX_CONCURRENT_CRAWLS):
            _register(f"stale-{i}", _ExitedButUnreapedProc(0), crawl_env)

        result = asyncio.run(server.crawl_site("https://example.com"))

        assert "Maximum" not in result
        assert "Crawl started in background" in result
        # Stale entries were cleaned up before admission.
        assert all(not cid.startswith("stale-") for cid in server._running_crawls)

    def test_running_crawls_block_and_report_counts(self, crawl_env):
        for i in range(server.MAX_CONCURRENT_CRAWLS):
            _register(f"live-{i}", _RunningProc(), crawl_env)

        result = asyncio.run(server.crawl_site("https://example.com"))

        assert result.startswith("ERROR:")
        assert f"active: {server.MAX_CONCURRENT_CRAWLS}" in result
        assert f"limit: {server.MAX_CONCURRENT_CRAWLS}" in result
        assert "retry" in result.lower()

    def test_mixed_stale_and_live_counts_only_live(self, crawl_env):
        """One stale + one live entry is below the limit: admission allowed,
        and the live entry is still tracked."""
        _register("stale", _ExitedButUnreapedProc(0), crawl_env)
        _register("live", _RunningProc(), crawl_env)

        result = asyncio.run(server.crawl_site("https://example.com"))

        assert "Crawl started in background" in result
        assert "stale" not in server._running_crawls
        assert "live" in server._running_crawls
        assert any(cid.startswith("crawl-") for cid in server._running_crawls)

    def test_cleanup_closes_logs_of_exited_crawl(self, crawl_env):
        log_path = crawl_env / "logfile.log"
        fh = open(log_path, "w")
        _register("done", _ExitedButUnreapedProc(0), crawl_env)
        server._running_crawls["done"]["stdout_log"] = fh

        server._cleanup_completed_crawls()

        assert fh.closed
        assert "done" not in server._running_crawls

    def test_cleanup_keeps_memory_mode_seospider(self, crawl_env):
        _register("mem", _ExitedButUnreapedProc(0), crawl_env, memory_file=True)

        server._cleanup_completed_crawls()

        assert (crawl_env / "mem" / "crawl.seospider").exists()


# ---------------------------------------------------------------------------
# Timeout termination path
# ---------------------------------------------------------------------------

DB_LOG = (
    "INFO  - Completed the spider of https://example.com/ crawled 42 urls\n"
    "INFO  - Shutdown database, location: "
    "/Users/test/.ScreamingFrogSEOSpider/ProjectInstanceData/abc/results/sql\n"
)


def _register_expired(crawl_id, tmp_path, log_text, memory_file=False, proc=None):
    _register(crawl_id, proc or _RunningProc(), tmp_path, memory_file=memory_file)
    info = server._running_crawls[crawl_id]
    info["started"] = time.time() - server.MAX_CRAWL_DURATION - 10
    log_dir = info["log_dir"]
    (log_dir / "stdout.log").write_text(log_text, encoding="utf-8")
    (log_dir / "stderr.log").write_text("", encoding="utf-8")
    info["stdout_log"] = open(log_dir / "stdout.log", "a")
    info["stderr_log"] = open(log_dir / "stderr.log", "a")
    return info


class TestTimeoutTermination:
    @pytest.fixture(autouse=True)
    def clean_registry(self, monkeypatch):
        # Keep the terminate/kill grace periods out of test wall-clock time.
        monkeypatch.setattr(server, "CRAWL_TERMINATE_GRACE_SECONDS", 0.2)
        monkeypatch.setattr(server, "CRAWL_KILL_WAIT_SECONDS", 0.2)
        server._running_crawls.clear()
        yield
        server._running_crawls.clear()

    def test_terminates_and_stops_process(self, tmp_path):
        proc = _RunningProc()
        _register_expired("t1", tmp_path, DB_LOG, proc=proc)

        result = asyncio.run(server.crawl_status("t1"))

        assert proc.terminate_calls == 1
        assert proc.returncode is not None
        assert "terminated" in result
        assert f"{server.MAX_CRAWL_DURATION // 3600}h" in result

    def test_database_mode_guidance_matches_normal_completion(self, tmp_path):
        _register_expired("t2", tmp_path, DB_LOG)

        result = asyncio.run(server.crawl_status("t2"))

        assert "URLs crawled: 42" in result
        assert "list_crawls" in result
        assert "internal database" in result

    def test_database_mode_without_shutdown_line_is_not_claimed_saved(self, tmp_path):
        """A terminated crawl that never logged the DB shutdown line must not
        claim the data is saved."""
        log = "INFO  - Completed the spider of https://example.com/ crawled 12 urls\n"
        _register_expired("t2b", tmp_path, log)

        result = asyncio.run(server.crawl_status("t2b"))

        assert "list_crawls" in result
        assert "unclear whether it was saved" in result
        assert "The crawl is saved" not in result

    def test_memory_mode_without_shutdown_line_still_names_file(self, tmp_path):
        log = "INFO  - Completed the spider of https://example.com/ crawled 12 urls\n"
        info = _register_expired("t2c", tmp_path, log, memory_file=True)
        save = info["output_dir"] / "crawl.seospider"

        result = asyncio.run(server.crawl_status("t2c"))

        assert str(save) in result
        assert "partial" in result.lower()

    def test_memory_mode_reports_file_and_is_preserved(self, tmp_path):
        info = _register_expired("t3", tmp_path, DB_LOG, memory_file=True)
        save = info["output_dir"] / "crawl.seospider"

        result = asyncio.run(server.crawl_status("t3"))

        assert str(save) in result
        assert "memory storage mode" in result
        assert "will\nnot see it" in result or "not see it" in result
        # Termination must never delete the user's crawl file.
        assert save.exists()
        assert save.read_bytes() == b"data"

    def test_save_failure_reported(self, tmp_path):
        log = (
            "INFO  - Completed the spider of https://example.com/ crawled 7 urls\n"
            "WARN  - Crawl save failed {}\n"
        )
        _register_expired("t4", tmp_path, log)

        result = asyncio.run(server.crawl_status("t4"))

        assert "WARNING: Crawl save failed" in result
        assert "crawled" in result.lower()

    def test_fatal_error_reported(self, tmp_path):
        log = "FATAL - Something broke\n"
        _register_expired("t5", tmp_path, log)

        result = asyncio.run(server.crawl_status("t5"))

        assert "FATAL: FATAL - Something broke" in result
        assert "did not complete" in result

    def test_log_handles_closed_after_termination(self, tmp_path):
        info = _register_expired("t6", tmp_path, DB_LOG)
        stdout_handle = info["stdout_log"]
        stderr_handle = info["stderr_log"]

        asyncio.run(server.crawl_status("t6"))

        assert stdout_handle.closed
        assert stderr_handle.closed

    def test_kill_escalation_when_terminate_ignored(self, tmp_path):
        proc = _StubbornProc()
        _register_expired("t7", tmp_path, DB_LOG, proc=proc)

        result = asyncio.run(server.crawl_status("t7"))

        assert proc.terminate_calls == 1
        assert proc.kill_calls == 1
        assert "terminated" in result

    def test_second_status_call_does_not_retreat_as_running(self, tmp_path):
        """After the timeout report, a later poll reports the same outcome."""
        _register_expired("t8", tmp_path, DB_LOG)

        first = asyncio.run(server.crawl_status("t8"))
        second = asyncio.run(server.crawl_status("t8"))

        assert "terminated" in first
        assert "still running" not in second
        assert "terminated" in second

    def test_repeat_call_escalates_to_kill_if_still_alive(self, tmp_path):
        proc = _SurvivorProc()
        _register_expired("t9", tmp_path, DB_LOG, proc=proc)

        asyncio.run(server.crawl_status("t9"))
        second = asyncio.run(server.crawl_status("t9"))

        assert proc.terminate_calls == 1  # grace attempted once
        assert proc.kill_calls == 2  # one per status call, incl. the retry
        assert "did not exit" in second
        assert "still running" not in second.replace("did not exit", "")

    def test_identical_outcome_for_normal_and_terminated_reports(self, tmp_path):
        """Both paths must reach the same database/memory conclusion."""
        _register_expired(
            "sim", tmp_path, DB_LOG, proc=_ExitedProc(0))
        server._running_crawls["sim"]["started"] = time.time()
        normal = asyncio.run(server.crawl_status("sim"))
        assert "completed" in normal

        _register_expired("term", tmp_path / "other", DB_LOG)
        terminated = asyncio.run(server.crawl_status("term"))

        normal_guidance = normal.split("\n\n")[-1]
        terminated_guidance = terminated.split("\n\n")[-1]
        assert normal_guidance == terminated_guidance

    def test_unknown_exit_code_is_not_reported_as_failure(self, tmp_path, monkeypatch):
        """A process observed gone before its exit status arrives must not
        be labelled 'failed (exit code None)'."""
        class _NoExitCodeProc:
            pid = None
            returncode = None

            def poll(self):
                return None

        _register_expired(
            "gone", tmp_path, DB_LOG, proc=_NoExitCodeProc())
        server._running_crawls["gone"]["started"] = time.time()
        # Simulate the process being observed gone while its exit status is
        # still in flight (the POSIX pid probe path).
        monkeypatch.setattr(server, "_crawl_is_active", lambda info: False)

        report = asyncio.run(server.crawl_status("gone"))

        assert "exit code None" not in report
        assert "finished (exit code unavailable)" in report
        assert "list_crawls" in report
