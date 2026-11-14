"""`freu-cli capture <path>` recorder.

Starts an in-process bridge with an active capture sink, waits until the user
hits Ctrl-C (or the optional `stop_event` fires), then writes the collected
events to `<path>/events.json`.

No intermediate files (no `events_dom.json`, no screenshots, no mouse.json) —
just one `events.json` with a JSON array of DOM event records.

The file is a complete, ordered record of everything the session accepted:

* records are appended in arrival order under a single lock, and the
  `event_id` (`e1`, `e2`, …) is allocated inside the same critical section,
  so the ids are contiguous and match their position in the array;
* an event whose snapshot HTML cannot be persisted is still recorded — its
  `snapshot` field is `null` and `snapshot_status`/`snapshot_error` carry the
  failure marker;
* when a session stops, events that were already accepted are allowed to
  finish before `record_capture()` flushes and returns the path. New events
  arriving after the stop are answered by the bridge with
  `recording=false`.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from freu_cli.capture.bridge import (
    DEFAULT_HOST,
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    DEFAULT_PORT,
    BridgeServerHandle,
    start_bridge_server,
)
from freu_cli.capture.event_record import build_event_record

logger = logging.getLogger(__name__)

SNAPSHOT_STATUS_FIELD = "snapshot_status"
SNAPSHOT_ERROR_FIELD = "snapshot_error"
SNAPSHOT_FAILED_STATUS = "failed"


class CaptureSession:
    """Collect event records in memory; flush a JSON array on stop()."""

    SNAPSHOTS_DIRNAME = "snapshots"

    def __init__(self, output_dir: Path, session_id: str | None = None) -> None:
        self.output_dir = output_dir
        self.session_id = session_id or output_dir.name or str(uuid.uuid4())
        self._events: list[dict[str, Any]] = []
        # Re-entrant because `sink()` holds the lock for the whole record
        # build (id allocation + snapshot write + append), and `_write_snapshot`
        # re-acquires it for its per-timestamp counter.
        self._lock = threading.RLock()
        self._snapshot_seq: dict[int, int] = {}
        self._event_seq = 0
        self._closed = False

    def sink(self, payload: dict[str, Any]) -> None:
        """Accept one extension payload and append its event record.

        A single lock is held from id allocation through the list append, so
        concurrent POSTs cannot interleave allocation and insertion: the
        array order is the arrival order and `event_id` increases with it.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("capture session is already closed")

            ts_ms = payload.get("timestamp")
            if not isinstance(ts_ms, int):
                ts_ms = int(datetime.now().timestamp() * 1000)

            next_seq = self._event_seq + 1
            record = build_event_record(
                ts_ms, self.session_id, payload, event_id=f"e{next_seq}",
            )

            # Pull the snapshot HTML out of the event payload and persist it
            # to a sibling `snapshots/<ts>.html` file, leaving only a
            # relative path reference in the event record so events.json
            # stays small and human-scannable. A write failure does NOT
            # discard the event; it gets a failure marker instead.
            snapshot_ref, snapshot_error = self._persist_snapshot(ts_ms, payload)

            if snapshot_ref is not None:
                record["snapshot"] = snapshot_ref
            elif snapshot_error is not None:
                record["snapshot"] = None
                record[SNAPSHOT_STATUS_FIELD] = SNAPSHOT_FAILED_STATUS
                record[SNAPSHOT_ERROR_FIELD] = snapshot_error

            # Only consume the sequence number once the record is complete.
            # A payload that cannot be turned into a record must not leave a
            # hole in the contiguous `e1, e2, ...` id sequence.
            self._event_seq = next_seq
            self._events.append(record)

    def _persist_snapshot(
        self, ts_ms: int, payload: dict[str, Any],
    ) -> tuple[str | None, str | None]:
        """Return `(relative_ref, None)` on success or `(None, error)` on failure.

        Both are `(None, None)` when the event simply carried no snapshot HTML.
        """
        event = payload.get("event")
        if not isinstance(event, dict):
            return None, None
        html = event.pop("snapshot_html", None)
        if not isinstance(html, str) or not html:
            return None, None
        try:
            ref = self._write_snapshot(ts_ms, html)
        except Exception as exc:  # noqa: BLE001 - failure is recorded, not raised
            logger.warning("capture snapshot write failed: %s", exc)
            return None, f"snapshot write failed: {exc}"
        return ref, None

    def _write_snapshot(self, ts_ms: int, html: str) -> str:
        """Persist `html` under `snapshots/<ts_ms>[-N].html` and return the
        relative path. Disambiguates if two clicks share a millisecond
        boundary by appending an incrementing suffix.

        Raises `OSError` (or any write error) when the snapshot cannot be
        stored; `sink()` turns that into a per-event failure marker.
        """
        snapshots_dir = self.output_dir / self.SNAPSHOTS_DIRNAME
        snapshots_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            seq = self._snapshot_seq.get(ts_ms, 0)
            self._snapshot_seq[ts_ms] = seq + 1
        name = f"{ts_ms}.html" if seq == 0 else f"{ts_ms}-{seq}.html"
        (snapshots_dir / name).write_text(html, encoding="utf-8")
        return f"{self.SNAPSHOTS_DIRNAME}/{name}"

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)

    def flush(self) -> Path:
        """Write `events.json` and return its path.

        The array is serialized in the order records were accepted, and the
        file is replaced atomically so a partially written capture can never
        be mistaken for a complete one. Once flushed the session is closed:
        any later `sink()` call raises instead of silently appending an
        event that would not appear in the file.
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)
        out_path = self.output_dir / "events.json"
        with self._lock:
            self._closed = True
            serialized = json.dumps(self._events, indent=2, ensure_ascii=False)
            self._write_json_atomically(out_path, serialized)
        return out_path

    @staticmethod
    def _write_json_atomically(path: Path, text: str) -> None:
        tmp_path = path.with_name(f".{path.name}.tmp")
        try:
            tmp_path.write_text(text, encoding="utf-8")
            os.replace(tmp_path, path)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass


def record_capture(
    output_dir: Path,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    idle_timeout_seconds: int = DEFAULT_IDLE_TIMEOUT_SECONDS,
    stop_event: threading.Event | None = None,
    on_ready: Callable[[BridgeServerHandle], None] | None = None,
) -> Path:
    """Run a capture session, blocking until stop_event is set or SIGINT.

    Returns the path to the written events.json. When called from the CLI the
    default `stop_event` is None — the function installs a SIGINT handler and
    blocks until Ctrl-C. In tests, pass a `stop_event` to drive shutdown
    deterministically.

    Shutdown order: the bridge stops accepting new capture events, events
    that were already accepted are drained to completion, the bridge is
    shut down, and only then is events.json flushed and the path returned.
    """
    output_dir = Path(output_dir).expanduser().resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(
            f"Capture path already exists and is not empty: {output_dir}. "
            "Pick a fresh path."
        )
    output_dir.mkdir(parents=True, exist_ok=True)

    session = CaptureSession(output_dir)
    handle = start_bridge_server(
        host=host, port=port,
        idle_timeout_seconds=idle_timeout_seconds,
        capture_sink=session.sink,
    )

    local_stop = stop_event or threading.Event()
    prev_sigint = None
    prev_sigterm = None
    installed_signals = stop_event is None
    if installed_signals:
        def _on_signal(_signum: int, _frame: object) -> None:
            local_stop.set()

        prev_sigint = signal.signal(signal.SIGINT, _on_signal)
        prev_sigterm = signal.signal(signal.SIGTERM, _on_signal)

    try:
        if on_ready is not None:
            on_ready(handle)
        while not local_stop.is_set():
            time.sleep(0.2)
    finally:
        # Close the capture gate first: requests that have already claimed
        # the sink keep their reference and are allowed to finish. New ones
        # see no session and are answered with recording=false.
        handle.state.set_capture_sink(None)
        if not handle.state.wait_for_capture_idle():
            logger.warning(
                "capture shutdown timed out with %d event(s) still being "
                "written; events.json may be missing them",
                handle.state.capture_in_flight(),
            )
        handle.shutdown()
        if installed_signals:
            if prev_sigint is not None:
                signal.signal(signal.SIGINT, prev_sigint)
            if prev_sigterm is not None:
                signal.signal(signal.SIGTERM, prev_sigterm)

    return session.flush()
