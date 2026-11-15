"""Generic cold-start: launch an Android app from the launcher home screen.

Three paths tried in priority order:

1. **monkey** — ``monkey -p <package> -c LAUNCHER 1`` (primary)
2. **am start Intent** — ``am start -a MAIN -c LAUNCHER <package>`` (backup
   for custom ROMs like MIUI / ColorOS that may restrict monkey)
3. **Dock icon tap** — calculated via ``dock_app_icon_coords`` (last resort,
   with 0.5 s pre‑wait and up to 2 retries)

After the app enters foreground, optionally taps a session tab to reach the
app's main content list (e.g. WeChat's bottom "微信" tab).  When a
``session_tab_verify`` callback is supplied, the tap is *verified* — a
foreground package match alone is no longer treated as success.

Screen dimensions are always auto‑detected via ``adb shell wm size``.

.. code-block:: python

    from layernav_android.cold_start import cold_start_app_from_launcher

    result = cold_start_app_from_launcher(
        adb, "com.tencent.mm",
        app_name="wechat", M=4, N=3,
        session_tab_x=108, session_tab_y=2192,
        session_tab_verify=lambda a: model.detect_layer(a, scale_w, "L1"),
    )
    if not result:
        print(result.failure)  # "app_not_started" | "tab_verify_failed"
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable

from layernav_android._protocol import AdbProtocol
from layernav_android.logging import get_logger

logger = get_logger(__name__)

_SZ_RE = re.compile(r"(\d{3,})\s*x\s*(\d{3,})")

APP_DEFAULTS: dict[str, dict[str, int]] = {
    "wechat": {"M": 4, "N": 3},
    "xhs":    {"M": 4, "N": 1},
}

_DOCK_RETRIES = 2
_DOCK_PRE_WAIT_S = 0.5

#: ``ColdStartResult.failure`` — the app never reached the foreground.
FAILURE_APP_NOT_STARTED = "app_not_started"
#: ``ColdStartResult.failure`` — foreground OK, but the session-tab
#: verification callback never confirmed the conversation list page.
FAILURE_TAB_VERIFY_FAILED = "tab_verify_failed"

# Per-launch-path outcomes (internal)
_PATH_OK = "ok"
_PATH_NOT_FOREGROUND = "not_foreground"
_PATH_TAB_FAILED = "tab_failed"


@dataclass(frozen=True)
class ColdStartResult:
    """Outcome of :func:`cold_start_app_from_launcher`.

    ``bool(result)`` is ``True`` iff the cold-start fully succeeded, so
    legacy callers written against the old ``bool`` return value
    (``if cold_start_app_from_launcher(...):``) keep working unchanged.

    Attributes:
        ok: full success (foreground + session tab verified if requested).
        foreground_ready: the package reached the foreground at some point.
        tab_verified: ``True``/``False`` when a session-tab check ran,
            ``None`` when no tab check was requested.
        failure: ``None`` on success, otherwise
            :data:`FAILURE_APP_NOT_STARTED` or :data:`FAILURE_TAB_VERIFY_FAILED`.
        path: which launch path succeeded (``"monkey"`` / ``"am_start"`` /
            ``"dock"`` / ``"monkey_after_reboot"`` / ``"dock_after_reboot"``).
    """

    ok: bool
    foreground_ready: bool = False
    tab_verified: bool | None = None
    failure: str | None = None
    path: str | None = None

    def __bool__(self) -> bool:
        return self.ok


def dock_app_icon_coords(
    screen_w: int,
    screen_h: int,
    scale_w: float,
    *,
    app_name: str = "wechat",
    M: int = 4,
    N: int | None = None,
) -> tuple[int, int]:
    """Calculate the centre of the *N*-th Dock slot (1‑indexed) in a Dock with *M* equal-width slots.

    - *M*: total number of Dock slots (default 4).
    - *N*: 1‑based slot index; defaults from ``APP_DEFAULTS`` (wechat→3, xhs→1).
    - Formula: ``x = round(W * (N - 0.5) / M)``.
    """
    if N is None:
        defaults = APP_DEFAULTS.get(app_name, {})
        N = defaults.get("N", 1)
        M = defaults.get("M", M)
    N = max(1, min(M, N))
    pad_x = max(12, screen_w // 8)
    dx = int(round(screen_w * (N - 0.5) / M))
    dx = max(pad_x, min(screen_w - pad_x, dx))
    dy = screen_h - max(48, int(round(52 * max(scale_w, 1e-6))))
    return dx, dy


def cold_start_app_from_launcher(
    adb: AdbProtocol,
    package: str,
    *,
    app_name: str = "wechat",
    M: int = 4,
    N: int | None = None,
    session_tab_x: int | None = None,
    session_tab_y: int | None = None,
    session_tab_verify: Callable[[AdbProtocol], bool] | None = None,
    session_tab_retries: int = 2,
    force_stop_before: bool = True,
    deadline_s: float = 25.0,
    allow_reboot: bool = False,
) -> ColdStartResult:
    """Cold-start *package* from the Android launcher and optionally tap a session tab.

    Screen dimensions are always auto‑detected via ``adb shell wm size``.

    Args:
        adb: :class:`AdbProtocol` client.
        package: Android package name (e.g. ``"com.tencent.mm"``).
        app_name: key in :data:`APP_DEFAULTS` — drives default *M* / *N*.
        M: number of Dock slots.
        N: 1‑based slot index where the app icon lives.
        session_tab_x, session_tab_y: if both are non-``None``, tap this
            coordinate after the app enters foreground (e.g. WeChat's
            「微信」 bottom tab).
        session_tab_verify: optional callback ``(adb) -> bool`` confirming
            the session tab actually took effect (e.g. the conversation
            list page is showing).  Only consulted when the tab
            coordinates are also given; without it the legacy
            foreground-only behaviour is kept.
        session_tab_retries: extra tab taps allowed per launch path when
            verification fails.
        force_stop_before: issue ``am force-stop`` before cold-start.
        deadline_s: maximum time budget for the whole cold-start.
        allow_reboot: if ``True`` and all three launch paths fail, issue
            ``adb reboot`` as a last-resort recovery, wait for the device
            to finish booting, then retry from path 1.

            .. warning::

               Reboot adds 60–120 s.  The device must be able to reach the
               home screen without manual intervention (no PIN / pattern
               lock).  Defaults to ``False``.

    Returns:
        :class:`ColdStartResult` — truthy on full success.  Inspect
        ``result.failure`` to distinguish :data:`FAILURE_APP_NOT_STARTED`
        (app never came up) from :data:`FAILURE_TAB_VERIFY_FAILED`
        (foreground ready but session tab never verified).
    """
    screen_w, screen_h = _resolve_screen_size(adb)
    scale_w = screen_w / 1080.0
    logger.info("cold_start: screen %dx%d scale_w=%.3f", screen_w, screen_h, scale_w)

    deadline = time.monotonic() + deadline_s

    if force_stop_before:
        logger.info("cold_start: force-stop %s", package)
        try:
            adb._run(["shell", "am", "force-stop", package])
        except Exception:
            logger.warning("cold_start: force-stop %s failed (non-fatal)", package)
        time.sleep(0.65)

    saw_tab_failure = False

    # -- path 1: monkey LAUNCHER --
    if _try_monkey(adb, package):
        outcome = _settle_after_launch(
            adb, package, "monkey", deadline=deadline,
            session_tab_x=session_tab_x, session_tab_y=session_tab_y,
            session_tab_verify=session_tab_verify,
            session_tab_retries=session_tab_retries,
        )
        if outcome == _PATH_OK:
            return _success_result("monkey", session_tab_x, session_tab_y, session_tab_verify)
        saw_tab_failure |= outcome == _PATH_TAB_FAILED

    # -- path 2: am start Intent (backup for custom ROMs) --
    if _try_am_start(adb, package):
        outcome = _settle_after_launch(
            adb, package, "am_start", deadline=deadline,
            session_tab_x=session_tab_x, session_tab_y=session_tab_y,
            session_tab_verify=session_tab_verify,
            session_tab_retries=session_tab_retries,
        )
        if outcome == _PATH_OK:
            return _success_result("am_start", session_tab_x, session_tab_y, session_tab_verify)
        saw_tab_failure |= outcome == _PATH_TAB_FAILED

    # -- path 3: Dock icon tap (last resort, with pre-wait + retry) --
    dx, dy = dock_app_icon_coords(
        screen_w, screen_h, scale_w, app_name=app_name, M=M, N=N,
    )
    outcome = _try_dock_tap_with_retry(
        adb, package, dx, dy, session_tab_x, session_tab_y,
        session_tab_verify=session_tab_verify,
        session_tab_retries=session_tab_retries,
    )
    if outcome == _PATH_OK:
        return _success_result("dock", session_tab_x, session_tab_y, session_tab_verify)
    saw_tab_failure |= outcome == _PATH_TAB_FAILED

    # -- path 4: adb reboot (opt-in, very slow) --
    if not allow_reboot:
        return _failure_result(
            saw_tab_failure, session_tab_x, session_tab_y, session_tab_verify,
        )

    logger.warning("cold_start: all 3 launch paths failed, rebooting device")
    try:
        adb._run(["reboot"])
    except Exception as exc:
        logger.error("cold_start: adb reboot failed (%s)", exc)
        return _failure_result(
            saw_tab_failure, session_tab_x, session_tab_y, session_tab_verify,
        )

    # Wait for device to disconnect + come back
    time.sleep(5)
    _wait_for_device(adb, timeout_s=90)
    _wait_for_boot_completed(adb, timeout_s=60)
    # Retry path 1 after reboot
    screen_w2, screen_h2 = _resolve_screen_size(adb)
    scale_w2 = screen_w2 / 1080.0
    if force_stop_before:
        try:
            adb._run(["shell", "am", "force-stop", package])
        except Exception:
            pass
        time.sleep(0.65)
    if _try_monkey(adb, package):
        outcome = _settle_after_launch(
            adb, package, "monkey_after_reboot", deadline=float("inf"),
            session_tab_x=session_tab_x, session_tab_y=session_tab_y,
            session_tab_verify=session_tab_verify,
            session_tab_retries=session_tab_retries,
        )
        if outcome == _PATH_OK:
            logger.info("cold_start: succeeded after reboot")
            return _success_result(
                "monkey_after_reboot", session_tab_x, session_tab_y, session_tab_verify,
            )
        saw_tab_failure |= outcome == _PATH_TAB_FAILED
    dx2, dy2 = dock_app_icon_coords(
        screen_w2, screen_h2, scale_w2, app_name=app_name, M=M, N=N,
    )
    outcome = _try_dock_tap_with_retry(
        adb, package, dx2, dy2, session_tab_x, session_tab_y,
        session_tab_verify=session_tab_verify,
        session_tab_retries=session_tab_retries,
    )
    if outcome == _PATH_OK:
        logger.info("cold_start: succeeded after reboot + Dock")
        return _success_result(
            "dock_after_reboot", session_tab_x, session_tab_y, session_tab_verify,
        )
    saw_tab_failure |= outcome == _PATH_TAB_FAILED

    return _failure_result(
        saw_tab_failure, session_tab_x, session_tab_y, session_tab_verify,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_screen_size(adb: AdbProtocol) -> tuple[int, int]:
    out = adb._run(["shell", "wm", "size"])
    m = _SZ_RE.search(out)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 1080, 1920


def _try_monkey(adb: AdbProtocol, package: str) -> bool:
    logger.info("cold_start: monkey LAUNCHER for %s", package)
    try:
        adb._run(
            ["shell", "monkey", "-p", package, "-c",
             "android.intent.category.LAUNCHER", "1"],
        )
        return True
    except Exception as exc:
        logger.warning("cold_start: monkey failed (%s)", exc)
        return False


def _try_am_start(adb: AdbProtocol, package: str) -> bool:
    logger.info("cold_start: am start LAUNCHER for %s", package)
    try:
        adb._run(
            ["shell", "am", "start",
             "-a", "android.intent.action.MAIN",
             "-c", "android.intent.category.LAUNCHER",
             package],
        )
        return True
    except Exception as exc:
        logger.warning("cold_start: am start failed (%s)", exc)
        return False


def _tap_session_tab(
    adb: AdbProtocol,
    session_tab_x: int | None,
    session_tab_y: int | None,
) -> None:
    if session_tab_x is not None and session_tab_y is not None:
        logger.info("cold_start: tap session tab (%d, %d)", session_tab_x, session_tab_y)
        adb.tap(session_tab_x, session_tab_y)
        time.sleep(0.55)


def _tab_check_requested(
    session_tab_x: int | None,
    session_tab_y: int | None,
    session_tab_verify: Callable[[AdbProtocol], bool] | None,
) -> bool:
    return (
        session_tab_x is not None
        and session_tab_y is not None
        and session_tab_verify is not None
    )


def _success_result(
    path: str,
    session_tab_x: int | None,
    session_tab_y: int | None,
    session_tab_verify: Callable[[AdbProtocol], bool] | None,
) -> ColdStartResult:
    return ColdStartResult(
        ok=True,
        foreground_ready=True,
        tab_verified=(
            True
            if _tab_check_requested(session_tab_x, session_tab_y, session_tab_verify)
            else None
        ),
        path=path,
    )


def _failure_result(
    saw_tab_failure: bool,
    session_tab_x: int | None,
    session_tab_y: int | None,
    session_tab_verify: Callable[[AdbProtocol], bool] | None,
) -> ColdStartResult:
    if saw_tab_failure:
        return ColdStartResult(
            ok=False,
            foreground_ready=True,
            tab_verified=False,
            failure=FAILURE_TAB_VERIFY_FAILED,
        )
    return ColdStartResult(
        ok=False,
        foreground_ready=False,
        tab_verified=None,
        failure=FAILURE_APP_NOT_STARTED,
    )


def _settle_after_launch(
    adb: AdbProtocol,
    package: str,
    path_name: str,
    *,
    deadline: float,
    session_tab_x: int | None,
    session_tab_y: int | None,
    session_tab_verify: Callable[[AdbProtocol], bool] | None,
    session_tab_retries: int,
) -> str:
    """Two-level post-launch check: foreground package, then session tab."""
    time.sleep(1.5)
    _tap_session_tab(adb, session_tab_x, session_tab_y)
    if time.monotonic() >= deadline or not _check_foreground(adb, package):
        logger.warning("cold_start: %s path: %s not in foreground", path_name, package)
        return _PATH_NOT_FOREGROUND
    if not _verify_session_tab(
        adb, path_name, session_tab_x, session_tab_y,
        session_tab_verify, session_tab_retries,
    ):
        return _PATH_TAB_FAILED
    return _PATH_OK


def _verify_session_tab(
    adb: AdbProtocol,
    path_name: str,
    session_tab_x: int | None,
    session_tab_y: int | None,
    session_tab_verify: Callable[[AdbProtocol], bool] | None,
    session_tab_retries: int,
) -> bool:
    """Confirm the session tab actually took effect.

    Returns ``True`` immediately when no tab check was requested (no
    coordinates or no verify callback) — the legacy foreground-only
    behaviour.  Otherwise verifies, re-tapping the tab up to
    ``session_tab_retries`` times on failure.
    """
    if not _tab_check_requested(session_tab_x, session_tab_y, session_tab_verify):
        return True
    assert session_tab_x is not None and session_tab_y is not None
    assert session_tab_verify is not None
    for attempt in range(1, session_tab_retries + 2):
        try:
            ok = bool(session_tab_verify(adb))
        except Exception as exc:
            logger.warning(
                "cold_start: %s: session tab verify raised (%s)", path_name, exc,
            )
            ok = False
        if ok:
            if attempt > 1:
                logger.info(
                    "cold_start: %s: session tab verified on attempt %d",
                    path_name, attempt,
                )
            return True
        if attempt <= session_tab_retries:
            logger.warning(
                "cold_start: %s: session tab verify failed, re-tapping (%d/%d)",
                path_name, attempt, session_tab_retries + 1,
            )
            _tap_session_tab(adb, session_tab_x, session_tab_y)
    logger.error(
        "cold_start: %s: session tab verify failed after %d attempts",
        path_name, session_tab_retries + 1,
    )
    return False


def _try_dock_tap_with_retry(
    adb: AdbProtocol,
    package: str,
    dx: int,
    dy: int,
    session_tab_x: int | None,
    session_tab_y: int | None,
    *,
    session_tab_verify: Callable[[AdbProtocol], bool] | None = None,
    session_tab_retries: int = 2,
) -> str:
    logger.info("cold_start: Dock tap at (%d, %d) for %s (pre-wait %.1fs, max %d retries)",
             dx, dy, package, _DOCK_PRE_WAIT_S, _DOCK_RETRIES)
    for attempt in range(1 + _DOCK_RETRIES):
        if attempt > 0:
            time.sleep(_DOCK_PRE_WAIT_S)
        adb.tap(dx, dy)
        time.sleep(1.2)
        _tap_session_tab(adb, session_tab_x, session_tab_y)
        if not _check_foreground(adb, package):
            logger.warning("cold_start: Dock tap attempt %d failed (not foreground)",
                           attempt + 1)
            continue
        logger.info("cold_start: Dock tap foreground OK on attempt %d", attempt + 1)
        if _verify_session_tab(
            adb, "dock", session_tab_x, session_tab_y,
            session_tab_verify, session_tab_retries,
        ):
            return _PATH_OK
        return _PATH_TAB_FAILED
    return _PATH_NOT_FOREGROUND


def _check_foreground(adb: AdbProtocol, package: str) -> bool:
    try:
        return adb.foreground_package() == package
    except Exception:
        return False


def _wait_for_device(adb: AdbProtocol, timeout_s: float = 90) -> None:
    """Poll ``adb shell echo ok`` until the device is back online."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            out = adb._run(["shell", "echo", "ok"])
            if "ok" in out:
                logger.info("cold_start: device back online after reboot")
                return
        except Exception:
            pass
        time.sleep(3)
    logger.warning("cold_start: device did not come back within %.0fs", timeout_s)


def _wait_for_boot_completed(adb: AdbProtocol, timeout_s: float = 60) -> None:
    """Poll ``getprop sys.boot_completed`` until ``1``."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            out = adb._run(["shell", "getprop", "sys.boot_completed"])
            if out.strip() == "1":
                logger.info("cold_start: boot completed")
                return
        except Exception:
            pass
        time.sleep(3)
    logger.warning("cold_start: boot did not complete within %.0fs", timeout_s)
