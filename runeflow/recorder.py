import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyautogui
from PIL import ImageGrab, Image
from pynput import keyboard, mouse

from .config import RecorderConfig


class AnchorCaptureError(RuntimeError):
    """Raised when an anchor image cannot be produced.

    ``code`` is a small, machine-readable reason (for example ``grab_failed``
    or ``save_failed``) and ``detail`` carries the original error text.
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def ensure_dir(path: str | os.PathLike[str]) -> None:
    os.makedirs(path, exist_ok=True)


def capture_anchor(x: int, y: int, assets_dir: str, size: int) -> tuple[str, int, int, int]:
    try:
        ensure_dir(assets_dir)
    except Exception as exc:
        raise AnchorCaptureError("assets_dir_failed", f"{type(exc).__name__}: {exc}") from exc

    half = size // 2
    left, top = max(int(x) - half, 0), max(int(y) - half, 0)
    try:
        img = ImageGrab.grab(bbox=(left, top, left + size, top + size))
    except Exception as exc:
        raise AnchorCaptureError("grab_failed", f"{type(exc).__name__}: {exc}") from exc

    out = os.path.join(assets_dir, f"{uuid.uuid4().hex[:12]}.png")
    try:
        img.save(out)
    except Exception as exc:
        raise AnchorCaptureError("save_failed", f"{type(exc).__name__}: {exc}") from exc
    return os.path.abspath(out), left, top, size


def _copy_error(exc: Exception) -> dict[str, str]:
    return {"code": "copy_failed", "detail": f"{type(exc).__name__}: {exc}"}


def normalize_recording(events_abs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    prev = 0.0
    for ev in events_abs:
        delay = max(0.0, ev["t"] - prev)
        prev = ev["t"]
        row: dict[str, Any] = {"delay": delay, "type": ev["type"]}
        if ev["type"] == "click":
            row.update({"x": ev["x"], "y": ev["y"], "button": ev["button"]})
            if ev.get("anchor"):
                row["anchor"] = dict(ev["anchor"])
            if ev.get("anchor_error"):
                err = ev["anchor_error"]
                row["anchor_error"] = dict(err) if isinstance(err, dict) else str(err)
        else:
            row.update({"key": ev["key"]})
        out.append(row)
    return out


def save_recording(events: list[dict[str, Any]], json_path: str) -> str:
    assets_dir = os.path.join(os.path.dirname(json_path), "assets")
    try:
        ensure_dir(assets_dir)
    except Exception:
        # The per-step copy below will fail and record a reason for each event.
        pass

    normalized = []
    for idx, step in enumerate(events):
        step = dict(step)
        if step.get("type") == "key" and str(step.get("key")).lower() == "wait":
            step["key"] = "Key.pause"

        anc = step.get("anchor")
        if anc:
            if not isinstance(anc, dict) or not anc.get("path"):
                # An anchor without an image path can never be replayed.
                step.pop("anchor", None)
                step["anchor_error"] = {
                    "code": "copy_failed",
                    "detail": "anchor has no image path",
                }
            else:
                src = anc["path"]
                if not os.path.isabs(src):
                    src = os.path.abspath(src)
                dst = os.path.join(assets_dir, f"step_{idx:04d}.png")
                try:
                    with Image.open(src) as img:
                        img.convert("RGB").save(dst, "PNG")
                    new_anchor = dict(anc)
                    # Store a portable relative path; resolve_relative() handles
                    # both separators on replay.
                    new_anchor["path"] = f"assets/{os.path.basename(dst)}"
                    step["anchor"] = new_anchor
                except Exception as exc:
                    # Keep the click itself, drop the unusable image reference and
                    # leave a distinguishable reason behind.
                    step.pop("anchor", None)
                    step["anchor_error"] = _copy_error(exc)

        normalized.append(step)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"version": "1.0", "events": normalized}, f, indent=2)
    return json_path


@dataclass
class Recorder:
    assets_dir: str
    config: RecorderConfig = field(default_factory=RecorderConfig)

    #: How long ``stop()`` waits for an in-flight listener callback to finish.
    _JOIN_TIMEOUT = 2.0

    def __post_init__(self) -> None:
        self.events_abs: list[dict[str, Any]] = []
        self.is_recording = False
        self._t0 = 0.0
        self.stop_event = threading.Event()
        self._lock = threading.Lock()
        # Kept True until ``stop()`` has drained and joined the listener threads.
        # ``is_recording`` flips to False as soon as F8 is seen, but a click that
        # was already captured may still be sitting in the listener backlog; it
        # must not be dropped just because the stop key arrived first.
        self._session_open = False
        self._mouse_listener = None
        self._key_listener = None

    def ts(self) -> float:
        return time.time() - self._t0

    def start(self) -> None:
        with self._lock:
            self.events_abs = []
        self.is_recording = True
        self._session_open = True
        self.stop_event.clear()
        self._t0 = time.time()
        self._mouse_listener = mouse.Listener(on_click=self._on_click)
        self._key_listener = keyboard.Listener(on_press=self._on_key_press)
        self._mouse_listener.start()
        self._key_listener.start()

    def stop(self) -> list[dict[str, Any]]:
        self.is_recording = False
        self.stop_event.set()

        listeners = [l for l in (self._mouse_listener, self._key_listener) if l is not None]
        for listener in listeners:
            try:
                listener.stop()
            except Exception:
                pass

        # F8 stops the key listener immediately, but the mouse listener (and any
        # callback already running in either thread) may still be appending an
        # event. Wait for the listener threads to drain before snapshotting, so
        # tail clicks/keys reach the saved recording.
        current = threading.current_thread()
        for listener in listeners:
            if listener is current:
                continue
            try:
                if hasattr(listener, "is_alive") and not listener.is_alive():
                    continue
                listener.join(timeout=self._JOIN_TIMEOUT)
            except Exception:
                pass

        with self._lock:
            snapshot = list(self.events_abs)
            self._session_open = False
        return normalize_recording(snapshot)

    def _on_click(self, x, y, button, pressed):
        if not self._session_open or not pressed:
            return

        anchor = None
        anchor_error = None
        try:
            apath, left, top, size = capture_anchor(x, y, self.assets_dir, self.config.anchor_size)
            anchor = {
                "path": apath,
                "conf": self.config.default_confidence,
                "timeout": self.config.default_timeout,
                "ox": int(x - left),
                "oy": int(y - top),
                "w": int(size),
                "h": int(size),
            }
        except AnchorCaptureError as exc:
            anchor_error = {"code": exc.code, "detail": exc.detail}
        except Exception as exc:
            anchor_error = {"code": "capture_failed", "detail": f"{type(exc).__name__}: {exc}"}

        ev = {"type": "click", "x": x, "y": y, "button": str(button), "t": self.ts()}
        if anchor is not None:
            ev["anchor"] = anchor
        if anchor_error is not None:
            ev["anchor_error"] = anchor_error
        with self._lock:
            self.events_abs.append(ev)

    def _on_key_press(self, key):
        if not self._session_open:
            return
        if str(key) == "Key.f8":
            # Just mark the stop; the listener threads are joined by stop(), so
            # events already delivered are never lost to exit ordering.
            self.stop_event.set()
            self.is_recording = False
            return False
        try:
            k = key.char
        except Exception:
            k = str(key)
        with self._lock:
            self.events_abs.append({"type": "key", "key": k, "t": self.ts()})
