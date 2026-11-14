from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from contextlib import closing
from pathlib import Path

from freu_cli.capture.recorder import CaptureSession, record_capture


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_record_capture_writes_events_json(tmp_path: Path):
    """End-to-end: start recorder, POST an event, stop, assert events.json."""
    out_dir = tmp_path / "capture-1"
    port = _free_port()
    stop_event = threading.Event()
    ready = threading.Event()

    captured_path: list[Path] = []

    def _runner():
        path = record_capture(
            out_dir,
            host="127.0.0.1",
            port=port,
            stop_event=stop_event,
            on_ready=lambda _handle: ready.set(),
        )
        captured_path.append(path)

    thread = threading.Thread(target=_runner)
    thread.start()
    try:
        assert ready.wait(timeout=5), "record_capture never became ready"

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST", "/bridge/capture-event",
            body=json.dumps({
                "event": {
                    "type": "click",
                    "selector": "#go",
                    "ancestors": [{"tag": "button"}],
                },
                "url": "https://demo",
                "timestamp": 42,
            }),
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        assert response.status == 200
        response.read()
        conn.close()

        stop_event.set()
    finally:
        thread.join(timeout=10)
        assert not thread.is_alive()

    assert captured_path, "recorder did not return the events.json path"
    events_path = captured_path[0]
    assert events_path == out_dir / "events.json"
    payload = json.loads(events_path.read_text())
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert payload[0]["type"] == "click"
    assert payload[0]["selector"] == "#go"
    assert payload[0]["ts"] == 42


def test_record_capture_refuses_nonempty_dir(tmp_path: Path):
    (tmp_path / "stuff.txt").write_text("hi")
    try:
        record_capture(tmp_path, stop_event=threading.Event())
    except FileExistsError as exc:
        assert "not empty" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected FileExistsError")


def test_capture_session_writes_snapshot_html_for_clicks(tmp_path: Path):
    session = CaptureSession(tmp_path)
    html = "<html><body><button>Star</button></body></html>"
    session.sink({
        "event": {
            "type": "click",
            "selector": {"tag": "button", "text": "Star"},
            "ancestors": [{"tag": "body"}],
            "snapshot_html": html,
        },
        "url": "https://demo",
        "timestamp": 12345,
    })
    snapshot_file = tmp_path / "snapshots" / "12345.html"
    assert snapshot_file.exists()
    assert snapshot_file.read_text() == html
    record = session.snapshot()[0]
    assert record["snapshot"] == "snapshots/12345.html"


def test_capture_session_omits_snapshot_field_when_no_html(tmp_path: Path):
    session = CaptureSession(tmp_path)
    session.sink({
        "event": {
            "type": "click",
            "selector": {"tag": "button"},
            "ancestors": [{"tag": "body"}],
        },
        "url": "https://demo",
        "timestamp": 1,
    })
    record = session.snapshot()[0]
    assert "snapshot" not in record
    assert not (tmp_path / "snapshots").exists()


def test_capture_session_disambiguates_snapshots_with_same_timestamp(tmp_path: Path):
    session = CaptureSession(tmp_path)
    for _ in range(3):
        session.sink({
            "event": {
                "type": "click",
                "selector": {"tag": "button"},
                "ancestors": [{"tag": "body"}],
                "snapshot_html": "<html></html>",
            },
            "timestamp": 99,
        })
    refs = [e["snapshot"] for e in session.snapshot()]
    assert refs == [
        "snapshots/99.html",
        "snapshots/99-1.html",
        "snapshots/99-2.html",
    ]
    for ref in refs:
        assert (tmp_path / ref).exists()


def test_capture_session_keeps_event_with_failure_marker_when_snapshot_dir_is_file(
    tmp_path: Path,
):
    """If `snapshots/` cannot be created as a directory, the event survives
    with an explicit marker instead of silently losing its snapshot."""
    (tmp_path / "snapshots").write_text("not a directory")

    session = CaptureSession(tmp_path)
    session.sink({
        "event": {
            "type": "click",
            "selector": {"tag": "button"},
            "ancestors": [{"tag": "body"}],
            "snapshot_html": "<html><body>hi</body></html>",
        },
        "url": "https://demo",
        "timestamp": 7,
    })

    events = session.snapshot()
    assert len(events) == 1
    record = events[0]
    assert record["type"] == "click"
    assert record["ts"] == 7
    assert record["event_id"] == "e1"
    assert record["snapshot"] is None
    assert record["snapshot_status"] == "failed"
    assert record["snapshot_error"]


def test_capture_session_keeps_event_when_snapshot_write_raises(
    tmp_path: Path, monkeypatch,
):
    session = CaptureSession(tmp_path)

    def _boom(_ts_ms: int, _html: str) -> str:
        raise OSError("disk full")

    monkeypatch.setattr(session, "_write_snapshot", _boom)
    session.sink({
        "event": {"type": "click", "selector": "#x", "snapshot_html": "<html/>"},
        "timestamp": 11,
    })

    record = session.snapshot()[0]
    assert record["event_id"] == "e1"
    assert record["snapshot"] is None
    assert record["snapshot_status"] == "failed"
    assert "disk full" in record["snapshot_error"]


def test_capture_session_orders_events_and_ids_by_arrival_under_concurrency(
    tmp_path: Path,
):
    """Same-timestamp events posted concurrently must land in arrival order,
    with contiguous ids that match their array positions."""
    session = CaptureSession(tmp_path)
    posted = 40
    barrier = threading.Barrier(posted)

    def _worker(index: int) -> None:
        barrier.wait()
        session.sink({
            "event": {
                "type": "click",
                "selector": {"tag": "button", "text": str(index)},
                "snapshot_html": f"<html data-i='{index}'></html>",
            },
            "timestamp": 5000,
        })

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(posted)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    events = session.snapshot()
    assert len(events) == posted
    assert [e["event_id"] for e in events] == [f"e{i}" for i in range(1, posted + 1)]
    # Each record's id agrees with its position, and the payload it carries
    # was fully built while holding the same lock that inserted it.
    for position, record in enumerate(events, start=1):
        assert record["event_id"] == f"e{position}"
        index = int(record["selector"]["text"])
        assert (tmp_path / record["snapshot"]).read_text() == (
            f"<html data-i='{index}'></html>"
        )

    # The flushed file preserves that order and id sequence.
    flushed = json.loads(session.flush().read_text(encoding="utf-8"))
    assert [e["event_id"] for e in flushed] == [f"e{i}" for i in range(1, posted + 1)]
    assert [e["selector"]["text"] for e in flushed] == [
        e["selector"]["text"] for e in events
    ]


def test_capture_session_sink_raises_after_flush(tmp_path: Path):
    session = CaptureSession(tmp_path)
    session.flush()
    try:
        session.sink({"event": {"type": "click"}, "timestamp": 1})
    except RuntimeError as exc:
        assert "closed" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError after flush")


def test_record_capture_drains_in_flight_events_before_returning(
    tmp_path: Path, monkeypatch,
):
    """An event still inside the sink when stop_event fires must be written
    before record_capture() returns."""
    out_dir = tmp_path / "capture-drain"
    port = _free_port()
    stop_event = threading.Event()
    ready = threading.Event()
    sink_entered = threading.Event()
    allow_finish = threading.Event()
    captured_path: list[Path] = []

    original_sink = CaptureSession.sink

    def _slow_sink(self, payload):
        sink_entered.set()
        # Hold the event "in flight" until the test has stopped the session.
        assert allow_finish.wait(timeout=10), "test never released the sink"
        original_sink(self, payload)

    monkeypatch.setattr(CaptureSession, "sink", _slow_sink)

    def _runner():
        captured_path.append(record_capture(
            out_dir, host="127.0.0.1", port=port,
            stop_event=stop_event,
            on_ready=lambda _handle: ready.set(),
        ))

    thread = threading.Thread(target=_runner)
    thread.start()
    conn = None
    try:
        assert ready.wait(timeout=5), "record_capture never became ready"
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST", "/bridge/capture-event",
            body=json.dumps({
                "event": {"type": "input", "selector": "#q", "value": "late"},
                "timestamp": 77,
            }),
            headers={"Content-Type": "application/json"},
        )
        assert sink_entered.wait(timeout=5), "event never reached the sink"

        stop_event.set()
        thread.join(timeout=0.5)
        assert thread.is_alive(), "record_capture returned while an event was in flight"

        allow_finish.set()
        thread.join(timeout=10)
        assert not thread.is_alive()
        response = conn.getresponse()
        response.read()
    finally:
        allow_finish.set()
        stop_event.set()
        thread.join(timeout=10)
        if conn is not None:
            conn.close()

    events_path = captured_path[0]
    payload = json.loads(events_path.read_text())
    assert [e["event_id"] for e in payload] == [
        f"e{i}" for i in range(1, len(payload) + 1)
    ]
    assert len(payload) == 1
    assert payload[0]["type"] == "input"
    assert payload[0]["value"] == "late"
