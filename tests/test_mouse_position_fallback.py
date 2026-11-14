"""
tests/test_mouse_position_fallback.py — Explainable ordered-fallback tests.

These tests never touch a real compositor, real /dev/input devices or real
system binaries: the fallback chain is driven entirely by injectable fake
adapters, plus targeted subprocess patching for the concrete adapters.
"""

import subprocess
import unittest
from unittest.mock import MagicMock, patch

from wayland_automation.mouse_position import (
    BackendFailure,
    BackendSwitch,
    CursorBackendAdapter,
    CursorPositionTracker,
    DEFAULT_BACKEND_ORDER,
    EvdevAdapter,
    FailureReason,
    HyprctlAdapter,
    KWinAdapter,
    NoBackendAvailableError,
    PositionReport,
    WlFindCursorAdapter,
    XdotoolAdapter,
    get_position,
    get_position_with_report,
    mouse_position_generator,
    pick_backend_and_start,
)


# ---------------------------------------------------------------------------
# Injectable fake backends
# ---------------------------------------------------------------------------
def make_fake_backend(
    name,
    probe_result=(True, None, ""),
    start_result=(True, None, ""),
    read_script=None,
    persistent=False,
    supports_seed=False,
    initial_pos=None,
):
    """Build a fresh fake adapter class configured for one scenario.

    ``read_script`` is a list of ``(pos, reason, detail)`` triples replayed
    in order; the final entry repeats forever. When ``initial_pos`` is given
    the fake returns it from every read (a permanently-healthy backend).
    """

    class FakeAdapter(CursorBackendAdapter):
        instances = []

        def __init__(self, seed=None):
            super().__init__(seed=seed)
            self.name = name
            self.read_calls = 0
            self.started = False
            self.stopped = False
            self.seen_seed = seed
            FakeAdapter.instances.append(self)

        def probe(self):
            return probe_result

        def start(self):
            ok, reason, detail = start_result
            self.started = ok
            return ok, reason, detail

        def read(self, timeout=0.5):
            if initial_pos is not None:
                return initial_pos, None, ""
            if not read_script:
                return None, FailureReason.EMPTY_RESULT, "fake: empty"
            entry = read_script[min(self.read_calls, len(read_script) - 1)]
            self.read_calls += 1
            return entry

        def stop(self):
            self.stopped = True
            super().stop()

    FakeAdapter.__name__ = f"Fake_{name}"
    # Real class attributes so the tracker's getattr-based instantiation
    # (supports_seed) and persistent checks see the configured values.
    FakeAdapter.persistent = persistent
    FakeAdapter.supports_seed = supports_seed
    return FakeAdapter


def take(generator, n):
    """Pull exactly n items out of a generator."""
    return [next(generator) for _ in range(n)]


class TestSnapshotFallback(unittest.TestCase):
    def test_first_available_backend_wins(self):
        healthy = make_fake_backend("fake-a", initial_pos=(10, 20))
        untouched = make_fake_backend("fake-b", initial_pos=(99, 99))
        report = PositionReport()
        pos, returned_report = get_position_with_report(
            report=report, backends=(healthy, untouched)
        )
        self.assertEqual(pos, (10, 20))
        self.assertIs(returned_report, report)
        self.assertEqual(report.active_backend, "fake-a")
        self.assertEqual(report.failures, [])
        # The second backend must never even be instantiated.
        self.assertEqual(untouched.instances, [])

    def test_fallback_skips_unavailable_backend_in_priority_order(self):
        broken = make_fake_backend(
            "fake-broken",
            probe_result=(
                False,
                FailureReason.BINARY_MISSING,
                "binary not installed",
            ),
        )
        healthy = make_fake_backend("fake-ok", initial_pos=(7, 8))
        report = PositionReport()
        pos, report = get_position_with_report(
            report=report, backends=(broken, healthy)
        )
        self.assertEqual(pos, (7, 8))
        self.assertEqual(report.active_backend, "fake-ok")
        self.assertEqual(len(report.failures), 1)
        failure = report.failures[0]
        self.assertEqual(failure.backend, "fake-broken")
        self.assertEqual(failure.reason, FailureReason.BINARY_MISSING)
        self.assertEqual(failure.phase, "probe")
        self.assertIn("binary not installed", failure.detail)
        self.assertEqual(report.skipped, [failure])

    def test_startup_failure_falls_through(self):
        bad_start = make_fake_backend(
            "fake-bad-start",
            persistent=True,
            start_result=(
                False,
                FailureReason.STARTUP_FAILED,
                "process spawn blew up",
            ),
        )
        healthy = make_fake_backend("fake-ok", initial_pos=(1, 2))
        report = PositionReport()
        pos, report = get_position_with_report(
            report=report, backends=(bad_start, healthy)
        )
        self.assertEqual(pos, (1, 2))
        self.assertEqual(report.failures[0].reason, FailureReason.STARTUP_FAILED)
        self.assertEqual(report.failures[0].phase, "startup")
        # The failed controller must have been cleaned up.
        self.assertTrue(bad_start.instances[0].stopped)

    def test_first_read_failure_triggers_fallback(self):
        # Probe + start succeed, but the very first read comes back empty.
        empty = make_fake_backend(
            "fake-empty",
            persistent=True,
            read_script=[
                (None, FailureReason.EMPTY_RESULT, "no data yet"),
            ],
        )
        healthy = make_fake_backend("fake-ok", initial_pos=(3, 4))
        report = PositionReport()
        pos, report = get_position_with_report(
            report=report, backends=(empty, healthy)
        )
        self.assertEqual(pos, (3, 4))
        self.assertEqual(report.failures[0].backend, "fake-empty")
        self.assertEqual(report.failures[0].reason, FailureReason.EMPTY_RESULT)
        self.assertEqual(report.failures[0].phase, "read")
        self.assertTrue(empty.instances[0].stopped)

    def test_all_backends_fail_raises_detailed_exception(self):
        b1 = make_fake_backend(
            "b1",
            probe_result=(False, FailureReason.BINARY_MISSING, "missing b1"),
        )
        b2 = make_fake_backend(
            "b2",
            persistent=True,
            start_result=(False, FailureReason.STARTUP_FAILED, "spawn fail"),
        )
        b3 = make_fake_backend(
            "b3",
            read_script=[
                (None, FailureReason.READ_TIMEOUT, "slow backend"),
            ],
        )
        report = PositionReport()
        with self.assertRaises(NoBackendAvailableError) as ctx:
            CursorPositionTracker(backends=(b1, b2, b3), report=report).snapshot()
        exc = ctx.exception
        # Must be a RuntimeError for backwards compatibility.
        self.assertIsInstance(exc, RuntimeError)
        self.assertEqual(exc.failures, report.failures)
        message = str(exc)
        # Message names every backend and the categorized reason, not a
        # generic "no backend available" line.
        for backend_name in ("b1", "b2", "b3"):
            self.assertIn(backend_name, message)
        self.assertIn(FailureReason.BINARY_MISSING.value, message)
        self.assertIn(FailureReason.STARTUP_FAILED.value, message)
        self.assertIn(FailureReason.READ_TIMEOUT.value, message)
        self.assertIn("slow backend", message)
        self.assertIn("missing b1", message)
        self.assertEqual(len(exc.failures), 3)

    def test_empty_chain_raises(self):
        with self.assertRaises(NoBackendAvailableError):
            CursorPositionTracker(backends=()).snapshot()

    def test_get_position_legacy_call_style_returns_tuple(self):
        healthy = make_fake_backend("fake-ok", initial_pos=(50, 60))
        with patch(
            "wayland_automation.mouse_position.DEFAULT_BACKEND_ORDER",
            (healthy,),
        ):
            pos = get_position()
        self.assertEqual(pos, (50, 60))
        self.assertIsInstance(pos, tuple)

    def test_get_position_raises_summary_when_everything_fails(self):
        dead = make_fake_backend(
            "dead",
            probe_result=(False, FailureReason.UNAVAILABLE, "nope"),
        )
        with self.assertRaises(NoBackendAvailableError) as ctx:
            CursorPositionTracker(backends=(dead,)).snapshot()
        self.assertIn("dead", str(ctx.exception))


class TestGeneratorSwitching(unittest.TestCase):
    def _failing_then_healthy(self, threshold, fail_reason):
        first = make_fake_backend(
            "first",
            persistent=True,
            read_script=[
                ((1, 1), None, ""),  # initial read used at activation
                (None, fail_reason, "stream died"),
            ],
        )
        second = make_fake_backend(
            "second",
            persistent=True,
            initial_pos=(5, 5),
        )
        report = PositionReport()
        switches = []
        tracker = CursorPositionTracker(
            backends=(first, second),
            read_fail_threshold=threshold,
            report=report,
            on_switch=switches.append,
        )
        return first, second, report, switches, tracker

    def test_switch_after_consecutive_failure_threshold(self):
        first, second, report, switches, tracker = self._failing_then_healthy(
            threshold=2, fail_reason=FailureReason.READ_ERROR
        )
        gen = tracker.stream(interval=0)
        positions = take(gen, 4)
        gen.close()

        # First coordinate from "first", then the threshold (2) of failures
        # retires it; subsequent coordinates come fresh from "second".
        self.assertEqual(positions[0], (1, 1))
        self.assertIn((5, 5), positions)
        self.assertEqual(report.active_backend, "second")
        self.assertEqual(len(switches), 1)
        switch = switches[0]
        self.assertIsInstance(switch, BackendSwitch)
        self.assertEqual(switch.from_backend, "first")
        self.assertEqual(switch.to_backend, "second")
        self.assertEqual(switch.reason, FailureReason.READ_ERROR)
        self.assertTrue(first.instances[0].stopped)
        self.assertTrue(second.instances[0].started)
        # Every failed read was recorded, none swallowed.
        read_failures = [
            f for f in report.failures
            if f.backend == "first" and f.phase == "read"
        ]
        self.assertEqual(len(read_failures), 2)
        self.assertTrue(
            all(f.reason == FailureReason.READ_ERROR for f in read_failures)
        )

    def test_below_threshold_does_not_switch(self):
        # threshold=3 but the fake's script permanently fails after the
        # initial read; take only a few items so the threshold isn't reached.
        first = make_fake_backend(
            "first",
            persistent=True,
            read_script=[
                ((1, 1), None, ""),
                (None, FailureReason.EMPTY_RESULT, "gap"),
            ],
        )
        second = make_fake_backend("second", initial_pos=(9, 9))
        report = PositionReport()
        tracker = CursorPositionTracker(
            backends=(first, second), read_fail_threshold=3, report=report
        )
        gen = tracker.stream(interval=0)
        positions = take(gen, 3)
        gen.close()
        # Still on the first backend; cadence preserved with last good value.
        self.assertEqual(report.active_backend, "first")
        self.assertEqual(report.switches, [])
        self.assertEqual(second.instances, [])
        self.assertEqual(positions, [(1, 1), (1, 1), (1, 1)])
        self.assertEqual(
            len([f for f in report.failures if f.phase == "read"]), 2
        )

    def test_switch_never_goes_backwards_or_replays(self):
        # First backend moves 1 -> 2 -> 3 then dies. The replacement starts
        # at a fresh coordinate. Yielded values must never move backwards and
        # the switch itself must produce a brand-new coordinate.
        first_script = [
            ((10, 10), None, ""),  # activation read
            ((11, 10), None, ""),
            ((12, 10), None, ""),
            (None, FailureReason.READ_ERROR, "dead"),
        ]
        first = make_fake_backend("first", persistent=True, read_script=first_script)
        second = make_fake_backend(
            "second", persistent=True, initial_pos=(13, 10)
        )
        report = PositionReport()
        tracker = CursorPositionTracker(
            backends=(first, second), read_fail_threshold=1, report=report
        )
        gen = tracker.stream(interval=0)
        positions = take(gen, 6)
        gen.close()

        self.assertEqual(report.active_backend, "second")
        # Monotonic non-decreasing across the whole stream.
        for earlier, later in zip(positions, positions[1:]):
            self.assertLessEqual(
                earlier, later,
                f"coordinates moved backwards: {earlier} -> {later}",
            )
        # The first value delivered by the replacement is genuinely fresh:
        # no duplicate of the dying backend's last coordinate at the seam.
        seam = positions.index((13, 10))
        self.assertGreater(seam, 0)
        self.assertNotEqual(positions[seam - 1], (13, 10))

    def test_switch_records_skipped_candidates(self):
        first = make_fake_backend(
            "first",
            persistent=True,
            read_script=[
                ((1, 1), None, ""),
                (None, FailureReason.READ_TIMEOUT, "frozen"),
            ],
        )
        middle = make_fake_backend(
            "middle",
            probe_result=(False, FailureReason.BINARY_MISSING, "no middle"),
        )
        last = make_fake_backend("last", persistent=True, initial_pos=(2, 2))
        report = PositionReport()
        tracker = CursorPositionTracker(
            backends=(first, middle, last),
            read_fail_threshold=1,
            report=report,
        )
        gen = tracker.stream(interval=0)
        positions = take(gen, 3)
        gen.close()
        self.assertEqual(report.active_backend, "last")
        skipped_names = {f.backend for f in report.skipped}
        self.assertIn("middle", skipped_names)
        self.assertEqual(positions[-1], (2, 2))
        switch = report.switches[-1]
        self.assertEqual(switch.from_backend, "first")
        self.assertEqual(switch.to_backend, "last")
        self.assertTrue(
            any(f.backend == "middle" for f in switch.failures)
        )

    def test_generator_raises_when_all_candidates_exhausted(self):
        first = make_fake_backend(
            "first",
            persistent=True,
            read_script=[
                ((1, 1), None, ""),
                (None, FailureReason.READ_ERROR, "dead"),
            ],
        )
        second = make_fake_backend(
            "second",
            probe_result=(False, FailureReason.UNAVAILABLE, "gone"),
        )
        report = PositionReport()
        tracker = CursorPositionTracker(
            backends=(first, second),
            read_fail_threshold=1,
            report=report,
        )
        gen = tracker.stream(interval=0)
        self.assertEqual(next(gen), (1, 1))
        with self.assertRaises(NoBackendAvailableError) as ctx:
            next(gen)
        message = str(ctx.exception)
        self.assertIn("first", message)
        self.assertIn("second", message)
        self.assertIn("gone", message)

    def test_generator_close_stops_active_backend(self):
        healthy = make_fake_backend(
            "healthy", persistent=True, initial_pos=(1, 1)
        )
        tracker = CursorPositionTracker(backends=(healthy,))
        gen = tracker.stream(interval=0)
        take(gen, 2)
        gen.close()
        self.assertTrue(healthy.instances[0].stopped)
        self.assertIsNone(tracker._active)

    def test_functional_generator_accepts_report_and_backends(self):
        healthy = make_fake_backend("fake-ok", initial_pos=(4, 5))
        report = PositionReport()
        gen = mouse_position_generator(
            interval=0, report=report, backends=(healthy,)
        )
        self.assertEqual(take(gen, 3), [(4, 5), (4, 5), (4, 5)])
        gen.close()
        self.assertEqual(report.active_backend, "fake-ok")

    def test_invalid_threshold_rejected(self):
        with self.assertRaises(ValueError):
            CursorPositionTracker(read_fail_threshold=0)


class TestSeedingAndContinuity(unittest.TestCase):
    def test_seed_capable_backend_receives_last_known_position(self):
        first = make_fake_backend(
            "first",
            persistent=True,
            read_script=[
                ((42, 43), None, ""),  # activation read
                (None, FailureReason.READ_ERROR, "dead"),
            ],
        )
        evdev_like = make_fake_backend(
            "evdevish",
            persistent=True,
            supports_seed=True,
            initial_pos=(42, 43),
        )
        tracker = CursorPositionTracker(
            backends=(first, evdev_like), read_fail_threshold=1
        )
        gen = tracker.stream(interval=0)
        # Item 1: activation coordinate. Read after it fails -> switch,
        # seeding the replacement from the last good position. Item 2 is the
        # replacement's first fresh coordinate.
        items = take(gen, 2)
        self.assertEqual(items[0], (42, 43))
        self.assertEqual(tracker.report.active_backend, "evdevish")
        self.assertEqual(evdev_like.instances[0].seen_seed, (42, 43))
        gen.close()

    def test_evdev_adapter_declares_seed_support(self):
        adapter = EvdevAdapter(seed=(100, 200))
        self.assertTrue(adapter.supports_seed)
        self.assertEqual(adapter.seed, (100, 200))


class TestConcreteAdaptersWithStubs(unittest.TestCase):
    @patch("shutil.which", return_value=None)
    def test_hyprctl_probe_reports_missing_binary(self, _which):
        ok, reason, _detail = HyprctlAdapter().probe()
        self.assertFalse(ok)
        self.assertEqual(reason, FailureReason.BINARY_MISSING)

    @patch("shutil.which", return_value="/usr/bin/hyprctl")
    def test_hyprctl_read_timeout_is_categorized(self, _which):
        with patch(
            "wayland_automation.mouse_position.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="hyprctl", timeout=0.5),
        ):
            pos, reason, detail = HyprctlAdapter().read()
        self.assertIsNone(pos)
        self.assertEqual(reason, FailureReason.READ_TIMEOUT)
        self.assertIn("timed out", detail)

    @patch("shutil.which", return_value="/usr/bin/hyprctl")
    def test_hyprctl_empty_output_is_empty_result(self, _which):
        mock_proc = MagicMock(returncode=0, stdout="\n", stderr="")
        with patch(
            "wayland_automation.mouse_position.subprocess.run",
            return_value=mock_proc,
        ):
            pos, reason, detail = HyprctlAdapter().read()
        self.assertIsNone(pos)
        self.assertEqual(reason, FailureReason.EMPTY_RESULT)
        self.assertIn("no coordinates", detail)

    @patch("shutil.which", return_value="/usr/bin/hyprctl")
    def test_hyprctl_nonzero_exit_is_read_error(self, _which):
        mock_proc = MagicMock(
            returncode=2, stdout="", stderr="socket failure"
        )
        with patch(
            "wayland_automation.mouse_position.subprocess.run",
            return_value=mock_proc,
        ):
            pos, reason, detail = HyprctlAdapter().read()
        self.assertIsNone(pos)
        self.assertEqual(reason, FailureReason.READ_ERROR)
        self.assertIn("socket failure", detail)

    @patch("shutil.which", return_value="/usr/bin/hyprctl")
    def test_hyprctl_success_path(self, _which):
        mock_proc = MagicMock(returncode=0, stdout="800, 600\n", stderr="")
        with patch(
            "wayland_automation.mouse_position.subprocess.run",
            return_value=mock_proc,
        ):
            pos, reason, detail = HyprctlAdapter().read()
        self.assertEqual(pos, (800, 600))
        self.assertIsNone(reason)
        self.assertEqual(detail, "")

    @patch.dict("os.environ", {}, clear=True)
    @patch("shutil.which", return_value="/usr/bin/xdotool")
    def test_xdotool_probe_needs_display(self, _which):
        ok, reason, _detail = XdotoolAdapter().probe()
        self.assertFalse(ok)
        self.assertEqual(reason, FailureReason.UNAVAILABLE)

    @patch("shutil.which", return_value=None)
    def test_wlfindcursor_probe_missing_binary(self, _which):
        ok, reason, _detail = WlFindCursorAdapter().probe()
        self.assertFalse(ok)
        self.assertEqual(reason, FailureReason.BINARY_MISSING)

    @patch("shutil.which", return_value="/usr/bin/wl-find-cursor")
    def test_wlfindcursor_startup_failure_categorized(self, _which):
        with patch(
            "wayland_automation.mouse_position.subprocess.Popen",
            side_effect=OSError("cannot spawn"),
        ):
            adapter = WlFindCursorAdapter()
            ok, reason, detail = adapter.start()
        self.assertFalse(ok)
        self.assertEqual(reason, FailureReason.STARTUP_FAILED)
        self.assertIn("cannot spawn", detail)

    @patch("shutil.which", return_value=None)
    def test_kwin_probe_missing_qdbus(self, _which):
        ok, reason, _detail = KWinAdapter().probe()
        self.assertFalse(ok)
        self.assertEqual(reason, FailureReason.BINARY_MISSING)

    @patch("wayland_automation.mouse_position._find_qdbus", return_value="/usr/bin/qdbus")
    @patch("shutil.which", return_value="/usr/bin/qdbus")
    def test_kwin_probe_missing_dbus_next(self, _which, _qdbus):
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "dbus_next":
                raise ImportError("not installed")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=fake_import):
            ok, reason, detail = KWinAdapter().probe()
        self.assertFalse(ok)
        self.assertEqual(reason, FailureReason.UNAVAILABLE)
        self.assertIn("dbus-next", detail)

    @patch("wayland_automation.mouse_position._find_qdbus", return_value="/usr/bin/qdbus")
    @patch("shutil.which", return_value="/usr/bin/qdbus")
    def test_kwin_probe_timeout(self, _which, _qdbus):
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "dbus_next":
                return MagicMock()
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=fake_import), patch(
            "wayland_automation.mouse_position.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="qdbus", timeout=1.0),
        ):
            ok, reason, _detail = KWinAdapter().probe()
        self.assertFalse(ok)
        self.assertEqual(reason, FailureReason.READ_TIMEOUT)

    def test_evdev_probe_unavailable_without_package(self):
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "evdev":
                raise ImportError("no evdev")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=fake_import):
            ok, reason, detail = EvdevAdapter().probe()
        self.assertFalse(ok)
        self.assertEqual(reason, FailureReason.UNAVAILABLE)
        self.assertIn("evdev", detail)


class TestStubCommandIntegration(unittest.TestCase):
    """Drive the concrete snapshot adapters with patched subprocess output,
    proving the fallback chain works against command-shaped stubs rather than
    only fake adapter classes."""

    @patch.dict("os.environ", {"DISPLAY": ":0"})
    def test_hyprctl_empty_then_xdotool_succeeds(self):
        # hyprctl "installed" but returns unparseable output; xdotool rescues.
        def fake_which(cmd):
            return f"/usr/bin/{cmd}" if cmd in ("hyprctl", "xdotool") else None

        def fake_run(argv, **kwargs):
            if argv[0].endswith("hyprctl"):
                return MagicMock(returncode=0, stdout="not-a-number\n", stderr="")
            # xdotool --shell style output
            return MagicMock(
                returncode=0,
                stdout="X=123\nY=456\nSCREEN=0\nWINDOW=1\n",
                stderr="",
            )

        with patch("shutil.which", side_effect=fake_which), patch(
            "wayland_automation.mouse_position.subprocess.run",
            side_effect=fake_run,
        ):
            report = PositionReport()
            tracker = CursorPositionTracker(
                backends=(HyprctlAdapter, XdotoolAdapter),
                initial_read_timeout=0,
                report=report,
            )
            pos = tracker.snapshot()

        self.assertEqual(pos, (123, 456))
        self.assertEqual(report.active_backend, "xdotool")
        self.assertEqual(len(report.failures), 1)
        self.assertEqual(report.failures[0].backend, "hyprctl")
        self.assertEqual(report.failures[0].reason, FailureReason.EMPTY_RESULT)

    @patch.dict("os.environ", {"DISPLAY": ":0"})
    def test_hyprctl_timeout_then_xdotool_succeeds(self):
        def fake_which(cmd):
            return f"/usr/bin/{cmd}" if cmd in ("hyprctl", "xdotool") else None

        def fake_run(argv, **kwargs):
            if argv[0].endswith("hyprctl"):
                raise subprocess.TimeoutExpired(cmd=argv[0], timeout=0.5)
            return MagicMock(
                returncode=0, stdout="X=1\nY=2\n", stderr=""
            )

        with patch("shutil.which", side_effect=fake_which), patch(
            "wayland_automation.mouse_position.subprocess.run",
            side_effect=fake_run,
        ):
            report = PositionReport()
            tracker = CursorPositionTracker(
                backends=(HyprctlAdapter, XdotoolAdapter),
                initial_read_timeout=0,
                report=report,
            )
            pos = tracker.snapshot()

        self.assertEqual(pos, (1, 2))
        self.assertEqual(
            report.failures[0].reason, FailureReason.READ_TIMEOUT
        )

    @patch.dict("os.environ", {"DISPLAY": ":0"})
    def test_every_command_breaks_raises_summary(self):
        with patch("shutil.which", return_value="/usr/bin/hyprctl"), patch(
            "wayland_automation.mouse_position.subprocess.run",
            return_value=MagicMock(returncode=1, stdout="", stderr="boom"),
        ):
            with self.assertRaises(NoBackendAvailableError) as ctx:
                CursorPositionTracker(
                    backends=(HyprctlAdapter,), initial_read_timeout=0
                ).snapshot()
        self.assertIn("hyprctl", str(ctx.exception))
        self.assertIn("read_error", str(ctx.exception))
        self.assertIn("boom", str(ctx.exception))


class TestFailureRecordsAndCompatibility(unittest.TestCase):
    def test_failure_render_includes_all_fields(self):
        failure = BackendFailure(
            "hyprctl",
            FailureReason.READ_TIMEOUT,
            "took too long",
            phase="read",
        )
        rendered = failure.render()
        self.assertIn("hyprctl", rendered)
        self.assertIn("read_timeout", rendered)
        self.assertIn("took too long", rendered)
        self.assertIn("read", rendered)

    def test_report_summary_and_skipped(self):
        report = PositionReport()
        report.failures.append(
            BackendFailure("a", FailureReason.BINARY_MISSING, "x", phase="probe")
        )
        report.failures.append(
            BackendFailure("b", FailureReason.EMPTY_RESULT, "y", phase="read")
        )
        report.active_backend = "b"
        self.assertEqual([f.backend for f in report.skipped], ["a"])
        summary = report.failure_summary()
        self.assertIn("a", summary)
        self.assertIn("b", summary)
        self.assertEqual(PositionReport().failure_summary(), "(no failures recorded)")

    def test_default_backend_order_preserved(self):
        self.assertEqual(
            tuple(b.name for b in (c() for c in DEFAULT_BACKEND_ORDER)),
            ("hyprctl", "wl-find-cursor", "kwin", "xdotool", "evdev"),
        )

    def test_legacy_pick_backend_shim(self):
        healthy = make_fake_backend(
            "legacy-ok", persistent=True, initial_pos=(6, 7)
        )
        with patch(
            "wayland_automation.mouse_position.DEFAULT_BACKEND_ORDER",
            (healthy,),
        ):
            name, getter, controller = pick_backend_and_start()
        self.assertEqual(name, "legacy-ok")
        self.assertTrue(callable(getter))
        self.assertEqual(getter(), (6, 7))
        # Snapshot-style backends still report a None controller.
        snap = make_fake_backend("legacy-snap", initial_pos=(8, 9))
        with patch(
            "wayland_automation.mouse_position.DEFAULT_BACKEND_ORDER",
            (snap,),
        ):
            name, getter, controller = pick_backend_and_start()
        self.assertEqual(name, "legacy-snap")
        self.assertIsNone(controller)
        self.assertEqual(getter(), (8, 9))

    def test_legacy_shim_returns_none_tuple_when_dead(self):
        dead = make_fake_backend(
            "dead",
            probe_result=(False, FailureReason.UNAVAILABLE, "x"),
        )
        with patch(
            "wayland_automation.mouse_position.DEFAULT_BACKEND_ORDER",
            (dead,),
        ):
            self.assertEqual(pick_backend_and_start(), (None, None, None))


if __name__ == "__main__":
    unittest.main()
