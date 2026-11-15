#!/usr/bin/env python3
"""
JMComic downloader - local web UI.

Instead of a native GUI toolkit, this serves a small page on 127.0.0.1 and opens the
user's browser: no native graphics stack to bundle, so freezing a build is just Python
plus jmcomic, and all four platforms share one interface implementation.

All download work is delegated to `jmcore`, the same engine the CLI and the agent
skill use.

    python webui/server.py                 # serve, open the browser
    python webui/server.py --no-browser    # just serve (print the URL)
    python webui/server.py --port 8765

Security model
--------------
* Binds 127.0.0.1 only.
* Requires a random per-run token on every request, so another process (or a web page
  the user has open, via a blind POST to localhost) cannot drive this one.
* Validates the Host header to blunt DNS-rebinding.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import secrets
import socket
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_HERE = Path(__file__).resolve().parent
for _candidate in (_HERE, _HERE.parent / "scripts", _HERE.parent):
    if (_candidate / "jmcore.py").is_file():
        sys.path.insert(0, str(_candidate))
        break
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

try:
    import jmcore
except ImportError as exc:  # pragma: no cover
    raise SystemExit(f"jmcore.py not found near {_HERE}: {exc}") from exc

try:
    from ui import INDEX_HTML
except ImportError as exc:  # pragma: no cover
    raise SystemExit(f"ui.py not found beside server.py: {exc}") from exc


# --------------------------------------------------------------------------- #
# job state and event fan-out
# --------------------------------------------------------------------------- #

# Per-book outcomes. `pending` is the "未开始" state: a book the batch never got
# to because the user cancelled first. `running` only appears in live events; a
# finished batch never reports it.
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

STATUS_LABELS = {
    STATUS_PENDING: "未开始",
    STATUS_RUNNING: "下载中",
    STATUS_SUCCESS: "成功",
    STATUS_FAILED: "失败",
    STATUS_CANCELLED: "取消",
}


class Hub:
    """Broadcasts progress events to every connected browser."""

    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue] = []
        self._backlog: list[dict] = []

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subscribers.append(q)
            for event in self._backlog:
                q.put(event)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def publish(self, event: dict) -> None:
        with self._lock:
            # Keep a short replay buffer so a browser that connects mid-download still
            # sees recent progress instead of an empty pane. The plan/item/done events
            # are included because the per-book result table is built from them: a page
            # that connects (or reconnects) after a fast batch must still render it.
            if event.get("type") in ("log", "result", "plan", "item", "done"):
                self._backlog.append(event)
                del self._backlog[:-200]
            for q in self._subscribers:
                q.put(event)

    def reset_backlog(self) -> None:
        with self._lock:
            self._backlog.clear()


class Job:
    """One download run. Only one may be active at a time.

    `_active` is claimed atomically by `reserve()`, before the worker thread exists, so
    two requests arriving at the same moment cannot both pass the "is a job running?"
    check and start a second batch.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.cancel = threading.Event()
        self._active = False
        # Monotonic timestamp of the cancel request, if any. The worker compares it
        # with the moment each book finished so a cancel that arrives *after* a book
        # completed does not retroactively turn that book into "cancelled".
        self.cancel_at: float | None = None

    @property
    def running(self) -> bool:
        with self.lock:
            return self._active

    def reserve(self) -> bool:
        """Claim the single job slot. Returns False when one is already taken."""
        with self.lock:
            if self._active:
                return False
            self._active = True
            return True

    def release(self) -> None:
        with self.lock:
            self._active = False

    def begin(self) -> None:
        """Reset cancel state for a new batch, atomically with respect to cancel."""
        with self.lock:
            self.cancel_at = None
            self.cancel.clear()

    def request_cancel(self) -> None:
        with self.lock:
            if self.cancel_at is None:
                self.cancel_at = time.monotonic()
            # Set the flag under the same lock as the timestamp: a worker that sees the
            # flag must never also see a missing timestamp and misclassify the book.
            self.cancel.set()

    def cancelled_before(self, finished_at: float) -> bool:
        if not self.cancel.is_set():
            return False
        with self.lock:
            started_at = self.cancel_at
        return started_at is not None and started_at <= finished_at


HUB = Hub()
JOB = Job()


def _new_entry(index: int, raw: str, resolved_id: str | None) -> dict:
    """One row of the per-book outcome table.

    `index` is stable for the lifetime of a batch and is how the page maps a later
    `item` event back to its row, so duplicate ids cannot update the wrong row.
    """
    return {
        "index": index,
        "id": resolved_id or raw,
        "input": raw,
        "resolved": resolved_id is not None,
        "status": STATUS_PENDING,
        "label": STATUS_LABELS[STATUS_PENDING],
        "imageCount": None,
        "exportFiles": [],
        "savePath": "",
        "title": None,
        "error": None,
        "note": None,
    }


def _snapshot(entry: dict) -> dict:
    """A shallow copy safe to publish: the worker keeps mutating its own entry."""
    snap = dict(entry)
    snap["exportFiles"] = list(entry.get("exportFiles") or [])
    return snap


def _set_status(entry: dict, status: str, **fields) -> None:
    entry["status"] = status
    entry["label"] = STATUS_LABELS[status]
    entry.update(fields)


def _failure_text(exc: Exception) -> str:
    if isinstance(exc, jmcore.OperationError):
        return exc.message if not exc.hint else f"{exc.message}（{exc.hint}）"
    return f"{type(exc).__name__}: {exc}"


def _result_facts(payload) -> tuple:
    """(imageCount, exportFiles, savePath, title) from run_download's report.

    An album download yields one job per chapter, so counts and export files are
    summed across them and the save path is narrowed to their common directory.
    """
    if not isinstance(payload, dict):
        return 0, [], "", None
    jobs = payload.get("results") if isinstance(payload.get("results"), list) else [payload]
    image_count = 0
    exports: list[str] = []
    save_paths: list[str] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        try:
            image_count += int(job.get("imageCount") or 0)
        except (TypeError, ValueError):
            pass
        exports.extend(str(p) for p in (job.get("exportFiles") or []))
        if job.get("savePath"):
            save_paths.append(str(job["savePath"]))

    save_path = save_paths[0] if save_paths else ""
    if len(save_paths) > 1:
        try:
            save_path = os.path.commonpath(save_paths)
        except (ValueError, OSError):
            pass
    first = jobs[0] if jobs and isinstance(jobs[0], dict) else {}
    return image_count, exports, save_path, first.get("title")


def _summary(entries: list[dict]) -> dict:
    counts = {status: 0 for status in STATUS_LABELS}
    for entry in entries:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    return {
        "total": len(entries),
        "success": counts[STATUS_SUCCESS],
        "failed": counts[STATUS_FAILED],
        "cancelled": counts[STATUS_CANCELLED],
        "pending": counts[STATUS_PENDING],
        "running": counts[STATUS_RUNNING],
    }


def _done_payload(entries: list[dict], fatal: Exception | None) -> dict:
    """Build the terminal event: summary counts plus one row per book."""
    summary = _summary(entries)
    if fatal is not None:
        outcome, text = "error", f"任务失败：{_failure_text(fatal)}"
    elif summary["cancelled"] or summary["pending"]:
        outcome = "cancelled"
        text = (f"已取消：共 {summary['total']} 本，成功 {summary['success']} 本，"
                f"失败 {summary['failed']} 本，取消 {summary['cancelled']} 本，"
                f"未开始 {summary['pending']} 本")
    elif summary["failed"] and summary["success"] == 0:
        # "部分失败" would be misleading when nothing succeeded.
        outcome = "failed"
        text = f"全部失败：共 {summary['total']} 本，全部失败"
    elif summary["failed"]:
        outcome = "partial"
        text = (f"部分失败：共 {summary['total']} 本，成功 {summary['success']} 本，"
                f"失败 {summary['failed']} 本")
    else:
        outcome, text = "success", "全部完成"
    return {
        "type": "done",
        "outcome": outcome,       # success | partial | failed | cancelled | error
        "text": text,
        "summary": summary,
        "items": [_snapshot(e) for e in entries],
    }


def _run_job(settings_payload: dict) -> None:
    """Worker: download each id in turn so cancel can act between them.

    Every book gets its own outcome record. A failure is recorded and the loop moves
    on to the next id; only JOB.cancel stops the remainder. A book that was still
    running when cancel arrived is reported as *cancelled* - neither success nor
    failure, since its result cannot be treated as complete.
    """
    log_handler = None
    entries: list[dict] = settings_payload["entries"]
    fatal: Exception | None = None
    try:
        try:
            jmcomic = jmcore.load_jmcomic()
            log_handler = jmcore.attach_log_sink(
                lambda text: HUB.publish({"type": "log", "text": text}))
        except Exception as e:  # pragma: no cover - only if jmcomic is broken
            fatal = e
            HUB.publish({"type": "log", "text": traceback.format_exc()})
            HUB.publish({"type": "error", "text": _failure_text(e)})

        if fatal is None:
            fatal = _download_each(entries, settings_payload, jmcomic)

        if fatal is not None:
            # Nothing can be attributed a verdict any more: a book that was mid-flight
            # did not succeed, and a book that never started never will in this run.
            for entry in entries:
                if entry["status"] in (STATUS_RUNNING, STATUS_PENDING):
                    _set_status(entry, STATUS_FAILED, error=_failure_text(fatal))
        # Publish done BEFORE releasing the job slot: a new batch must never reset
        # the backlog and emit its plan in between this batch's last row and its
        # terminal event.
        HUB.publish(_done_payload(entries, fatal))
    except Exception as e:  # pragma: no cover - last-resort so the UI always settles
        HUB.publish({"type": "log", "text": traceback.format_exc()})
        HUB.publish({"type": "error", "text": _failure_text(e)})
        try:
            HUB.publish(_done_payload(entries, e))
        except Exception:
            HUB.publish({"type": "done", "outcome": "error",
                         "text": f"任务失败：{_failure_text(e)}", "summary": {},
                         "items": []})
    finally:
        if log_handler is not None:
            jmcore.detach_log_sink(log_handler)
        with JOB.lock:
            JOB.thread = None
        JOB.release()


def _download_each(entries: list[dict], settings_payload: dict,
                   jmcomic) -> Exception | None:
    """Run one book at a time, recording each verdict. Returns a fatal error, if any.

    Only errors that escape per-book handling (e.g. the cancel flag machinery itself)
    become fatal; a normal download failure is stored on its own row.
    """
    for entry in entries:
        if entry["status"] != STATUS_PENDING:
            continue          # unparseable ids were already settled at submit
        if JOB.cancel.is_set():
            break             # the rest stay 'pending' (未开始)

        _set_status(entry, STATUS_RUNNING)
        HUB.publish({"type": "item", "item": _snapshot(entry)})

        settings = jmcore.DownloadSettings(
            targets=[entry["id"]],
            kind=settings_payload["kind"],
            exports=settings_payload["exports"],
            save_dir=settings_payload["saveDir"],
            threads=settings_payload["threads"],
            proxy=settings_payload["proxy"],
            http_backend=settings_payload["backend"],
            dir_rule="Bd / Aid / Ptitle",
        )
        payload = None
        error: str | None = None
        try:
            payload = jmcore.run_download(settings, jmcomic=jmcomic,
                                          progress_sink=lambda t: HUB.publish(
                                              {"type": "log", "text": t}))
        except Exception as e:
            # One book failing must never abort the batch. Unexpected exceptions
            # are logged in full but still only fail this book.
            if not isinstance(e, jmcore.OperationError):
                HUB.publish({"type": "log", "text": traceback.format_exc()})
            error = _failure_text(e)
        finished_at = time.monotonic()

        if JOB.cancelled_before(finished_at):
            # Cancel arrived while this book was downloading: its partial result
            # (or its error) is deliberately not counted as success or failure.
            _set_status(entry, STATUS_CANCELLED, note="下载中被取消")
            HUB.publish({"type": "item", "item": _snapshot(entry)})
            HUB.publish({"type": "log",
                         "text": f"[取消] JM{entry['id']} 下载中被取消，不计入成功或失败"})
            break

        if error is not None:
            _set_status(entry, STATUS_FAILED, error=error)
            HUB.publish({"type": "error", "text": f"JM{entry['id']}：{error}"})
            HUB.publish({"type": "item", "item": _snapshot(entry)})
            continue

        image_count, exports, save_path, title = _result_facts(payload)
        _set_status(entry, STATUS_SUCCESS, imageCount=image_count,
                    exportFiles=exports, savePath=save_path, title=title)
        # Kept for compatibility with existing consumers of the SSE stream.
        HUB.publish({"type": "result", "item": payload})
        HUB.publish({"type": "item", "item": _snapshot(entry)})
    return None


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

class Handler(BaseHTTPRequestHandler):
    server_version = "jmcomic-downloader"
    protocol_version = "HTTP/1.1"
    token = ""
    allowed_hosts: set[str] = set()

    # -- helpers -------------------------------------------------------- #

    def log_message(self, fmt, *args):  # quieter than the default stderr spam
        pass

    def _authorised(self, query: dict) -> bool:
        supplied = (query.get("token") or [""])[0]
        if not secrets.compare_digest(str(supplied), self.token):
            self._json({"error": "invalid or missing token"}, 403)
            return False
        host = (self.headers.get("Host") or "").split(":")[0]
        if host and host not in self.allowed_hosts:
            self._json({"error": "unexpected Host header"}, 403)
            return False
        return True

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    # -- routes --------------------------------------------------------- #

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path in ("/", "/index.html"):
            # The per-run token is injected into the page rather than required in the
            # URL: python-for-android's webview bootstrap loads a fixed
            # "http://127.0.0.1:PORT/" with no query string, so a URL-only token would
            # leave the Android UI unable to call its own API. API requests still carry
            # the token, so another local process still cannot drive this one.
            body = INDEX_HTML.replace("__TOKEN__", self.token).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if not self._authorised(query):
            return

        if parsed.path == "/api/config":
            self._json(_config_payload())
        elif parsed.path == "/api/events":
            self._events()
        elif parsed.path == "/api/status":
            self._json({"running": JOB.running})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not self._authorised(query):
            return

        if parsed.path == "/api/download":
            self._start_download(self._body())
        elif parsed.path == "/api/cancel":
            JOB.request_cancel()
            self._json({"ok": True})
        elif parsed.path == "/api/shutdown":
            self._json({"ok": True})
            HUB.publish({"type": "log", "text": "正在退出…"})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self._json({"error": "not found"}, 404)

    # -- handlers ------------------------------------------------------- #

    def _events(self):
        """Server-Sent Events stream of job progress."""
        q = HUB.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                try:
                    event = q.get(timeout=15)
                except queue.Empty:
                    # Comment frame keeps proxies and the browser from timing out.
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                data = json.dumps(event, ensure_ascii=False)
                self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            HUB.unsubscribe(q)

    def _start_download(self, body: dict):
        # Claim the slot up front (atomically) rather than checking `running` and
        # assigning the thread afterwards: otherwise two simultaneous POSTs could both
        # see an idle job and start two batches.
        if not JOB.reserve():
            self._json({"error": "已有任务在下载中，请先等待或取消"}, 409)
            return
        # Reset cancel state as soon as the slot is ours, so a cancel that arrives
        # while the ids are being parsed is honoured by the worker rather than cleared
        # later by this request.
        JOB.begin()

        started = False
        try:
            started = self._start_download_reserved(body)
        except Exception as e:  # setup failed; surface it rather than dying silently
            try:
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            except Exception:
                pass
        if not started:
            # `_start_download_reserved` only returns True once the worker owns the
            # slot; in every other path nothing is running, so return it. (Checking
            # JOB.running here would always be True, since reserve() set it.)
            JOB.release()

    def _start_download_reserved(self, body: dict) -> bool:
        """Build and launch a batch. Returns True once the worker thread is running."""
        raw_ids = body.get("ids") or ""
        jmcomic = None
        try:
            jmcomic = jmcore.load_jmcomic()
        except jmcore.OperationError as e:
            self._json({"error": e.message}, 500)
            return False

        # Every token the user submitted gets a row, including ones that cannot be
        # parsed: those are reported as failed rows rather than silently dropped, so
        # the final table accounts for everything the user typed.
        entries: list[dict] = []
        parsed: list[str] = []
        bad: list[str] = []
        for index, token in enumerate(jmcore.split_id_text(str(raw_ids))):
            try:
                resolved = jmcore.require_id(jmcomic, token)
            except jmcore.OperationError as e:
                entry = _new_entry(index, token, None)
                _set_status(entry, STATUS_FAILED, error=_failure_text(e))
                entries.append(entry)
                bad.append(token)
                continue
            parsed.append(resolved)
            entries.append(_new_entry(index, token, resolved))
        if not parsed:
            self._json({"error": "没有可用的车号"}, 400)
            return False

        exports = [s for s in (body.get("exports") or []) if s in jmcore.EXPORT_KINDS]
        settings_payload = {
            "entries": entries,
            "kind": "photo" if body.get("kind") == "photo" else "album",
            "exports": exports,
            "saveDir": (body.get("saveDir") or "").strip() or str(jmcore.default_download_dir()),
            "threads": max(1, min(50, int(body.get("threads") or 30))),
            "proxy": (body.get("proxy") or "").strip(),
            "backend": body.get("backend") or jmcore.default_http_backend(),
        }

        HUB.reset_backlog()
        if bad:
            HUB.publish({"type": "log",
                         "text": f"[失败] 无法识别的车号：{', '.join(bad)}"})
        # Publish the plan after resetting the backlog, so a page that subscribes now
        # can draw every row (all 未开始) before the first book starts.
        HUB.publish({"type": "plan", "items": [_snapshot(e) for e in entries],
                     "summary": _summary(entries)})
        JOB.thread = threading.Thread(target=_run_job, args=(settings_payload,), daemon=True)
        JOB.thread.start()
        # The worker now owns the slot. Any failure writing the HTTP response (e.g. the
        # browser disconnected) must not release it out from under the running batch,
        # so the response is written defensively and always reported as "started".
        try:
            self._json({"ok": True, "ids": parsed,
                        "items": [_snapshot(e) for e in entries],
                        "summary": _summary(entries)})
        except Exception:
            pass
        return True


def _config_payload() -> dict:
    missing = []
    for suffix in jmcore.EXPORT_KINDS:
        for entry in jmcore.missing_export_dependencies([suffix]):
            missing.append(entry.split(":", 1)[-1])
    return {
        "saveDir": str(jmcore.default_download_dir()),
        "backend": jmcore.default_http_backend(),
        "android": jmcore.is_android(),
        "missingDeps": sorted(set(missing)),
        "python": sys.version.split()[0],
    }


# --------------------------------------------------------------------------- #
# startup
# --------------------------------------------------------------------------- #

def pick_port(preferred: int = 0) -> int:
    """Return a free port, preferring `preferred` when it is available."""
    for candidate in ([preferred] if preferred else []) + [0]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", candidate))
                return sock.getsockname()[1]
            except OSError:
                continue
    raise SystemExit("could not bind a local port")


ANDROID_WEBVIEW_PORT = 5000
"""python-for-android's webview bootstrap pings and loads this port by default."""


def serve(port: int = 0, open_browser: bool = True, quiet: bool = False,
          url_file: str | None = None) -> int:
    jmcore.reconfigure_streams_utf8()
    jmcore.route_logs_to_stderr()

    android = jmcore.is_android()
    if android:
        # On Android the webview bootstrap pings and loads a fixed port, and there is no
        # external browser to open.
        port = port or ANDROID_WEBVIEW_PORT
        open_browser = False
        # Warm up ctypes.util HERE, on the main thread. p4a patches it to import the
        # `android` module, which needs the Activity's ClassLoader - unavailable on the
        # download worker thread. Warming it up now means the worker's import is a no-op.
        if not quiet:
            print(f"[jmcomic] ctypes.util: {jmcore.ensure_ctypes_util_importable()}",
                  flush=True)
        else:
            jmcore.ensure_ctypes_util_importable()

    token = secrets.token_urlsafe(24)
    try:
        chosen = pick_port(port)
    except SystemExit:
        if android:
            raise SystemExit(
                f"port {ANDROID_WEBVIEW_PORT} is unavailable, but the Android WebView "
                f"is hardcoded to load it")
        raise
    Handler.token = token
    Handler.allowed_hosts = {"127.0.0.1", "localhost", "[::1]"}

    httpd = ThreadingHTTPServer(("127.0.0.1", chosen), Handler)
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{chosen}/?token={token}"

    # A --windowed bundle has sys.stdout set to None, so printing the URL is not
    # enough to find it. Writing it to a file makes a packaged build drivable by a
    # script (and recoverable by a user if the browser did not open).
    if url_file:
        try:
            Path(url_file).expanduser().write_text(url + "\n", encoding="utf-8")
        except OSError as e:
            print(f"could not write {url_file}: {e}", file=sys.stderr)

    if not quiet:
        print("JMComic 下载器已启动")
        print(f"  地址: {url}")
        if url_file:
            print(f"  地址也已写入: {url_file}")
        print("  停止: 在此窗口按 Ctrl+C（或点页面里的「退出程序」）")
        print()

    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()
        if url_file:
            try:
                Path(url_file).expanduser().unlink()
            except OSError:
                pass
    return 0


def _selftest(album_id: str = "438696") -> int:
    """
    Non-GUI verification for packaged builds: prove the bundle can import jmcomic,
    reach the site and write files.

    The result is written to a file as well as stdout, because a frozen --windowed
    build has sys.stdout set to None and `print` would silently discard everything.
    `gui/build.py --verify` reads that file back.
    """
    import tempfile

    report = Path(tempfile.gettempdir()) / "jmcomic-downloader-selftest.log"
    lines: list[str] = []

    def emit(text=""):
        lines.append(str(text))
        try:
            if sys.stdout is not None:
                print(text)
        except Exception:
            pass
        try:
            report.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception:
            pass
        return text

    def finish(code: int, summary: str) -> int:
        emit(summary)
        return code

    jmcore.route_logs_to_stderr()
    emit(f"android={jmcore.is_android()} platform={sys.platform}")
    emit(f"http backend={jmcore.default_http_backend()} python={sys.version.split()[0]}")
    emit(f"report={report}")

    try:
        jmcomic = jmcore.load_jmcomic()
    except jmcore.OperationError as e:
        return finish(1, f"FAIL import: {e.message}")
    emit(f"jmcomic {getattr(jmcomic, '__version__', '?')} from {jmcomic.__file__}")

    # The UI is embedded, so a frozen build either has it or does not.
    emit(f"ui embedded={len(INDEX_HTML) > 1000} bytes={len(INDEX_HTML)}")

    with tempfile.TemporaryDirectory(prefix="jm-selftest-") as tmp:
        settings = jmcore.DownloadSettings(
            targets=[album_id],
            kind="photo",
            exports=["pdf"],
            save_dir=tmp,
            threads=4,
            http_backend=jmcore.default_http_backend(),
        )
        try:
            payload = jmcore.run_download(settings, jmcomic=jmcomic,
                                          progress_sink=emit)
        except Exception as e:
            return finish(1, f"FAIL download: {type(e).__name__}: {e}")

        item = payload
        emit(f"downloaded id={item.get('id')} images={item.get('imageCount')} "
             f"duration={item.get('durationSec')}s")
        emit(f"savePath={item.get('savePath')}")
        for path in item.get("exportFiles") or []:
            emit(f"export={path}")
        if not item.get("imageCount"):
            return finish(1, "FAIL: no images were written")

    return finish(0, "SELFTEST OK")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Serve the JMComic downloader UI on localhost.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=0,
                        help="Port to bind (0 = pick a free one).")
    parser.add_argument("--no-browser", action="store_true",
                        help="Do not open a browser automatically.")
    parser.add_argument("--quiet", action="store_true", help="Suppress the banner.")
    parser.add_argument("--url-file", default=None, metavar="PATH",
                        help="Write the UI URL here. Needed for --windowed builds, "
                             "which have no console to print it to.")
    parser.add_argument("--selftest", nargs="?", const="438696", default=None,
                        metavar="ALBUM_ID",
                        help="Verify a packaged build by downloading one chapter, then exit.")
    args = parser.parse_args(argv)

    if args.selftest:
        try:
            return _selftest(args.selftest)
        except Exception:
            detail = traceback.format_exc()
            try:
                (Path(__import__("tempfile").gettempdir())
                 / "jmcomic-downloader-error.log").write_text(detail, encoding="utf-8")
            except Exception:
                pass
            print(detail, file=sys.stderr)
            return 1

    return serve(port=args.port, open_browser=not args.no_browser, quiet=args.quiet,
                 url_file=args.url_file)


if __name__ == "__main__":
    sys.exit(main())
