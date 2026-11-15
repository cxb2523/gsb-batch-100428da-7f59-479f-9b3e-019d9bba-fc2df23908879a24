import threading
import time

import pytest

from proxyrotate.models import Proxy
from proxyrotate.rotation import (
    BytesRotationStrategy,
    CombinedStrategy,
    ConnectionsRotationStrategy,
    ProxyPool,
    Rotator,
    TimeRotationStrategy,
)
from proxyrotate.singbox import ActivityDelta, SingBoxError


class RecordingMonitor:
    """Scriptable stand-in for ActivityMonitor."""

    def __init__(self):
        self.delta = ActivityDelta()
        self.polled = 0
        self.resets = 0

    def poll(self):
        self.polled += 1

    @property
    def activity(self):
        return self.delta

    def reset(self):
        self.resets += 1
        self.delta = ActivityDelta()


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def p(n: int) -> Proxy:
    return Proxy.parse(f"http://10.0.0.{n}:8080")


class TestTimeRotationStrategy:
    def test_fires_at_interval(self):
        strategy = TimeRotationStrategy(interval=5.0)
        assert not strategy.should_rotate(4.99, ActivityDelta())
        assert strategy.should_rotate(5.0, ActivityDelta())
        assert strategy.should_rotate(6.0, ActivityDelta())

    def test_zero_disables(self):
        strategy = TimeRotationStrategy(interval=0)
        assert not strategy.should_rotate(9999, ActivityDelta())


class TestActivityStrategies:
    def test_connections_strategy_triggers_at_threshold(self):
        strategy = ConnectionsRotationStrategy(max_connections=3)
        assert not strategy.should_rotate(0, ActivityDelta(new_connections=2))
        assert strategy.should_rotate(0, ActivityDelta(new_connections=3))
        assert strategy.should_rotate(0, ActivityDelta(new_connections=7))

    def test_connections_ignores_elapsed(self):
        strategy = ConnectionsRotationStrategy(max_connections=100)
        assert not strategy.should_rotate(9999, ActivityDelta())

    def test_bytes_strategy_triggers_at_threshold(self):
        strategy = BytesRotationStrategy(max_bytes=1024)
        assert not strategy.should_rotate(0, ActivityDelta(new_bytes=1023))
        assert strategy.should_rotate(0, ActivityDelta(new_bytes=1024))

    def test_combined_or_first_hit_wins(self):
        combined = CombinedStrategy(
            TimeRotationStrategy(interval=60),
            ConnectionsRotationStrategy(max_connections=5),
        )
        # neither hit
        assert not combined.should_rotate(30, ActivityDelta(new_connections=4))
        # time hits first
        assert combined.should_rotate(60, ActivityDelta(new_connections=4))
        # connections hits before time
        assert combined.should_rotate(10, ActivityDelta(new_connections=5))

    def test_combined_empty_never_rotates(self):
        combined = CombinedStrategy()
        assert not combined.should_rotate(9999, ActivityDelta(99, 99))


class TestProxyPool:
    def test_candidates_circular_order(self):
        pool = ProxyPool([p(1), p(2), p(3)], cooldown=30)
        candidates = pool.candidates_from(start_index=2)
        assert [i for i, _ in candidates] == [0, 1]
        assert [proxy.host for _, proxy in candidates] == ["10.0.0.1", "10.0.0.2"]

    def test_first_candidate(self):
        pool = ProxyPool([p(1), p(2)], cooldown=30)
        index, proxy = pool.first_candidate()
        assert (index, proxy.host) == (0, "10.0.0.1")

    def test_cooldown_excludes_proxy(self):
        clock = FakeClock()
        pool = ProxyPool([p(1), p(2), p(3)], cooldown=30, clock=clock)

        pool.mark_failed(p(2))
        assert pool.in_cooldown(p(2))
        # candidates_from(0) excludes the current (index 0) and the
        # cooling-down proxy at index 1.
        indexes = [i for i, _ in pool.candidates_from(start_index=0)]
        assert indexes == [2]

    def test_cooldown_expires(self):
        clock = FakeClock()
        pool = ProxyPool([p(1), p(2)], cooldown=30, clock=clock)

        pool.mark_failed(p(1))
        clock.advance(29.9)
        assert pool.in_cooldown(p(1))
        clock.advance(0.2)
        assert not pool.in_cooldown(p(1))

    def test_mark_ok_clears_failure(self):
        clock = FakeClock()
        pool = ProxyPool([p(1), p(2)], cooldown=30, clock=clock)
        pool.mark_failed(p(1))
        pool.mark_ok(p(1))
        assert not pool.in_cooldown(p(1))

    def test_retry_delay(self):
        clock = FakeClock()
        pool = ProxyPool([p(1), p(2)], cooldown=30, clock=clock)

        assert pool.retry_delay() == 0.0
        pool.mark_failed(p(1))
        clock.advance(10)
        assert pool.retry_delay() == pytest.approx(20.0)
        clock.advance(21)
        assert pool.retry_delay() == 0.0

    def test_all_cooling_down_no_candidates(self):
        clock = FakeClock()
        pool = ProxyPool([p(1), p(2)], cooldown=30, clock=clock)
        pool.mark_failed(p(1))
        pool.mark_failed(p(2))
        assert pool.first_candidate() is None
        assert pool.candidates_from(0) == []
        assert pool.healthy_count() == 0

    def test_healthy_count(self):
        clock = FakeClock()
        pool = ProxyPool([p(1), p(2), p(3)], cooldown=30, clock=clock)
        pool.mark_failed(p(2))
        assert pool.healthy_count() == 2

    def test_identity_is_by_value_not_object(self):
        clock = FakeClock()
        pool = ProxyPool([Proxy.parse("http://h:1")], cooldown=30, clock=clock)
        pool.mark_failed(Proxy.parse("http://h:1"))
        assert pool.in_cooldown(Proxy.parse("http://h:1"))

    def test_empty_pool_rejected(self):
        with pytest.raises(ValueError):
            ProxyPool([])


class TestEscalatingCooldown:
    """Repeated failures double the cooldown, capped; success resets."""

    def make_pool(self, **kwargs):
        clock = FakeClock()
        defaults = dict(proxies=[p(1), p(2), p(3)], cooldown=30, clock=clock,
                        cooldown_cap=600)
        defaults.update(kwargs)
        return ProxyPool(**defaults), clock

    def test_first_failure_uses_base_cooldown(self):
        pool, clock = self.make_pool()
        pool.mark_failed(p(1))
        clock.advance(29)
        assert pool.in_cooldown(p(1))
        clock.advance(2)
        assert not pool.in_cooldown(p(1))

    def test_second_failure_doubles(self):
        pool, clock = self.make_pool()
        pool.mark_failed(p(1))   # streak 1 -> 30s
        clock.advance(31)
        pool.mark_failed(p(1))   # streak 2 -> 60s
        clock.advance(45)
        assert pool.in_cooldown(p(1))   # 45 < 60
        clock.advance(20)
        assert not pool.in_cooldown(p(1))

    def test_streak_keeps_growing_until_cap(self):
        pool, clock = self.make_pool()
        streaks = []
        for _ in range(6):       # 30,60,120,240,480,960->capped 600
            pool.mark_failed(p(1))
            streaks.append(pool.effective_cooldown(p(1)))
            clock.advance(pool.effective_cooldown(p(1)) + 0.5)
        assert streaks == [30, 60, 120, 240, 480, 600]
        # stays capped
        pool.mark_failed(p(1))
        assert pool.effective_cooldown(p(1)) == 600

    def test_success_resets_streak(self):
        pool, clock = self.make_pool()
        pool.mark_failed(p(1))
        pool.mark_failed(p(1))   # streak 2
        pool.mark_ok(p(1))
        pool.mark_failed(p(1))   # back to streak 1
        assert pool.effective_cooldown(p(1)) == 30

    def test_retry_delay_respects_effective_cooldown(self):
        pool, clock = self.make_pool()
        pool.mark_failed(p(1))   # 30
        clock.advance(40)
        pool.mark_failed(p(1))   # 60 from t=40
        clock.advance(10)
        assert pool.retry_delay() == pytest.approx(50)

    def test_other_proxies_unaffected(self):
        pool, clock = self.make_pool()
        pool.mark_failed(p(1))
        pool.mark_failed(p(1))   # p1 escalated
        assert pool.effective_cooldown(p(2)) == 30


class FakeManager:
    """Records select calls; can be scripted to fail."""

    def __init__(self, fail_select=(), fail_verify=()):
        self.selected: list[str] = []
        self.fail_select = set(fail_select)
        self.fail_verify = set(fail_verify)
        self.verifications = 0

    def select(self, tag: str) -> None:
        self.selected.append(tag)
        if tag in self.fail_select:
            raise SingBoxError("select failed")

    def verify_current(self, timeout: float = 3.0) -> float:
        self.verifications += 1
        if self.selected and self.selected[-1] in self.fail_verify:
            raise SingBoxError("verify failed")
        return 12.5


class TestRotator:
    def make_rotator(self, proxies, manager, interval=60.0, **kwargs):
        pool = ProxyPool(proxies, cooldown=30, clock=FakeClock())
        return Rotator(manager, pool, TimeRotationStrategy(interval), **kwargs), pool

    def test_initial_selection_prefers_first_working(self):
        manager = FakeManager(fail_verify=("proxy-0",))
        rotator, pool = self.make_rotator([p(1), p(2)], manager)
        chosen = rotator.select_initial()

        assert chosen == 1
        assert manager.selected == ["proxy-0", "proxy-1"]
        assert pool.in_cooldown(p(1))

    def test_initial_none_when_everything_fails(self):
        manager = FakeManager(fail_verify=("proxy-0", "proxy-1"))
        rotator, _ = self.make_rotator([p(1), p(2)], manager)
        assert rotator.select_initial() is None

    def test_rotate_once_moves_to_next_healthy(self):
        manager = FakeManager(fail_verify=("proxy-1",))
        rotator, pool = self.make_rotator([p(1), p(2), p(3)], manager)

        assert rotator.select_initial() == 0
        assert rotator.rotate_once() is True
        # proxy-1 skipped (dead), landed on proxy-2
        assert rotator.current_index == 2
        assert manager.selected == ["proxy-0", "proxy-1", "proxy-2"]
        assert pool.in_cooldown(p(2))

    def test_rotate_wraps_to_start(self):
        manager = FakeManager()
        rotator, _ = self.make_rotator([p(1), p(2), p(3)], manager)
        rotator.select_initial()
        rotator.rotate_once()  # -> 1
        rotator.rotate_once()  # -> 2
        rotator.rotate_once()  # wraps -> 0
        assert rotator.current_index == 0
        assert manager.selected[-1] == "proxy-0"

    def test_rotate_restores_previous_when_all_fail(self):
        manager = FakeManager()
        rotator, pool = self.make_rotator([p(1), p(2)], manager)
        rotator.select_initial()  # proxy-0 active

        # Everything now fails verification.
        manager.fail_verify.update({"proxy-1", "proxy-0"})
        assert rotator.rotate_once() is False
        # Selector restored to the previous working tag as best effort.
        assert manager.selected[-1] == "proxy-0"

    def test_rotation_loop_stops_with_app(self):
        manager = FakeManager()
        rotator, _ = self.make_rotator([p(1)], manager, interval=0.001)

        stop_event = threading.Event()
        calls = {"n": 0}

        def app_running():
            calls["n"] += 1
            # app_running is checked twice per loop iteration (loop entry
            # and after the poll wait), so allow plenty of True answers.
            return calls["n"] <= 12

        rotator.run(app_running=app_running, stop_event=stop_event, poll_interval=0.005)
        # Loop exited because the app stopped; rotations happened.
        assert len(manager.selected) >= 2

    def test_rotation_loop_respects_stop_event(self):
        manager = FakeManager()
        rotator, _ = self.make_rotator([p(1)], manager, interval=3600)

        stop_event = threading.Event()
        stop_event.set()
        rotator.run(app_running=lambda: True, stop_event=stop_event, poll_interval=0.005)
        assert manager.selected == []  # never rotated

    def test_wait_when_all_proxies_dead(self):
        manager = FakeManager()
        rotator, _ = self.make_rotator([p(1)], manager, interval=0.001)
        rotator.select_initial()

        manager.fail_verify.add("proxy-0")

        # A real event (so waits actually pass time), wrapped to record.
        real_event = threading.Event()
        waits: list[float | None] = []

        class RecordingEvent:
            def wait(self, timeout=None):
                waits.append(timeout)
                return real_event.wait(timeout)

            def is_set(self):
                return real_event.is_set()

        calls = {"n": 0}

        def app_running():
            calls["n"] += 1
            # Two checks per iteration; enough for one failed rotation
            # followed by the back-off wait.
            return calls["n"] <= 4

        rotator.run(app_running=app_running, stop_event=RecordingEvent(),
                    poll_interval=0.005)
        # Backed off instead of hammering the dead proxy.
        assert any(t and t >= 1.0 for t in waits)

    def test_fallback_waits_pool_retry_delay_cooldown(self):
        # Two proxies: proxy-0 active; when rotation triggers, proxy-1
        # fails verification and the selector is restored to proxy-0.
        # The loop must then sleep for the pool's cooldown, not re-fire
        # immediately on the already-satisfied connection threshold.
        manager = FakeManager(fail_verify=("proxy-1",))
        clock = FakeClock()
        pool = ProxyPool([p(1), p(2)], cooldown=30, clock=clock)
        monitor = RecordingMonitor()
        rotator = Rotator(
            manager, pool,
            ConnectionsRotationStrategy(max_connections=1),
            activity_monitor=monitor,
        )
        assert rotator.select_initial() == 0
        monitor.delta = ActivityDelta(new_connections=5)  # threshold hit

        waits: list[float] = []

        class ScriptedEvent:
            """Never set; records requested wait durations."""

            def wait(self, timeout=None):
                waits.append(timeout)
                return False

            def is_set(self):
                return False

        iterations = {"n": 0}

        def app_running():
            iterations["n"] += 1
            # One full loop iteration is enough to reach the back-off.
            return iterations["n"] <= 2

        rotator.run(app_running=app_running, stop_event=ScriptedEvent(),
                    poll_interval=0.0)

        # proxy-1 was tried once and marked failed...
        assert pool.in_cooldown(p(2))
        expected = pool.retry_delay()
        assert expected > 0
        # ...and the loop cooled down for exactly that long (the other
        # recorded wait is the zero-length poll cadence), rather than
        # retrying immediately at the satisfied connection threshold.
        cooldown_waits = [t for t in waits if t and t > 0.0]
        assert cooldown_waits == [expected]
        # Failed rotation did not trigger a second candidate sweep.
        assert manager.selected == ["proxy-0", "proxy-1", "proxy-0"]

    def test_cooldown_wait_wakes_early_on_stop_event(self):
        manager = FakeManager(fail_verify=("proxy-1",))
        clock = FakeClock()
        pool = ProxyPool([p(1), p(2)], cooldown=300, clock=clock)
        rotator = Rotator(
            manager, pool,
            ConnectionsRotationStrategy(max_connections=1),
            activity_monitor=RecordingMonitor(),
        )
        assert rotator.select_initial() == 0
        rotator.activity_monitor.delta = ActivityDelta(new_connections=9)

        stop_event = threading.Event()

        def app_running():
            return True

        def stop_soon():
            # Wake the loop well inside the 300s cooldown.
            stop_event.wait(0.2)
            stop_event.set()

        thread = threading.Thread(target=stop_soon, daemon=True)
        thread.start()
        started = time.monotonic()
        rotator.run(app_running=app_running, stop_event=stop_event,
                    poll_interval=0.0)
        elapsed = time.monotonic() - started
        thread.join(timeout=2)
        # Returned promptly instead of sitting out the full cooldown.
        assert elapsed < 5.0
        assert stop_event.is_set()


class TestActivityDrivenRotation:
    def make(self, proxies, strategy):
        manager = FakeManager()
        pool = ProxyPool(proxies, cooldown=30, clock=FakeClock())
        monitor = RecordingMonitor()
        rotator = Rotator(manager, pool, strategy,
                          activity_monitor=monitor)
        return rotator, monitor

    @staticmethod
    def run_briefly(rotator, iterations=3):
        real_event = threading.Event()

        class NeverSet:
            def wait(self, timeout=None):
                return real_event.wait(timeout)

            def is_set(self):
                return False

        calls = {"n": 0}

        def app_running():
            calls["n"] += 1
            return calls["n"] <= iterations * 2 + 1

        rotator.run(app_running=app_running, stop_event=NeverSet(),
                    poll_interval=0.005)

    def test_single_burst_rotates_once_then_waits(self):
        rotator, monitor = self.make([p(1), p(2)],
                                     ConnectionsRotationStrategy(max_connections=3))
        rotator.select_initial()
        assert monitor.resets == 1            # baseline after initial pick

        # One burst of exactly the threshold...
        monitor.delta = ActivityDelta(new_connections=3)
        self.run_briefly(rotator)

        # ...rotates once, then silence (reset cleared the counter).
        assert rotator.manager.selected == ["proxy-0", "proxy-1"]
        assert monitor.resets == 2
        assert rotator.current_index == 1

    def test_continuous_activity_keeps_rotating(self):
        class StickyMonitor:
            """Always reports fresh activity; counts resets."""

            def __init__(self):
                self.polled = 0
                self.resets = 0

            def poll(self):
                self.polled += 1

            @property
            def activity(self):
                return ActivityDelta(new_connections=1)

            def reset(self):
                self.resets += 1

        manager = FakeManager()
        pool = ProxyPool([p(1), p(2), p(3)], cooldown=30, clock=FakeClock())
        monitor = StickyMonitor()
        rotator = Rotator(manager, pool,
                          ConnectionsRotationStrategy(max_connections=1),
                          activity_monitor=monitor)
        rotator.select_initial()

        self.run_briefly(rotator, iterations=3)

        assert len(manager.selected) >= 3     # rotated on consecutive ticks
        assert monitor.resets >= 2

    def test_below_threshold_stays_put_but_polls(self):
        rotator, monitor = self.make([p(1), p(2)],
                                     ConnectionsRotationStrategy(max_connections=100))
        rotator.select_initial()
        monitor.delta = ActivityDelta(new_connections=3, new_bytes=500)

        self.run_briefly(rotator)

        assert rotator.manager.selected == ["proxy-0"]   # never rotated
        assert monitor.polled > 0                        # but activity was watched
        assert monitor.resets == 1

    def test_bytes_threshold_rotates(self):
        rotator, monitor = self.make(
            [p(1), p(2), p(3)],
            BytesRotationStrategy(max_bytes=1024),
        )
        rotator.select_initial()
        monitor.delta = ActivityDelta(new_bytes=2048)
        self.run_briefly(rotator)
        assert len(rotator.manager.selected) >= 2
