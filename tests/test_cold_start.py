"""Tests for cold_start two-level (foreground + session tab) verification."""

from __future__ import annotations

import pytest

from layernav_android.cold_start import (
    FAILURE_APP_NOT_STARTED,
    FAILURE_TAB_VERIFY_FAILED,
    cold_start_app_from_launcher,
    dock_app_icon_coords,
)

WECHAT = "com.tencent.mm"
TAB_X, TAB_Y = 108, 2192


# ── Mock ADB ──────────────────────────────────────────────────────────────────


class MockAdb:
    """Minimal ADB mock: configurable foreground app, records taps/shell."""

    def __init__(self, foreground: str = WECHAT) -> None:
        self._foreground = foreground
        self.taps: list[tuple[int, int]] = []
        self.shell_commands: list[list[str]] = []

    def foreground_package(self) -> str:
        return self._foreground

    def tap(self, x: int, y: int) -> None:
        self.taps.append((x, y))

    def screencap(self) -> bytes:
        return b"\x89PNG"

    def key_event(self, code: int) -> None:
        pass

    def swipe(self, *args, **kwargs) -> None:
        pass

    def _run(self, args: list[str]) -> str:
        self.shell_commands.append(list(args))
        if args[:3] == ["shell", "wm", "size"]:
            return "Physical size: 1080x2400"
        return ""


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Cold-start is full of real sleeps; neutralise them for tests."""
    monkeypatch.setattr(
        "layernav_android.cold_start.time.sleep", lambda *_a, **_k: None,
    )


def _launch_commands(adb: MockAdb) -> list[list[str]]:
    return [c for c in adb.shell_commands if c[:1] == ["shell"]]


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestSessionTabVerify:
    def test_tab_verify_fails_once_then_retry_succeeds(self):
        """monkey 路径：首次 Tab 校验失败 → 重试点中 → 整体成功。"""
        adb = MockAdb()
        verify_results = [False, True]
        verify_calls: list[int] = []

        def verify(_adb) -> bool:
            verify_calls.append(1)
            return verify_results.pop(0) if verify_results else True

        result = cold_start_app_from_launcher(
            adb, WECHAT,
            app_name="wechat", M=4, N=3,
            session_tab_x=TAB_X, session_tab_y=TAB_Y,
            session_tab_verify=verify,
            session_tab_retries=2,
        )

        assert result.ok is True
        assert bool(result) is True  # legacy truthiness兼容
        assert result.path == "monkey"
        assert result.foreground_ready is True
        assert result.tab_verified is True
        assert result.failure is None
        # 初次点击 + 1 次重试，共 2 次 Tab 点击
        assert adb.taps.count((TAB_X, TAB_Y)) == 2
        assert len(verify_calls) == 2

    def test_all_three_paths_tab_verify_fail(self):
        """三条启动路径全部 Tab 校验失败 → tab_verify_failed，可区分。"""
        adb = MockAdb()

        def verify(_adb) -> bool:
            return False

        result = cold_start_app_from_launcher(
            adb, WECHAT,
            app_name="wechat", M=4, N=3,
            session_tab_x=TAB_X, session_tab_y=TAB_Y,
            session_tab_verify=verify,
            session_tab_retries=1,
            allow_reboot=False,
        )

        assert result.ok is False
        assert bool(result) is False
        assert result.failure == FAILURE_TAB_VERIFY_FAILED
        assert result.foreground_ready is True   # 前台已就绪，只是 Tab 没生效
        assert result.tab_verified is False

        cmds = _launch_commands(adb)
        # 三条路径都真正尝试过：monkey、am start、Dock 图标点击
        assert any(c[:2] == ["shell", "monkey"] for c in cmds)
        assert any(c[:3] == ["shell", "am", "start"] for c in cmds)
        dock_x, dock_y = dock_app_icon_coords(1080, 2400, 1.0, app_name="wechat", M=4, N=3)
        assert (dock_x, dock_y) in adb.taps
        # 没有触发 reboot 兜底
        assert not any(c == ["reboot"] for c in adb.shell_commands)

    def test_no_tab_coords_keeps_legacy_behaviour(self):
        """未传 Tab 坐标：仅校验前台包名，verify 回调不被调用。"""
        adb = MockAdb()
        verify_calls: list[int] = []

        def verify(_adb) -> bool:
            verify_calls.append(1)
            return False  # 即使返回 False 也不应被调用

        result = cold_start_app_from_launcher(
            adb, WECHAT,
            app_name="wechat", M=4, N=3,
            session_tab_verify=verify,
        )

        assert result.ok is True
        assert result.path == "monkey"
        assert result.tab_verified is None
        assert verify_calls == []
        assert adb.taps == []  # 没有 Tab 点击，也没有走到 Dock 路径

    def test_app_never_starts_distinguishable(self):
        """应用根本没起来 → app_not_started，与 Tab 校验失败可区分。"""
        adb = MockAdb(foreground="com.android.launcher3")

        result = cold_start_app_from_launcher(
            adb, WECHAT,
            app_name="wechat", M=4, N=3,
            session_tab_x=TAB_X, session_tab_y=TAB_Y,
            session_tab_verify=lambda _a: True,
            allow_reboot=False,
        )

        assert result.ok is False
        assert result.failure == FAILURE_APP_NOT_STARTED
        assert result.foreground_ready is False
        assert result.tab_verified is None
