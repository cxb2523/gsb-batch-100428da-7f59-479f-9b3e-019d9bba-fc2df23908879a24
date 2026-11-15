"""Regression tests for thread-safe sessions and time-based detection accuracy.

Covers two robustness fixes in ``scanner.py``:

1. Worker threads must not share the master ``requests.Session`` (sessions are
   not thread-safe): the constructing thread keeps using the master session,
   while every other thread gets its own lazily-created clone.
2. The configured per-request politeness ``delay`` must not count towards
   time-based SQLi/CMDi detection: a non-vulnerable server behind a 3s delay
   must not be flagged when ``--delay >= --time-based-threshold``.
"""

import threading

from unittest.mock import Mock, patch

from scanner import Scanner


class TestThreadLocalSessions:
    """Worker threads get isolated sessions; the owner keeps the master."""

    def test_owner_thread_uses_master_session(self):
        scanner = Scanner("http://example.com", timeout=5)
        # The constructing thread must keep returning the live master session
        # (existing callers and mocks depend on `scanner.session` identity).
        assert scanner._session() is scanner.session

    def test_worker_thread_gets_isolated_clone(self):
        scanner = Scanner("http://example.com", timeout=5)
        scanner.session.headers["User-Agent"] = "TestAgent/1.0"

        seen = {}

        def worker():
            seen["session"] = scanner._session()
            # Second call in the same thread returns the same clone
            seen["again"] = scanner._session()

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

        clone = seen.get("session")
        assert clone is not None
        assert clone is not scanner.session
        # Clones are stable per thread and seeded from the master session
        assert seen["again"] is clone
        assert clone.headers.get("User-Agent") == "TestAgent/1.0"

    def test_each_worker_thread_gets_its_own_clone(self):
        scanner = Scanner("http://example.com", timeout=5)
        sessions = {}
        barrier = threading.Barrier(2)

        def worker(name):
            barrier.wait()
            sessions[name] = scanner._session()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sessions[0] is not sessions[1]
        assert sessions[0] is not scanner.session
        assert sessions[1] is not scanner.session


class TestTimeBasedDelayIsolation:
    """The politeness delay must not trigger time-based blind detections."""

    def _scanner_with_delay(self, delay=3.0):
        # delay == time_based_threshold reproduces the original bug: _request
        # sleeps `delay` after every response, which used to inflate the
        # measured elapsed time above the threshold even for fast servers.
        return Scanner("http://example.com", timeout=5, delay=delay)

    @staticmethod
    def _install_clock(delta_per_call):
        """Return a fake monotonic clock that advances `delta_per_call` per call.

        Patches sleep to a no-op so no real time passes. Each request
        measures one delta as its elapsed time.
        """
        state = {"now": 1000.0}

        def fake_monotonic():
            state["now"] += delta_per_call
            return state["now"]

        return patch("scanner.time.monotonic", side_effect=fake_monotonic), \
            patch("scanner.time.sleep")

    def test_delay_does_not_false_positive_on_fast_server(self):
        scanner = self._scanner_with_delay(delay=3.0)
        # Measured elapsed 3.05s per request is all politeness delay on a fast
        # server; after removing the delay the true response time (~0.05s) is
        # far below the 3.0s threshold.
        clock, sleeper = self._install_clock(delta_per_call=3.05)

        with clock, sleeper:
            with patch.object(scanner.session, "get") as mock_get:
                mock_get.return_value = Mock(
                    content=b"<html>page</html>", status_code=200
                )
                result = scanner.test_sqli_in_link("http://example.com/user?id=1")

        assert result is False
        assert scanner.scan_result.vulnerabilities == []

    def test_delay_does_not_hide_true_time_based_sqli(self):
        scanner = self._scanner_with_delay(delay=3.0)
        # 6.05s measured - 3.0s politeness delay = ~3.05s genuine server time,
        # which is still above the 3.0s threshold: the fix must not suppress
        # real detections.
        clock, sleeper = self._install_clock(delta_per_call=6.05)

        with clock, sleeper:
            with patch.object(scanner.session, "get") as mock_get:
                mock_get.return_value = Mock(
                    content=b"<html>page</html>", status_code=200
                )
                result = scanner.test_sqli_in_link("http://example.com/user?id=1")

        assert result is True

    def test_delay_does_not_false_positive_on_fast_server_cmdi(self):
        scanner = self._scanner_with_delay(delay=3.0)
        clock, sleeper = self._install_clock(delta_per_call=3.05)

        with clock, sleeper:
            with patch.object(scanner.session, "get") as mock_get:
                mock_get.return_value = Mock(
                    content=b"<html>pong</html>", status_code=200
                )
                result = scanner.test_command_injection(
                    "http://example.com/ping?host=test"
                )

        assert result is False
