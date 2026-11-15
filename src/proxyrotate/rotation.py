"""Proxy pool state, rotation strategies, and the rotation loop.

The pool tracks per-proxy health with a cooldown so a failed proxy is
not selected again until the cooldown expires. Selection always skips
proxies that are cooling down; if every proxy is cooling down the caller
is told to wait instead of picking a known-bad proxy.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from .models import Proxy
from .singbox import SingBoxManager


class RotationStrategy(Protocol):
    """Decides when it is time to rotate.

    Receives elapsed seconds since the last rotation and an
    :class:`~proxyrotate.singbox.ActivityDelta` describing traffic since
    then. Future strategies plug in here without touching the loop.
    """

    def should_rotate(self, elapsed: float, activity) -> bool: ...


@dataclass(slots=True)
class TimeRotationStrategy:
    interval: float

    def should_rotate(self, elapsed: float, activity) -> bool:
        return self.interval > 0 and elapsed >= self.interval


@dataclass(slots=True)
class ConnectionsRotationStrategy:
    """Rotate once N new inbound connections were observed."""

    max_connections: int

    def should_rotate(self, elapsed: float, activity) -> bool:
        return self.max_connections > 0 and activity.new_connections >= self.max_connections


@dataclass(slots=True)
class BytesRotationStrategy:
    """Rotate once N bytes flowed through the inbound."""

    max_bytes: int

    def should_rotate(self, elapsed: float, activity) -> bool:
        return self.max_bytes > 0 and activity.new_bytes >= self.max_bytes


@dataclass(slots=True)
class CombinedStrategy:
    """OR-combination: rotates when its first child strategy triggers."""

    strategies: tuple

    def __init__(self, *strategies):
        self.strategies = tuple(strategies)

    def should_rotate(self, elapsed: float, activity) -> bool:
        return any(s.should_rotate(elapsed, activity) for s in self.strategies)


@dataclass(slots=True)
class _State:
    failed_at: float | None = None
    streak: int = 0


class ProxyPool:
    """Ordered proxies plus failure tracking with escalating cooldowns.

    Each consecutive failure doubles the proxy's cooldown
    (``cooldown * 2^(streak-1)``), capped at ``cooldown_cap``. A
    successful selection resets the streak.
    """

    def __init__(self, proxies: list[Proxy], cooldown: float = 30.0,
                 clock=time.monotonic, cooldown_cap: float = 600.0):
        if not proxies:
            raise ValueError("proxy pool must not be empty")
        self.proxies = list(proxies)
        self.cooldown = cooldown
        self.cooldown_cap = cooldown_cap
        self._clock = clock
        self._states: dict[int, _State] = {}

    # -- health bookkeeping --------------------------------------------------

    def mark_failed(self, proxy: Proxy) -> None:
        index = self._index(proxy)
        state = self._states.setdefault(index, _State())
        state.streak += 1
        state.failed_at = self._clock()

    def mark_ok(self, proxy: Proxy) -> None:
        self._states.pop(self._index(proxy), None)

    def effective_cooldown(self, proxy: Proxy) -> float:
        state = self._states.get(self._index(proxy))
        if state is None or state.streak < 1:
            return self.cooldown
        return min(self.cooldown * (2 ** (state.streak - 1)), self.cooldown_cap)

    def in_cooldown(self, proxy: Proxy) -> bool:
        state = self._states.get(self._index(proxy))
        if state is None or state.failed_at is None:
            return False
        return (self._clock() - state.failed_at) < self.effective_cooldown(proxy)

    def healthy_count(self) -> int:
        return sum(1 for p in self.proxies if not self.in_cooldown(p))

    def retry_delay(self) -> float:
        """Seconds to wait before any proxy leaves cooldown (0 if none)."""
        now = self._clock()
        remaining = [
            self.effective_cooldown(p) - (now - s.failed_at)
            for p, s in self._per_index_states()
            if s.failed_at is not None
        ]
        active = [r for r in remaining if r > 0]
        return min(active) if active else 0.0

    def _per_index_states(self):
        for index, state in self._states.items():
            yield self.proxies[index], state

    # -- selection -----------------------------------------------------------

    def candidates_from(self, start_index: int) -> list[tuple[int, Proxy]]:
        """Eligible proxies in circular order starting after start_index."""
        count = len(self.proxies)
        ordered: list[tuple[int, Proxy]] = []
        for offset in range(1, count):
            index = (start_index + offset) % count
            proxy = self.proxies[index]
            if not self.in_cooldown(proxy):
                ordered.append((index, proxy))
        return ordered

    def first_candidate(self) -> tuple[int, Proxy] | None:
        eligible = [(i, p) for i, p in enumerate(self.proxies) if not self.in_cooldown(p)]
        return eligible[0] if eligible else None

    def _index(self, proxy: Proxy) -> int:
        key = proxy.dedupe_key()
        for index, candidate in enumerate(self.proxies):
            if candidate.dedupe_key() == key:
                return index
        raise KeyError("proxy not in pool")


class Rotator:
    """Switches sing-box between proxies and verifies each switch."""

    def __init__(
        self,
        manager: SingBoxManager,
        pool: ProxyPool,
        strategy: RotationStrategy,
        verify_timeout: float = 3.0,
        verify_enabled: bool = True,
        logger=None,
        activity_monitor=None,
    ):
        from .logging import get_logger

        self.manager = manager
        self.pool = pool
        self.strategy = strategy
        self.verify_timeout = verify_timeout
        self.verify_enabled = verify_enabled
        self.activity_monitor = activity_monitor
        self.log = logger or get_logger("rotation")
        self.current_index: int | None = None

    def select_initial(self) -> int | None:
        """Pick and activate the first usable proxy. Returns index or None."""
        start = self.pool.first_candidate()
        if start is None:
            return None
        candidates = [start] + self.pool.candidates_from(start[0])
        self._try_candidates(candidates, previous_tag=None)
        return self.current_index

    def rotate_once(self) -> bool:
        """Move to the next healthy proxy. Returns True on success."""
        if self.current_index is None:
            return self.select_initial() is not None

        current_tag = f"proxy-{self.current_index}"
        candidates = self.pool.candidates_from(self.current_index)
        return self._try_candidates(candidates, previous_tag=current_tag)

    def _try_candidates(self, candidates: list[tuple[int, Proxy]],
                        previous_tag: str | None) -> bool:
        for index, proxy in candidates:
            tag = f"proxy-{index}"
            try:
                self.manager.select(tag)
                if self.verify_enabled:
                    self.manager.verify_current(timeout=self.verify_timeout)
            except Exception as exc:
                self.log.warning(
                    "proxy %s unusable (%s); skipping", proxy.redacted_url(), exc
                )
                self.pool.mark_failed(proxy)
                continue

            self.pool.mark_ok(proxy)
            self.current_index = index
            if self.activity_monitor is not None:
                self.activity_monitor.reset()
            self.log.info("active proxy: %s", proxy.redacted_url())
            return True

        if previous_tag is not None:
            try:
                self.manager.select(previous_tag)
            except Exception as exc:
                self.log.error("could not restore previous proxy: %s", exc)

        return False

    def run(self, app_running, stop_event, poll_interval: float = 0.25) -> None:
        """Rotate periodically while the application runs.

        app_running is a callable returning True while the target process
        is alive. stop_event wakes the loop immediately on shutdown.
        """
        from .singbox import ActivityDelta

        started = time.monotonic()

        while app_running() and not stop_event.is_set():
            stop_event.wait(poll_interval)
            if stop_event.is_set() or not app_running():
                break

            if self.activity_monitor is not None:
                self.activity_monitor.poll()
                activity = self.activity_monitor.activity
            else:
                activity = ActivityDelta()

            elapsed = time.monotonic() - started
            if not self.strategy.should_rotate(elapsed, activity):
                continue

            if self.rotate_once():
                started = time.monotonic()
            else:
                delay = max(self.pool.retry_delay(), 1.0)
                self.log.debug("no eligible proxy; retrying in %.1fs", delay)
                stop_event.wait(delay)
