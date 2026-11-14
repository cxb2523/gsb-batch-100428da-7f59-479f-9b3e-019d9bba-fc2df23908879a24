#!/usr/bin/env python3
"""
mouse_position.py — Multi-backend cursor position tracker for Wayland.

Automatically selects the best available backend for your compositor and
falls back, in order, when a backend is unavailable or fails:

  1. Hyprland   — via ``hyprctl cursorpos``
  2. wlroots    — via ``wl-find-cursor`` (Sway, River, etc.)
  3. KDE Plasma — via KWin scripting and D-Bus (net.mousePosition)
  4. XWayland   — via ``xdotool`` (if DISPLAY is set)
  5. evdev      — relative-motion integration fallback (needs /dev/input access)

Both the one-shot snapshot (:func:`get_position`) and the continuous stream
(:func:`mouse_position_generator`) run on the same :class:`CursorPositionTracker`,
so they share one fallback chain, one failure taxonomy and one set of
diagnostics.

Failure reasons are never swallowed. Every skipped or failed backend produces a
:class:`BackendFailure` with one of the categorized reasons from
:class:`FailureReason`:

  * ``BINARY_MISSING``     — required executable not on ``$PATH``
  * ``UNAVAILABLE``        — binary present, but prerequisites missing
                             (e.g. no ``DISPLAY``, D-Bus/KWin not reachable,
                             no ``/dev/input`` device, import error)
  * ``STARTUP_FAILED``     — persistent backend process/thread could not start
  * ``READ_TIMEOUT``       — a read command exceeded its timeout
  * ``EMPTY_RESULT``       — the backend answered, but returned no coordinates
  * ``READ_ERROR``         — any other read failure

Callers that only want coordinates keep using the legacy API unchanged::

    from wayland_automation import get_position, mouse_position_generator

    x, y = get_position()
    for x, y in mouse_position_generator(interval=0.1):
        ...

Callers that need explainability opt in via :class:`PositionReport` or
:func:`get_position_with_report` — no legacy call site changes are required::

    from wayland_automation.mouse_position import (
        PositionReport, mouse_position_generator as stream,
    )

    report = PositionReport()
    for x, y in stream(report=report):
        print(report.active_backend, report.skipped)

CLI::

    python -m wayland_automation.mouse_position          # default 0.2s interval
    python -m wayland_automation.mouse_position 0.5      # custom interval

Dependencies:
  - Python: ``evdev`` (for evdev fallback), ``dbus-next`` (for KDE Plasma)
  - System: ``hyprctl`` | ``wl-find-cursor`` | ``qdbus`` | ``xdotool`` (auto-detected)
"""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple

INTERVAL = 0.2
#: Consecutive failed reads from the active backend before the stream
#: switches to the next candidate backend.
DEFAULT_READ_FAIL_THRESHOLD = 3
#: How long a freshly started persistent backend is waited on for its first
#: coordinate before it is treated as an empty-result failure.
DEFAULT_INITIAL_READ_TIMEOUT = 1.0
NUM_RE = re.compile(r"-?\d+")


# ---------------------------------------------------------------------------
# Failure taxonomy
# ---------------------------------------------------------------------------
class FailureReason(Enum):
    """Why a backend was skipped or stopped."""

    BINARY_MISSING = "binary_missing"
    UNAVAILABLE = "unavailable"
    STARTUP_FAILED = "startup_failed"
    READ_TIMEOUT = "read_timeout"
    EMPTY_RESULT = "empty_result"
    READ_ERROR = "read_error"


@dataclass
class BackendFailure:
    """A single recorded failure of one backend.

    Attributes:
        backend: Name of the backend (e.g. ``"hyprctl"``).
        reason: Categorized failure reason.
        detail: Human-readable explanation (exception text, stderr, ...).
        phase: Where in the lifecycle it happened — ``"probe"`` (the
            ``available()`` check), ``"startup"`` or ``"read"``.
        timestamp: ``time.time()`` of the failure.
    """

    backend: str
    reason: FailureReason
    detail: str = ""
    phase: str = "read"
    timestamp: float = field(default_factory=time.time)

    def render(self) -> str:
        """One-line summary suitable for exception messages and logs."""
        text = f"{self.backend} [{self.phase}/{self.reason.value}]"
        if self.detail:
            text += f": {self.detail}"
        return text


@dataclass
class BackendSwitch:
    """A record of one backend hand-over inside a running stream."""

    from_backend: Optional[str]
    to_backend: str
    at_position: Optional[Tuple[int, int]]
    reason: FailureReason
    failures: List[BackendFailure] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


@dataclass
class PositionReport:
    """Live, non-breaking diagnostics handle for a tracker run.

    The same object is updated in place, so callers can inspect it at any
    point during a snapshot or stream without changing the
    ``(x, y)``-returning API::

        report = PositionReport()
        pos = get_position_with_report(report=report)
        print(report.active_backend)      # e.g. "wl-find-cursor"
        print(report.skipped)             # [BackendFailure(...), ...]
    """

    #: Backend currently serving coordinates (``None`` until one is selected).
    active_backend: Optional[str] = None
    #: Every failure recorded so far, in fallback order.
    failures: List[BackendFailure] = field(default_factory=list)
    #: Backend hand-overs that happened while streaming.
    switches: List[BackendSwitch] = field(default_factory=list)
    #: Last coordinate known to be valid, kept across switches.
    last_position: Optional[Tuple[int, int]] = None

    @property
    def skipped(self) -> List[BackendFailure]:
        """Failures of backends that were never activated."""
        return [f for f in self.failures if f.backend != self.active_backend]

    def failure_summary(self) -> str:
        """Render all recorded failures as a multi-line string."""
        if not self.failures:
            return "(no failures recorded)"
        return "\n".join(f"  - {f.render()}" for f in self.failures)


class NoBackendAvailableError(RuntimeError):
    """Raised when every backend in the fallback chain has failed.

    The message always contains a per-backend failure summary instead of a
    generic "no backend available" string. Subclasses :class:`RuntimeError`
    so existing ``except RuntimeError`` call sites keep working.
    """

    def __init__(self, failures: List[BackendFailure]):
        self.failures = list(failures)
        if self.failures:
            detail = "\n" + "\n".join(
                f"  - {f.render()}" for f in self.failures
            )
        else:
            detail = " (no backends were configured)"
        super().__init__(
            "No mouse tracking backend could provide a cursor "
            f"position; per-backend results:{detail}"
        )


# ---------------------------------------------------------------------------
# Shared low-level helpers
# ---------------------------------------------------------------------------
def _run_cursor_command(
    argv: List[str], timeout: float
) -> Tuple[Optional[str], Optional[FailureReason], str]:
    """Run a one-shot cursor command, never raising.

    Returns:
        ``(stdout, reason, detail)``. On success ``reason`` is ``None``;
        timeouts and other errors are categorized so callers can record them
        instead of swallowing them.
    """
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, FailureReason.READ_TIMEOUT, (
            f"command timed out after {timeout}s: {' '.join(argv)}"
        )
    except FileNotFoundError as exc:
        return None, FailureReason.BINARY_MISSING, str(exc)
    except OSError as exc:
        return None, FailureReason.READ_ERROR, str(exc)

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or (
            f"exit code {proc.returncode}"
        )
        return proc.stdout, FailureReason.READ_ERROR, detail
    return proc.stdout, None, ""


# -------------------- Hyprland backend --------------------
class HyprlandBackend:
    name = "hyprctl"

    @staticmethod
    def available() -> bool:
        return shutil.which("hyprctl") is not None

    @staticmethod
    def _read(timeout: float = 0.5) -> Tuple[Optional[Tuple[int, int]], Optional[FailureReason], str]:
        if shutil.which("hyprctl") is None:
            return None, FailureReason.BINARY_MISSING, "hyprctl not found on PATH"
        stdout, reason, detail = _run_cursor_command(
            ["hyprctl", "cursorpos"], timeout=timeout
        )
        if reason is not None:
            return None, reason, detail
        nums = NUM_RE.findall(stdout or "")
        if len(nums) >= 2:
            return (int(nums[0]), int(nums[1])), None, ""
        return None, FailureReason.EMPTY_RESULT, (
            f"no coordinates in hyprctl output: {stdout!r}"
        )

    @staticmethod
    def read_once() -> Optional[Tuple[int, int]]:
        """Legacy convenience wrapper; returns ``None`` on any failure."""
        pos, _reason, _detail = HyprlandBackend._read()
        return pos


# -------------------- wl-find-cursor backend (Sway / wlroots) --------------------
class WlFindCursorBackend:
    name = "wl-find-cursor"

    @staticmethod
    def available() -> bool:
        return shutil.which("wl-find-cursor") is not None

    def __init__(self):
        self.proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._last: Optional[Tuple[int, int]] = None
        self._running = False
        self._thr: Optional[threading.Thread] = None
        #: Populated with a categorized reason/detail when start() fails.
        self.last_error: Optional[Tuple[FailureReason, str]] = None

    def start(self) -> bool:
        cmd = shutil.which("wl-find-cursor")
        if not cmd:
            self.last_error = (
                FailureReason.BINARY_MISSING,
                "wl-find-cursor not found on PATH",
            )
            return False

        try:
            self.proc = subprocess.Popen(
                [cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._running = True
            self._thr = threading.Thread(target=self._reader, daemon=True)
            self._thr.start()
            return True
        except FileNotFoundError as exc:
            self.last_error = FailureReason.BINARY_MISSING, str(exc)
            return False
        except OSError as exc:
            self.last_error = FailureReason.STARTUP_FAILED, str(exc)
            return False

    def _reader(self) -> None:
        if not self.proc or not self.proc.stdout:
            return
        try:
            for line in self.proc.stdout:
                if not self._running:
                    break
                nums = NUM_RE.findall(line)
                if len(nums) >= 2:
                    with self._lock:
                        self._last = (int(nums[0]), int(nums[1]))
        except Exception:
            pass

    def get_position(self) -> Optional[Tuple[int, int]]:
        with self._lock:
            return self._last

    def stop(self) -> None:
        self._running = False
        try:
            if self.proc:
                self.proc.terminate()
                self.proc.wait(timeout=0.5)
        except Exception:
            pass


# -------------------- KDE Plasma / KWin backend --------------------
def _find_qdbus() -> Optional[str]:
    """Find the qdbus binary (handles Qt5 and Qt6 naming across distributions)."""
    for cmd in ("qdbus", "qdbus6", "qdbus-qt6", "qdbus-qt5"):
        path = shutil.which(cmd)
        if path:
            return path
    return None


class KWinBackend:
    name = "kwin"

    @staticmethod
    def available() -> bool:
        """Check if qdbus, dbus-next, and KWin's scripting interface are available."""
        qdbus_cmd = _find_qdbus()
        if not qdbus_cmd:
            return False
        try:
            import dbus_next  # noqa: F401
        except ImportError:
            return False

        try:
            res = subprocess.run(
                [qdbus_cmd, "org.kde.KWin", "/Scripting"],
                capture_output=True,
                text=True,
                timeout=1.0,
            )
            return res.returncode == 0
        except Exception:
            return False

    def __init__(self):
        self.mouse_position: Optional[Tuple[int, int]] = None
        self._lock = threading.Lock()
        self._running = False
        self._bus = None
        self._loop = None
        self._thread: Optional[threading.Thread] = None
        self._ready_event = threading.Event()
        self._temp_js_path: Optional[str] = None
        self._script_id: Optional[str] = None
        #: Populated with a categorized reason/detail when start() fails.
        self.last_error: Optional[Tuple[FailureReason, str]] = None

    def _set_mouse_position(self, pos) -> None:
        if pos and len(pos) >= 2:
            with self._lock:
                self.mouse_position = (int(pos[0]), int(pos[1]))

    def _get_script_path(self) -> Optional[str]:
        pkg_script = Path(__file__).resolve().parent / "kwin_cursor_pos.js"
        if pkg_script.exists():
            return str(pkg_script)

        import tempfile

        js_code = (
            "var lastCallTime = 0;\n"
            "function sendPos() {\n"
            "  callDBus(\n"
            "    'net.mousePosition',\n"
            "    '/commands',\n"
            "    'net.mousePosition',\n"
            "    'ReceiveMousePosition',\n"
            "    '[' + workspace.cursorPos.x + ', ' + workspace.cursorPos.y + ']'\n"
            "  );\n"
            "}\n"
            "sendPos();\n"
            "workspace.cursorPosChanged.connect(function() {\n"
            "  var currentTime = Date.now();\n"
            "  if (currentTime - lastCallTime > 16) {\n"
            "    sendPos();\n"
            "    lastCallTime = currentTime;\n"
            "  }\n"
            "});\n"
        )
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as f:
                f.write(js_code)
                self._temp_js_path = f.name
                return self._temp_js_path
        except Exception:
            return None

    def _run_event_loop(self) -> None:
        import asyncio
        from dbus_next.aio import MessageBus
        from dbus_next.service import ServiceInterface, method

        class DbusInterface(ServiceInterface):
            def __init__(self, callback):
                super().__init__("net.mousePosition")
                self.callback = callback

            @method()
            def ReceiveMousePosition(self, val: "s"):  # type: ignore
                try:
                    pos = json.loads(val)
                    self.callback(pos)
                except Exception:
                    pass

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        async def _serve():
            try:
                self._bus = await MessageBus().connect()
                interface = DbusInterface(self._set_mouse_position)
                self._bus.export("/commands", interface)
                await self._bus.request_name("net.mousePosition")
                self._ready_event.set()
                await self._bus.wait_for_disconnect()
            except Exception:
                self._ready_event.set()

        try:
            self._loop.run_until_complete(_serve())
        except Exception:
            pass
        finally:
            self._ready_event.set()

    def start(self) -> bool:
        qdbus_cmd = _find_qdbus()
        if not qdbus_cmd:
            self.last_error = (
                FailureReason.BINARY_MISSING,
                "no qdbus binary found on PATH",
            )
            return False

        self._running = True
        self._thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self._thread.start()

        if not self._ready_event.wait(timeout=3.0) or not self._bus:
            self.stop()
            self.last_error = (
                FailureReason.STARTUP_FAILED,
                "D-Bus message bus did not become ready",
            )
            return False

        # Unload any previously loaded instance of our script
        subprocess.call(
            [qdbus_cmd, "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.unloadScript", "kwin_cursor_pos"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1.0,
        )

        script_path = self._get_script_path()
        if not script_path or not os.path.exists(script_path):
            self.stop()
            self.last_error = (
                FailureReason.STARTUP_FAILED,
                "could not prepare KWin cursor-position script",
            )
            return False

        result = subprocess.run(
            [qdbus_cmd, "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.loadScript", str(script_path), "kwin_cursor_pos"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        script_id = result.stdout.strip()
        if not script_id or script_id == "-1" or "error" in script_id.lower():
            self.stop()
            self.last_error = (
                FailureReason.STARTUP_FAILED,
                f"KWin rejected loadScript: {script_id or result.stderr.strip() or 'empty response'}",
            )
            return False

        self._script_id = script_id

        script_obj = script_id if script_id.startswith("/") else f"/Scripting/Script{script_id}"
        subprocess.call(
            [qdbus_cmd, "org.kde.KWin", script_obj, "org.kde.kwin.Script.run"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1.0,
        )
        return True

    def get_position(self) -> Optional[Tuple[int, int]]:
        with self._lock:
            return self.mouse_position

    def stop(self) -> None:
        self._running = False
        qdbus_cmd = _find_qdbus()
        if qdbus_cmd:
            try:
                subprocess.call(
                    [qdbus_cmd, "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.unloadScript", "kwin_cursor_pos"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=1.0,
                )
            except Exception:
                pass

        if self._bus:
            try:
                self._bus.disconnect()
            except Exception:
                pass

        if self._loop and self._loop.is_running():
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass

        if self._temp_js_path and os.path.exists(self._temp_js_path):
            try:
                os.remove(self._temp_js_path)
            except Exception:
                pass
            self._temp_js_path = None


# -------------------- X11 / XWayland backend (xdotool) --------------------
class XdotoolBackend:
    name = "xdotool"

    @staticmethod
    def available() -> bool:
        return ("DISPLAY" in os.environ) and (shutil.which("xdotool") is not None)

    @staticmethod
    def _read(timeout: float = 0.5) -> Tuple[Optional[Tuple[int, int]], Optional[FailureReason], str]:
        if shutil.which("xdotool") is None:
            return None, FailureReason.BINARY_MISSING, "xdotool not found on PATH"
        if "DISPLAY" not in os.environ:
            return None, FailureReason.UNAVAILABLE, "DISPLAY is not set (no XWayland session)"
        stdout, reason, detail = _run_cursor_command(
            ["xdotool", "getmouselocation", "--shell"], timeout=timeout
        )
        if reason is not None:
            return None, reason, detail
        data: Dict[str, str] = {}
        for line in (stdout or "").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip()
        if "X" in data and "Y" in data:
            try:
                return (int(data["X"]), int(data["Y"])), None, ""
            except ValueError as exc:
                return None, FailureReason.READ_ERROR, f"non-integer coordinates: {exc}"
        return None, FailureReason.EMPTY_RESULT, (
            f"no X/Y coordinates in xdotool output: {stdout!r}"
        )

    @staticmethod
    def read_once() -> Optional[Tuple[int, int]]:
        """Legacy convenience wrapper; returns ``None`` on any failure."""
        pos, _reason, _detail = XdotoolBackend._read()
        return pos


# -------------------- evdev fallback (relative -> absolute integration) --------------------
class EvdevFallback:
    name = "evdev"

    def __init__(self, seed: Tuple[Optional[int], Optional[int]] = (None, None)):
        try:
            from evdev import list_devices, InputDevice, ecodes
            self.evdev = True
            self.list_devices = list_devices
            self.InputDevice = InputDevice
            self.ecodes = ecodes
        except Exception:
            self.evdev = False

        self.device_path: Optional[str] = None
        self.dev = None
        self._running = False
        self._lock = threading.Lock()
        self.width = 1920
        self.height = 1080
        self.x: Optional[float] = seed[0]
        self.y: Optional[float] = seed[1]
        #: Populated with a categorized reason/detail when start() fails.
        self.last_error: Optional[Tuple[FailureReason, str]] = None

    def available(self) -> bool:
        return self.evdev

    def find_device(self) -> Optional[str]:
        if not self.evdev:
            return None
        for path in self.list_devices():
            try:
                dev = self.InputDevice(path)
            except Exception:
                continue
            try:
                try:
                    caps = dev.capabilities(skip_missing=True)
                except TypeError:
                    caps = dev.capabilities()
            except Exception:
                try:
                    dev.close()
                except Exception:
                    pass
                continue

            rels = caps.get(self.ecodes.EV_REL)
            if rels:
                rels_list = list(rels.keys()) if isinstance(rels, dict) else list(rels)
                if self.ecodes.REL_X in rels_list and self.ecodes.REL_Y in rels_list:
                    try:
                        dev.close()
                    except Exception:
                        pass
                    return path
            try:
                dev.close()
            except Exception:
                pass
        return None

    def start(self) -> bool:
        if not self.evdev:
            self.last_error = (
                FailureReason.UNAVAILABLE,
                "python evdev package is not importable",
            )
            return False
        self.device_path = self.find_device()
        if not self.device_path:
            self.last_error = (
                FailureReason.UNAVAILABLE,
                "no /dev/input device with REL_X/REL_Y was found",
            )
            return False

        try:
            self.dev = self.InputDevice(self.device_path)
        except Exception as exc:
            self.last_error = FailureReason.STARTUP_FAILED, str(exc)
            return False

        # Get screen resolution
        try:
            from wayland_automation.utils.screen_resolution import get_resolution
            w, h = get_resolution()
            self.width, self.height = int(w), int(h)
        except Exception:
            self.width, self.height = 1920, 1080

        if self.x is None or self.y is None:
            self.x = self.width // 2
            self.y = self.height // 2

        self._running = True
        self._thr = threading.Thread(target=self._reader, daemon=True)
        self._thr.start()
        return True

    def _reader(self) -> None:
        try:
            for ev in self.dev.read_loop():
                if not self._running:
                    break
                if ev.type == self.ecodes.EV_REL:
                    with self._lock:
                        if ev.code == self.ecodes.REL_X:
                            self.x += ev.value
                        elif ev.code == self.ecodes.REL_Y:
                            self.y += ev.value
                        # Clamp to screen boundary
                        if self.x < 0:
                            self.x = 0
                        if self.y < 0:
                            self.y = 0
                        if self.x >= self.width:
                            self.x = self.width - 1
                        if self.y >= self.height:
                            self.y = self.height - 1
        except Exception:
            pass

    def get_position(self) -> Tuple[int, int]:
        with self._lock:
            return int(self.x), int(self.y)

    def stop(self) -> None:
        self._running = False
        try:
            if self.dev:
                self.dev.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Adapter layer: one uniform interface over snapshot-style and persistent
# backends, so the tracker never has to know which kind it is talking to.
# ---------------------------------------------------------------------------
class CursorBackendAdapter:
    """Uniform interface for a cursor-position backend.

    Adapters never raise for backend problems: they return
    ``(position, reason, detail)`` triples so the tracker can record every
    failure categorically. Adapters may raise only for genuine programming
    errors.
    """

    #: Backend identifier shown in reports and exception messages.
    name: str = "base"
    #: Whether the backend keeps a process/thread alive between reads.
    persistent: bool = False
    #: Whether the backend accepts a coordinate seed (evdev integration).
    supports_seed: bool = False

    def __init__(self, seed: Optional[Tuple[int, int]] = None) -> None:
        self.seed = seed
        self._controller: Any = None

    # -- lifecycle -------------------------------------------------------
    def probe(self) -> Tuple[bool, Optional[FailureReason], str]:
        """Check availability. Returns ``(is_available, reason, detail)``."""
        raise NotImplementedError

    def start(self) -> Tuple[bool, Optional[FailureReason], str]:
        """Start a persistent backend. Snapshot backends succeed trivially."""
        return True, None, ""

    def initial_read(
        self, timeout: float = DEFAULT_INITIAL_READ_TIMEOUT
    ) -> Tuple[Optional[Tuple[int, int]], Optional[FailureReason], str]:
        """First read after activation; defaults to an ordinary read."""
        return self.read(timeout=timeout)

    def read(
        self, timeout: float = 0.5
    ) -> Tuple[Optional[Tuple[int, int]], Optional[FailureReason], str]:
        """Return ``(position, reason, detail)``; reason is None on success."""
        raise NotImplementedError

    def stop(self) -> None:
        """Release any resources held by the backend."""
        if self._controller is not None:
            try:
                self._controller.stop()
            except Exception:
                pass
            self._controller = None


class _PersistentAdapter(CursorBackendAdapter):
    """Common startup/warm-up logic for process/thread-backed backends."""

    persistent = True
    #: Subclasses set this to the concrete backend class.
    backend_cls: Any = None

    def _make_controller(self) -> Any:
        return self.backend_cls()

    def start(self) -> Tuple[bool, Optional[FailureReason], str]:
        try:
            controller = self._make_controller()
        except Exception as exc:  # defensive: construction itself failed
            return False, FailureReason.STARTUP_FAILED, str(exc)
        try:
            ok = bool(controller.start())
        except Exception as exc:
            try:
                controller.stop()
            except Exception:
                pass
            return False, FailureReason.STARTUP_FAILED, str(exc)
        if not ok:
            reason, detail = getattr(
                controller, "last_error",
                (FailureReason.STARTUP_FAILED, "start() returned False"),
            )
            try:
                controller.stop()
            except Exception:
                pass
            return False, reason, detail
        self._controller = controller
        return True, None, ""

    def initial_read(
        self, timeout: float = DEFAULT_INITIAL_READ_TIMEOUT
    ) -> Tuple[Optional[Tuple[int, int]], Optional[FailureReason], str]:
        """Warm-up poll: give the freshly spawned backend time to emit once."""
        deadline = time.monotonic() + max(timeout, 0.0)
        detail = "no coordinates received before warm-up timeout"
        while time.monotonic() <= deadline:
            pos, reason, detail = self.read()
            if pos is not None:
                return pos, None, ""
            if reason is not None and reason != FailureReason.EMPTY_RESULT:
                return None, reason, detail
            time.sleep(min(0.05, max(timeout, 0.05) / 5))
        return None, FailureReason.EMPTY_RESULT, detail

    def read(
        self, timeout: float = 0.5
    ) -> Tuple[Optional[Tuple[int, int]], Optional[FailureReason], str]:
        if self._controller is None:
            return None, FailureReason.STARTUP_FAILED, "backend has not been started"
        try:
            pos = self._controller.get_position()
        except Exception as exc:
            return None, FailureReason.READ_ERROR, str(exc)
        if pos is None:
            return None, FailureReason.EMPTY_RESULT, (
                f"{self.name} has not produced coordinates yet"
            )
        return pos, None, ""


class HyprctlAdapter(CursorBackendAdapter):
    name = "hyprctl"
    persistent = False

    def probe(self) -> Tuple[bool, Optional[FailureReason], str]:
        if shutil.which("hyprctl") is None:
            return False, FailureReason.BINARY_MISSING, "hyprctl not found on PATH"
        return True, None, ""

    def read(self, timeout: float = 0.5):
        return HyprlandBackend._read(timeout=timeout)


class WlFindCursorAdapter(_PersistentAdapter):
    name = "wl-find-cursor"
    backend_cls = WlFindCursorBackend

    def probe(self) -> Tuple[bool, Optional[FailureReason], str]:
        if shutil.which("wl-find-cursor") is None:
            return False, FailureReason.BINARY_MISSING, (
                "wl-find-cursor not found on PATH"
            )
        return True, None, ""


class KWinAdapter(_PersistentAdapter):
    name = "kwin"
    backend_cls = KWinBackend

    def probe(self) -> Tuple[bool, Optional[FailureReason], str]:
        qdbus_cmd = _find_qdbus()
        if qdbus_cmd is None:
            return False, FailureReason.BINARY_MISSING, (
                "no qdbus binary (qdbus/qdbus6/qdbus-qt6/qdbus-qt5) found on PATH"
            )
        try:
            import dbus_next  # noqa: F401
        except ImportError as exc:
            return False, FailureReason.UNAVAILABLE, (
                f"dbus-next is not installed: {exc}"
            )
        try:
            res = subprocess.run(
                [qdbus_cmd, "org.kde.KWin", "/Scripting"],
                capture_output=True,
                text=True,
                timeout=1.0,
            )
        except subprocess.TimeoutExpired:
            return False, FailureReason.READ_TIMEOUT, (
                "qdbus org.kde.KWin /Scripting timed out"
            )
        except OSError as exc:
            return False, FailureReason.READ_ERROR, str(exc)
        if res.returncode != 0:
            detail = (res.stderr or res.stdout or "").strip() or (
                f"qdbus exited with code {res.returncode}"
            )
            return False, FailureReason.UNAVAILABLE, detail
        return True, None, ""


class XdotoolAdapter(CursorBackendAdapter):
    name = "xdotool"
    persistent = False

    def probe(self) -> Tuple[bool, Optional[FailureReason], str]:
        if shutil.which("xdotool") is None:
            return False, FailureReason.BINARY_MISSING, "xdotool not found on PATH"
        if "DISPLAY" not in os.environ:
            return False, FailureReason.UNAVAILABLE, (
                "DISPLAY is not set (no XWayland session)"
            )
        return True, None, ""

    def read(self, timeout: float = 0.5):
        return XdotoolBackend._read(timeout=timeout)


class EvdevAdapter(_PersistentAdapter):
    name = "evdev"
    supports_seed = True

    def __init__(self, seed: Optional[Tuple[int, int]] = None) -> None:
        super().__init__(seed=seed)

    def _make_controller(self) -> Any:
        return EvdevFallback(seed=self.seed or (None, None))

    def probe(self) -> Tuple[bool, Optional[FailureReason], str]:
        try:
            import evdev  # noqa: F401
        except ImportError as exc:
            return False, FailureReason.UNAVAILABLE, (
                f"python evdev package is not importable: {exc}"
            )
        return True, None, ""

    def read(self, timeout: float = 0.5):
        # evdev integrates relative motion from a seed, so once started it
        # always has a coordinate — there is no empty-result state.
        if self._controller is None:
            return None, FailureReason.STARTUP_FAILED, "backend has not been started"
        try:
            return self._controller.get_position(), None, ""
        except Exception as exc:
            return None, FailureReason.READ_ERROR, str(exc)


#: Fallback order, matching the historical priority.
DEFAULT_BACKEND_ORDER: Tuple[type, ...] = (
    HyprctlAdapter,
    WlFindCursorAdapter,
    KWinAdapter,
    XdotoolAdapter,
    EvdevAdapter,
)


# ---------------------------------------------------------------------------
# Tracker: the single fallback engine shared by snapshot and stream APIs
# ---------------------------------------------------------------------------
class CursorPositionTracker:
    """Ordered, explainable fallback over cursor-position backends.

    Args:
        backends: Adapter classes or instances to try, in priority order.
            Defaults to :data:`DEFAULT_BACKEND_ORDER`. Instances allow tests
            to inject fakes; classes are constructed lazily as needed.
        read_fail_threshold: Consecutive failed reads from the active
            backend during :meth:`stream` before switching to the next
            candidate.
        initial_read_timeout: Warm-up time granted to a freshly started
            persistent backend to deliver its first coordinate.
        report: Optional :class:`PositionReport` updated in place with the
            active backend, every failure and every switch.
        on_switch: Optional callback invoked with a :class:`BackendSwitch`
            whenever the active backend changes.
    """

    def __init__(
        self,
        backends: Optional[Tuple[Any, ...]] = None,
        read_fail_threshold: int = DEFAULT_READ_FAIL_THRESHOLD,
        initial_read_timeout: float = DEFAULT_INITIAL_READ_TIMEOUT,
        report: Optional[PositionReport] = None,
        on_switch: Optional[Callable[[BackendSwitch], None]] = None,
    ) -> None:
        if read_fail_threshold < 1:
            raise ValueError("read_fail_threshold must be >= 1")
        self._backend_specs = tuple(
            DEFAULT_BACKEND_ORDER if backends is None else backends
        )
        self.read_fail_threshold = read_fail_threshold
        self.initial_read_timeout = initial_read_timeout
        self.report = report or PositionReport()
        self.on_switch = on_switch
        self._cursor = 0
        self._active: Optional[CursorBackendAdapter] = None

    # -- properties ------------------------------------------------------
    @property
    def active_backend(self) -> Optional[str]:
        return self._active.name if self._active is not None else None

    @property
    def failures(self) -> List[BackendFailure]:
        return self.report.failures

    # -- internal helpers ------------------------------------------------
    def _instantiate(self, spec: Any) -> CursorBackendAdapter:
        if isinstance(spec, CursorBackendAdapter):
            return spec
        # Seed only flows to the evdev integration backend, which uses it to
        # anchor relative-motion integration at the last known coordinate.
        if getattr(spec, "supports_seed", False):
            return spec(seed=self.report.last_position)
        return spec()

    def _record(self, failure: BackendFailure) -> None:
        self.report.failures.append(failure)

    def _activate_next(
        self,
        trigger_reason: Optional[FailureReason] = None,
    ) -> Tuple[Optional[CursorBackendAdapter], Optional[Tuple[int, int]]]:
        """Probe/start/initial-read backends in order until one yields.

        Args:
            trigger_reason: When this activation is a runtime hand-over
                triggered by repeated read failures, the reason that retired
                the previous backend. Recorded on the :class:`BackendSwitch`.

        Returns ``(adapter, initial_position)`` or ``(None, None)`` when the
        chain is exhausted. Every failure along the way is recorded.
        """
        previous = self.active_backend
        switch_failures: List[BackendFailure] = []

        while self._cursor < len(self._backend_specs):
            adapter = self._instantiate(self._backend_specs[self._cursor])
            name = adapter.name

            available, reason, detail = adapter.probe()
            if not available:
                failure = BackendFailure(
                    name, reason or FailureReason.UNAVAILABLE, detail, phase="probe"
                )
                self._record(failure)
                switch_failures.append(failure)
                self._cursor += 1
                continue

            if adapter.persistent:
                started, reason, detail = adapter.start()
                if not started:
                    failure = BackendFailure(
                        name,
                        reason or FailureReason.STARTUP_FAILED,
                        detail,
                        phase="startup",
                    )
                    self._record(failure)
                    switch_failures.append(failure)
                    adapter.stop()
                    self._cursor += 1
                    continue

            pos, reason, detail = adapter.initial_read(timeout=self.initial_read_timeout)
            if pos is None:
                failure = BackendFailure(
                    name,
                    reason or FailureReason.EMPTY_RESULT,
                    detail,
                    phase="read",
                )
                self._record(failure)
                switch_failures.append(failure)
                adapter.stop()
                self._cursor += 1
                continue

            # Success: this backend becomes the active one.
            self._active = adapter
            self.report.active_backend = name
            self.report.last_position = pos
            if previous is not None and previous != name:
                switch = BackendSwitch(
                    from_backend=previous,
                    to_backend=name,
                    at_position=self.report.last_position,
                    reason=trigger_reason or FailureReason.EMPTY_RESULT,
                    failures=switch_failures,
                )
                self.report.switches.append(switch)
                if self.on_switch is not None:
                    try:
                        self.on_switch(switch)
                    except Exception:
                        pass
            return adapter, pos

        return None, None

    # -- public API ------------------------------------------------------
    def snapshot(self) -> Tuple[int, int]:
        """Take one position through the full ordered fallback chain.

        Raises:
            NoBackendAvailableError: every backend failed; the exception
                message summarizes each backend's categorized failure.
        """
        adapter, pos = self._activate_next()
        if adapter is None:
            raise NoBackendAvailableError(self.report.failures)
        assert pos is not None
        self.stop()
        return pos

    def stream(
        self, interval: float = INTERVAL
    ) -> Generator[Tuple[int, int], None, None]:
        """Yield coordinates forever, switching backends on repeated failure.

        Switching rules:

        * A backend that fails ``read_fail_threshold`` consecutive reads is
          retired and the next candidate is activated.
        * Transient failures below the threshold keep the stream cadence by
          re-serving the last known good coordinate; nothing is swallowed —
          every failed read is recorded on ``report``.
        * The replacement backend is seeded with the last valid coordinate
          (evdev) and must produce a fresh coordinate before its first yield,
          so a hand-over never replays cached values or moves the reported
          position backwards.
        * If every remaining candidate fails, :class:`NoBackendAvailableError`
          is raised with the full per-backend summary.
        """
        adapter, pos = self._activate_next()
        if adapter is None:
            raise NoBackendAvailableError(self.report.failures)
        assert pos is not None
        consecutive_failures = 0
        try:
            while True:
                yield pos

                # Sleep after yielding so the first coordinate is delivered
                # immediately and callers closing the generator aren't delayed.
                time.sleep(interval)

                next_pos, reason, detail = adapter.read()
                if next_pos is not None:
                    pos = next_pos
                    self.report.last_position = pos
                    consecutive_failures = 0
                    continue

                consecutive_failures += 1
                self._record(
                    BackendFailure(
                        adapter.name,
                        reason or FailureReason.READ_ERROR,
                        detail,
                        phase="read",
                    )
                )
                if consecutive_failures < self.read_fail_threshold:
                    # Transient hiccup: the next loop iteration re-serves the
                    # last good coordinate to preserve stream cadence.
                    continue

                # Threshold reached — retire this backend and fall back.
                self._cursor += 1
                switch_reason = reason or FailureReason.READ_ERROR
                adapter.stop()
                adapter, next_pos = self._activate_next(
                    trigger_reason=switch_reason
                )
                if adapter is None:
                    raise NoBackendAvailableError(self.report.failures)
                # _activate_next seeds the replacement with last_position and
                # only returns once it produced a fresh coordinate: no
                # duplicated or replayed values cross the hand-over.
                pos = next_pos
                consecutive_failures = 0
                if self.report.switches:
                    self.report.switches[-1].at_position = (
                        self.report.last_position
                    )
        except GeneratorExit:
            return
        except KeyboardInterrupt:
            return
        finally:
            self.stop()

    def stop(self) -> None:
        """Stop the active backend (idempotent)."""
        if self._active is not None:
            self._active.stop()
            self._active = None


# ---------------------------------------------------------------------------
# Backwards-compatible functional API
# ---------------------------------------------------------------------------
def mouse_position_generator(
    interval: Optional[float] = None,
    print_output: bool = False,
    report: Optional[PositionReport] = None,
    backends: Optional[Tuple[Any, ...]] = None,
    read_fail_threshold: int = DEFAULT_READ_FAIL_THRESHOLD,
    on_switch: Optional[Callable[[BackendSwitch], None]] = None,
) -> Generator[Tuple[int, int], None, None]:
    """
    Generator that yields (x, y) mouse positions continuously.

    The return type and positional calling convention are unchanged. New
    observability is opt-in via keyword arguments, so legacy callers are
    unaffected:

    Args:
        interval: Polling interval in seconds (default: 0.2).
        print_output: If True, prints current position and backend switches
            to stdout.
        report: Optional :class:`PositionReport` updated with the active
            backend, recorded failures and switch history.
        backends: Optional custom adapter chain (for injected fakes/tests).
        read_fail_threshold: Consecutive failed reads before switching to
            the next candidate backend.
        on_switch: Optional callback receiving a :class:`BackendSwitch` on
            every backend hand-over.

    Yields:
        tuple[int, int]: (x, y) coordinates.

    Raises:
        NoBackendAvailableError: no backend could provide coordinates; the
            message summarizes each backend's failure reason.
    """
    if interval is None:
        interval = INTERVAL

    live_report = report or PositionReport()

    def _print_switch(switch: BackendSwitch) -> None:
        print(
            f"\nBackend switch: {switch.from_backend} -> {switch.to_backend} "
            f"({switch.reason.value}) at {switch.at_position}",
            flush=True,
        )

    callbacks: List[Callable[[BackendSwitch], None]] = []
    if print_output:
        callbacks.append(_print_switch)
    if on_switch is not None:
        callbacks.append(on_switch)

    def _dispatch(switch: BackendSwitch) -> None:
        for cb in callbacks:
            try:
                cb(switch)
            except Exception:
                pass

    tracker = CursorPositionTracker(
        backends=backends,
        read_fail_threshold=read_fail_threshold,
        report=live_report,
        on_switch=_dispatch if callbacks else None,
    )

    announced = False
    last = None
    for pos in tracker.stream(interval=interval):
        if print_output and not announced:
            print(f"Using backend: {live_report.active_backend}")
            announced = True
        if print_output and pos != last:
            print(f"Cursor: x={pos[0]}  y={pos[1]}    ", end="\r", flush=True)
            last = pos
        yield pos


def get_position_with_report(
    report: Optional[PositionReport] = None,
    backends: Optional[Tuple[Any, ...]] = None,
) -> Tuple[Tuple[int, int], PositionReport]:
    """Like :func:`get_position`, but also returns the diagnostics report.

    Returns:
        ``((x, y), report)`` — the same fallback engine as
        :func:`mouse_position_generator`, used for a single snapshot.
    """
    live_report = report or PositionReport()
    tracker = CursorPositionTracker(backends=backends, report=live_report)
    return tracker.snapshot(), live_report


def get_position() -> Optional[Tuple[int, int]]:
    """
    Get a single snapshot of the current cursor position (x, y).

    Returns:
        tuple[int, int]: Current (x, y) coordinates.

    Raises:
        NoBackendAvailableError (a RuntimeError subclass) when every backend
            fails; the message contains a per-backend failure summary.
    """
    position, _report = get_position_with_report()
    return position


def pick_backend_and_start():
    """
    Legacy compatibility shim for the pre-fallback API.

    Returns the historical ``(backend_name, getter_fn, controller_instance)``
    triple, or ``(None, None, None)`` when no backend works. New code should
    use :class:`CursorPositionTracker` / :class:`PositionReport` instead,
    which preserve per-backend failure reasons instead of flattening them.
    """
    tracker = CursorPositionTracker()
    adapter, pos = tracker._activate_next()
    if adapter is None:
        return (None, None, None)

    def _getter():
        position, _reason, _detail = adapter.read()
        return position

    if adapter.persistent:
        return (adapter.name, _getter, adapter._controller)
    return (adapter.name, _getter, None)


def main() -> None:
    """CLI entry point for mouse position monitoring."""
    interval = float(sys.argv[1]) if len(sys.argv) > 1 else INTERVAL
    print("Starting compositor-aware cursor watcher. (Ctrl+C to stop)")

    try:
        for _ in mouse_position_generator(interval=interval, print_output=True):
            pass
    except NoBackendAvailableError as exc:
        print(f"\n{exc}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
