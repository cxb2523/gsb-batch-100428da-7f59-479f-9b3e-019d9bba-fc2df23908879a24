from dotenv import load_dotenv

load_dotenv()

import asyncio
import importlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import time
import uuid
from pathlib import Path
import mimetypes
import datetime
from fastapi import FastAPI, HTTPException, UploadFile, File, Body, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from contextlib import asynccontextmanager

# Ensure DBUS_SESSION_BUS_ADDRESS is set so OculOS can connect to the session bus.
if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
    try:
        _result = subprocess.run(
            ["dbus-launch", "--sh-syntax"], capture_output=True, text=True
        )
        for _line in _result.stdout.splitlines():
            if "=" in _line:
                _key, _, _val = _line.partition("=")
                os.environ[_key.strip()] = _val.strip().rstrip(";").strip("'")
    except Exception:
        # dbus-launch is only available inside the Linux container — importing
        # this module elsewhere (e.g. for tests) must not explode.
        pass

import state as _state
from state import pause_event
from codegen import generate, CodegenError

# /workspace is the data directory inside the container. The env override lets
# local (non-container) verification point at a scratch directory.
FILES_ROOT = Path(os.environ.get("ORBIT_WORKSPACE", "/workspace"))
WORKFLOW_JSON = FILES_ROOT / "workflow.json"  # legacy migration source
WORKFLOW_PY = FILES_ROOT / "workflow.py"  # always overwritten by current workflow
SECRETS_FILE = FILES_ROOT / ".secrets.env"  # legacy migration source
ENV_FILE = FILES_ROOT / ".env"  # legacy migration source
DB_PATH = FILES_ROOT / "orbit.db"
RUNS_DIR = FILES_ROOT / "runs"

agent_task: asyncio.Task | None = None
_current_run_id: str | None = None
_current_run_workflow_id: str | None = None
_current_workflow_name: str = ""
_current_trigger: str = ""

# Set once the generated workflow reports `__workflow__` success. Generated code
# then holds the browser session open for human inspection — for scheduling we
# treat that as "work finished", so a queued trigger can take over immediately.
_logically_completed = False
# Runs whose workflow already completed successfully. Used to keep the final
# status `success` when we cancel the trailing 'hold screen open' loop.
_completed_run_ids: set[str] = set()

# ── Pending trigger queue ─────────────────────────────────────────────────────
# A trigger that arrives while a workflow is executing (or while the user has
# paused execution) is not dropped: it is recorded as a `queued` run and starts
# automatically once the current run finishes / execution is resumed.
# At most one queued entry per workflow is kept — a newer trigger supersedes the
# older queued one (which is marked `cancelled`), so repeated cron firings while
# a long run is in progress cannot pile up.
_pending_queue: list[dict] = []  # FIFO, each item is a queued run
_pending_by_workflow: dict[str, dict] = {}  # workflow_id -> queued item
_queue_wakeup: asyncio.Event | None = None  # created in lifespan
_start_lock = asyncio.Lock()  # serialises check-then-start across trigger sources

RUN_STATUSES = ("queued", "running", "success", "error", "stopped", "cancelled")


# ── SQLite helpers ────────────────────────────────────────────────────────────


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _reload_secrets() -> None:
    """Load all secrets from DB into os.environ."""
    with _db() as con:
        for row in con.execute("SELECT key, value FROM secrets"):
            os.environ[row["key"]] = row["value"]


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _db() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS workflows (
                id      TEXT PRIMARY KEY,
                name    TEXT NOT NULL DEFAULT 'Untitled',
                graph   TEXT NOT NULL,
                created REAL NOT NULL,
                updated REAL NOT NULL
            )
        """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS secrets (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id            TEXT PRIMARY KEY,
                workflow_id   TEXT NOT NULL,
                workflow_name TEXT NOT NULL,
                started_at    REAL NOT NULL,
                finished_at   REAL,
                status        TEXT,
                log_file      TEXT
            )
        """
        )
        # Additive migration: triggers are recorded with the source that caused
        # them (manual / webhook / cron) and the moment they were queued.
        existing_cols = {r["name"] for r in con.execute("PRAGMA table_info(runs)")}
        for col, ddl in (
            ("trigger", "TEXT"),
            ("queued_at", "REAL"),
        ):
            if col not in existing_cols:
                con.execute(f"ALTER TABLE runs ADD COLUMN {col} {ddl}")

    # Migrate legacy workflow.json → DB (only if DB is empty)
    with _db() as con:
        if con.execute("SELECT COUNT(*) FROM workflows").fetchone()[0] == 0:
            if WORKFLOW_JSON.exists():
                now = time.time()
                con.execute(
                    "INSERT INTO workflows VALUES (?,?,?,?,?)",
                    (
                        str(uuid.uuid4()),
                        "Untitled",
                        WORKFLOW_JSON.read_text(),
                        now,
                        now,
                    ),
                )

    # Migrate legacy .secrets.env and .env → DB (only if secrets table is empty)
    with _db() as con:
        if con.execute("SELECT COUNT(*) FROM secrets").fetchone()[0] == 0:
            for src in (SECRETS_FILE, ENV_FILE):
                if src.exists():
                    for line in src.read_text().splitlines():
                        line = line.strip()
                        if "=" in line and not line.startswith("#"):
                            k, _, v = line.partition("=")
                            con.execute(
                                "INSERT OR IGNORE INTO secrets VALUES (?,?)",
                                (k.strip(), v.strip()),
                            )

    _reload_secrets()


def _mark_stale_runs() -> None:
    """Runs left `running`/`queued` by a previous process are not alive any more."""
    now = time.time()
    with _db() as con:
        con.execute(
            "UPDATE runs SET status='stopped', finished_at=? WHERE status='running'",
            (now,),
        )
        con.execute(
            "UPDATE runs SET status='cancelled', finished_at=? WHERE status='queued'",
            (now,),
        )


# ── App lifecycle ─────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _queue_wakeup
    init_db()
    _mark_stale_runs()
    _queue_wakeup = asyncio.Event()
    _state.set_workflow_listener(_on_workflow_report)
    scheduler = asyncio.create_task(_scheduler_loop())
    dispatcher = asyncio.create_task(_queue_dispatcher())
    yield
    scheduler.cancel()
    dispatcher.cancel()
    _state.set_workflow_listener(None)
    if agent_task and not agent_task.done():
        agent_task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(agent_task), timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Secrets endpoints ─────────────────────────────────────────────────────────


@app.get("/secrets")
async def secrets_list():
    """Return secret keys only — values never leave the server."""
    with _db() as con:
        keys = [
            row["key"] for row in con.execute("SELECT key FROM secrets ORDER BY key")
        ]
    return {"keys": keys}


@app.post("/secrets")
async def secrets_save(payload: dict):
    """Full replace of secrets table from payload."""
    secrets = [s for s in payload.get("secrets", []) if s.get("key", "").strip()]
    with _db() as con:
        con.execute("DELETE FROM secrets")
        for s in secrets:
            con.execute(
                "INSERT INTO secrets VALUES (?,?)",
                (s["key"].strip(), s["value"]),
            )
    _reload_secrets()
    return {"status": "saved", "count": len(secrets)}


# ── Workflow CRUD endpoints ───────────────────────────────────────────────────


@app.get("/workflows")
async def workflows_list():
    with _db() as con:
        rows = con.execute(
            "SELECT id, name, created, updated FROM workflows ORDER BY updated DESC"
        ).fetchall()
    return {"workflows": [dict(r) for r in rows]}


@app.post("/workflows")
async def workflow_create(payload: dict = {}):
    wid = str(uuid.uuid4())
    name = payload.get("name", "Untitled")
    now = time.time()
    empty = json.dumps(
        {
            "version": "1",
            "global": {"llm": "gemini-3-flash-preview", "human_in_the_loop": False},
            "nodes": [],
            "edges": [],
        }
    )
    with _db() as con:
        con.execute(
            "INSERT INTO workflows VALUES (?,?,?,?,?)", (wid, name, empty, now, now)
        )
    return {"id": wid, "name": name}


@app.delete("/workflows/{wid}")
async def workflow_delete(wid: str):
    with _db() as con:
        cur = con.execute("DELETE FROM workflows WHERE id=?", (wid,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Workflow not found")
    return {"status": "deleted"}


@app.patch("/workflows/{wid}/rename")
async def workflow_rename(wid: str, payload: dict):
    name = payload.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    with _db() as con:
        cur = con.execute(
            "UPDATE workflows SET name=?, updated=? WHERE id=?",
            (name, time.time(), wid),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Workflow not found")
    return {"id": wid, "name": name}


# ── Workflow graph endpoints ──────────────────────────────────────────────────


@app.get("/workflow/load")
async def workflow_load(id: str | None = None):
    with _db() as con:
        if id:
            row = con.execute(
                "SELECT graph FROM workflows WHERE id=?", (id,)
            ).fetchone()
        else:
            row = con.execute(
                "SELECT graph FROM workflows ORDER BY updated DESC LIMIT 1"
            ).fetchone()
    if not row:
        return {"graph": None, "id": None}
    # Also return the id so the frontend knows which workflow is loaded
    with _db() as con:
        if id:
            meta = con.execute(
                "SELECT id, name FROM workflows WHERE id=?", (id,)
            ).fetchone()
        else:
            meta = con.execute(
                "SELECT id, name FROM workflows ORDER BY updated DESC LIMIT 1"
            ).fetchone()
    return {"graph": json.loads(row["graph"]), "id": meta["id"], "name": meta["name"]}


@app.post("/workflow/save")
async def workflow_save(payload: dict):
    wid = payload.get("id")
    graph = payload.get("graph")
    if not wid or graph is None:
        raise HTTPException(status_code=400, detail="id and graph are required")
    with _db() as con:
        cur = con.execute(
            "UPDATE workflows SET graph=?, updated=? WHERE id=?",
            (json.dumps(graph), time.time(), wid),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Workflow not found")
    return {"status": "saved"}


@app.post("/workflow/generate")
async def workflow_generate(payload: dict = {}):
    wid = payload.get("id")
    with _db() as con:
        if wid:
            row = con.execute(
                "SELECT graph FROM workflows WHERE id=?", (wid,)
            ).fetchone()
        else:
            row = con.execute(
                "SELECT graph FROM workflows ORDER BY updated DESC LIMIT 1"
            ).fetchone()
    if not row:
        return {"status": "error", "message": "No workflow found. Create one first."}
    try:
        graph_data = json.loads(row["graph"])
        code = generate(graph_data)
        WORKFLOW_PY.write_text(code)
        return {"status": "generated", "length": len(code)}
    except CodegenError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {e}"}


@app.get("/workflow/preview")
async def workflow_preview():
    if WORKFLOW_PY.exists():
        return {"code": WORKFLOW_PY.read_text()}
    return {"code": None}


# ── SSE execution events ──────────────────────────────────────────────────────


@app.get("/events")
async def events():
    q: asyncio.Queue = asyncio.Queue()
    _state._sse_queues.append(q)

    async def stream():
        snapshot = json.dumps(
            {
                "type": "snapshot",
                "statuses": _state.get_node_statuses(),
                "outputs": _state.get_node_outputs(),
                "logs": _state.get_node_logs(),
            }
        )
        yield f"data: {snapshot}\n\n"
        try:
            while True:
                event = await q.get()
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            _state._sse_queues.remove(q)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Agent control: triggers, queue, execution ────────────────────────────────
#
# Execution model
# ---------------
# Only one workflow runs at a time. Every trigger (manual /start, /webhook,
# cron) goes through `_trigger_workflow`, which either launches a run
# immediately (when the slot is free) or records it as a `queued` run.
#
# The queue holds at most one entry per workflow: a newer trigger for a
# workflow that already has a pending entry supersedes it, and the superseded
# run is marked `cancelled` so it is still visible in /runs.
#
# A background dispatcher drains the queue whenever a run finishes (or a
# generated workflow reports completion), in FIFO order, and keeps waiting
# while the user has execution paused; resuming kicks it again.


def _is_task_alive() -> bool:
    return agent_task is not None and not agent_task.done()


def _is_executing() -> bool:
    """True while a workflow is genuinely running.

    Generated workflows report `__workflow__` success and then hold the browser
    session open for inspection — `_logically_completed` marks that state so a
    queued run may take the slot over.
    """
    return _is_task_alive() and not _logically_completed


def _wake_dispatcher() -> None:
    if _queue_wakeup is not None:
        _queue_wakeup.set()


def _wake_dispatcher_later(delay: float = 2.0) -> None:
    """Retry the queue shortly, without polling while a run is healthy."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.call_later(delay, _wake_dispatcher)


def _on_workflow_report(status: str) -> None:
    """Listener for terminal reports on the synthetic `__workflow__` node."""
    global _logically_completed
    if status == "success" and _is_task_alive():
        _logically_completed = True
        if _current_run_id:
            _completed_run_ids.add(_current_run_id)
            # The workflow has done all of its work; the remaining task body is
            # only the 'hold screen open' loop. Record the win right away so
            # /runs reflects reality, then let a queued run take over.
            _update_run(_current_run_id, status="success", finished_at=time.time())
        _wake_dispatcher()


def _insert_run(
    run_id: str,
    workflow_id: str,
    workflow_name: str,
    log_path: str,
    trigger: str,
    status: str,
    queued_at: float | None = None,
) -> None:
    now = time.time()
    with _db() as con:
        con.execute(
            "INSERT INTO runs (id, workflow_id, workflow_name, started_at, "
            "finished_at, status, log_file, trigger, queued_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (run_id, workflow_id, workflow_name, now, None, status, log_path,
             trigger, queued_at),
        )


def _update_run(
    run_id: str,
    status: str | None = None,
    started_at: float | None = None,
    finished_at: float | None = None,
) -> None:
    sets, args = [], []
    if status is not None:
        sets.append("status=?")
        args.append(status)
    if started_at is not None:
        sets.append("started_at=?")
        args.append(started_at)
    if finished_at is not None:
        sets.append("finished_at=?")
        args.append(finished_at)
    if not sets:
        return
    args.append(run_id)
    with _db() as con:
        con.execute(f"UPDATE runs SET {', '.join(sets)} WHERE id=?", args)


def _load_workflow_row(workflow_id: str):
    with _db() as con:
        return con.execute(
            "SELECT id, name, graph FROM workflows WHERE id=?", (workflow_id,)
        ).fetchone()


def _run_exists(run_id: str) -> bool:
    with _db() as con:
        return (
            con.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone()
            is not None
        )


def _enqueue_locked(
    workflow_id: str, workflow_name: str, inputs: dict, trigger: str
) -> dict:
    """Record a queued run. Caller must hold `_start_lock`."""
    now = time.time()
    superseded = _pending_by_workflow.get(workflow_id)
    if superseded is not None:
        try:
            _pending_queue.remove(superseded)
        except ValueError:
            pass
        _pending_by_workflow.pop(workflow_id, None)
        # Keep the superseded firing visible in history instead of losing it.
        _update_run(superseded["run_id"], status="cancelled", finished_at=now)

    run_id = str(uuid.uuid4())
    item = {
        "run_id": run_id,
        "workflow_id": workflow_id,
        "workflow_name": workflow_name,
        "inputs": inputs or {},
        "trigger": trigger,
        "queued_at": now,
        "log_path": str(RUNS_DIR / f"{run_id}.log"),
    }
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    _insert_run(
        run_id, workflow_id, workflow_name, item["log_path"], trigger,
        status="queued", queued_at=now,
    )
    _pending_queue.append(item)
    _pending_by_workflow[workflow_id] = item
    # Nudge the dispatcher in case the slot is actually free (e.g. the previous
    # task swallowed cancellation). It is a no-op while paused or while busy.
    _wake_dispatcher()
    return {
        "status": "queued",
        "run_id": run_id,
        "workflow_id": workflow_id,
        "queue_position": len(_pending_queue),
        "pending": len(_pending_queue),
    }


def _cancel_pending_locked() -> int:
    """Cancel every queued firing (used by /stop). Caller holds `_start_lock`."""
    now = time.time()
    count = len(_pending_queue)
    for item in _pending_queue:
        _update_run(item["run_id"], status="cancelled", finished_at=now)
    _pending_queue.clear()
    _pending_by_workflow.clear()
    return count


async def _release_holding_task_locked() -> bool:
    """Cancel the 'hold the screen open' tail of a finished run.

    Returns True when the execution slot is free. Caller holds `_start_lock`.
    """
    global agent_task
    if agent_task is None or agent_task.done():
        agent_task = None
        return True
    old = agent_task
    old.cancel()
    try:
        await asyncio.wait_for(asyncio.shield(old), timeout=5.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
    if not old.done():
        return False  # it swallowed cancellation — do not start a second run
    agent_task = None
    return True


async def _launch_run_locked(
    workflow_id: str,
    inputs: dict | None,
    trigger: str,
    run_id: str | None = None,
) -> dict:
    """Compile and start a workflow run. Caller holds `_start_lock`.

    Pass `run_id` to promote an existing `queued` row to `running`.
    """
    global agent_task, _current_run_id, _current_run_workflow_id
    global _current_workflow_name, _current_trigger, _logically_completed

    row = _load_workflow_row(workflow_id)
    if not row:
        if run_id:
            _update_run(run_id, status="error", finished_at=time.time())
        return {"status": "error", "message": "Workflow not found."}

    wid, workflow_name = row["id"], row["name"]
    run_id = run_id or str(uuid.uuid4())
    log_path = str(RUNS_DIR / f"{run_id}.log")
    RUNS_DIR.mkdir(parents=True, exist_ok=True)

    try:
        graph_data = json.loads(row["graph"])
        code = generate(graph_data, log_file_path=log_path, inputs=inputs or {})
        WORKFLOW_PY.write_text(code)
    except Exception as e:
        # Ensure the firing still leaves a trace in run history.
        if _run_exists(run_id):
            _update_run(run_id, status="error", finished_at=time.time())
        else:
            _insert_run(run_id, wid, workflow_name, log_path, trigger, status="error")
            _update_run(run_id, finished_at=time.time())
        return {"status": "error", "run_id": run_id,
                "message": f"Code generation failed: {e}"}

    if _run_exists(run_id):
        _update_run(run_id, status="running", started_at=time.time())
    else:
        _insert_run(run_id, wid, workflow_name, log_path, trigger, status="running")

    _current_run_id = run_id
    _current_run_workflow_id = wid
    _current_workflow_name = workflow_name
    _current_trigger = trigger
    _logically_completed = False

    spec = importlib.util.spec_from_file_location("workflow", WORKFLOW_PY)
    _wf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(_wf)

    _state.reset_execution()
    pause_event.set()

    async def _run():
        final_status = "success"
        try:
            await _wf.main(pause_event=pause_event)
        except asyncio.CancelledError:
            # Cancelling a run whose workflow already reported success only
            # retires the trailing 'hold the screen open' loop — the work was
            # done, so keep the success record.
            final_status = "success" if run_id in _completed_run_ids else "stopped"
            if final_status == "stopped":
                _state.report_node("__workflow__", "stopped")
            raise
        except Exception:
            import traceback as _tb
            final_status = "error"
            _state.report_node("__workflow__", "error")
            _tb.print_exc()  # goes to tee'd stderr → log file
            raise
        finally:
            if run_id in _completed_run_ids:
                # Already recorded with its real finish time when the workflow
                # reported success; don't stretch it with the hold-loop time.
                _update_run(run_id, status=final_status)
            else:
                _update_run(run_id, status=final_status, finished_at=time.time())
            _completed_run_ids.discard(run_id)
            _wake_dispatcher()

    def _on_agent_task_done(task: asyncio.Task) -> None:
        # The run loop already logged/recorded the failure; retrieving the
        # exception here just keeps asyncio from warning about it.
        if not task.cancelled():
            task.exception()
        _wake_dispatcher()

    agent_task = asyncio.create_task(_run())
    # The run's `finally` fires while the task is still not `done()`, so wake the
    # dispatcher once more when it truly finishes and the slot is really free.
    agent_task.add_done_callback(_on_agent_task_done)
    return {
        "status": "started",
        "run_id": run_id,
        "workflow_id": wid,
        "trigger": trigger,
        "pending": len(_pending_queue),
    }


async def _trigger_workflow(
    workflow_id: str,
    inputs: dict | None = None,
    trigger: str = "manual",
    immediate: bool = True,
) -> dict:
    """Single entry point for every workflow trigger.

    immediate=True  → /start and /webhook keep their original semantics: launch
                      right away unless a workflow is already running.
    immediate=False → cron: in addition to that, a paused system queues the
                      firing rather than silently swallowing it.
    """
    async with _start_lock:
        row = _load_workflow_row(workflow_id)
        if not row:
            return {"status": "error", "message": "Workflow not found."}

        paused = not pause_event.is_set()
        if _is_executing() or (not immediate and paused):
            return _enqueue_locked(row["id"], row["name"], inputs or {}, trigger)

        # The slot is free (or only the post-success 'hold screen' loop is).
        if not await _release_holding_task_locked():
            # Could not retire the old task — queue rather than overlap runs.
            return _enqueue_locked(row["id"], row["name"], inputs or {}, trigger)

        return await _launch_run_locked(row["id"], inputs or {}, trigger)


async def _start_next_queued_locked() -> bool:
    """Start the oldest queued run if the slot is free. Caller holds `_start_lock`.

    Returns True when a run was started (so the dispatcher should loop again).
    """
    if not _pending_queue:
        return False
    if not pause_event.is_set():
        return False  # paused — wait for /resume, queue stays intact
    if _is_executing():
        return False
    if not await _release_holding_task_locked():
        return False

    item = _pending_queue.pop(0)
    _pending_by_workflow.pop(item["workflow_id"], None)
    await _launch_run_locked(
        item["workflow_id"], item["inputs"], item["trigger"], run_id=item["run_id"]
    )
    return True


async def _queue_dispatcher() -> None:
    """Drain queued triggers whenever the execution slot frees up."""
    while _queue_wakeup is None:
        await asyncio.sleep(0.05)  # lifespan creates it; be defensive anyway
    while True:
        try:
            await _queue_wakeup.wait()
            _queue_wakeup.clear()
            while True:
                async with _start_lock:
                    started = await _start_next_queued_locked()
                if started:
                    continue
                if _pending_queue and pause_event.is_set():
                    # Still queued and not paused: the slot is momentarily busy
                    # (a run is starting or a previous task is shutting down).
                    # Retry shortly instead of leaving the queue stranded.
                    _wake_dispatcher_later()
                break
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never let a bad queue entry kill the dispatcher.
            await asyncio.sleep(1)


@app.post("/start")
async def start(id: str | None = None, payload: dict | None = Body(default=None)):
    inputs = (payload or {}).get("inputs", {})

    if id:
        return await _trigger_workflow(id, inputs, trigger="manual", immediate=True)

    # Default to most recently updated workflow
    with _db() as con:
        row = con.execute(
            "SELECT id FROM workflows ORDER BY updated DESC LIMIT 1"
        ).fetchone()
    if not row:
        return {"status": "error", "message": "No workflow found. Create one first."}
    return await _trigger_workflow(
        row["id"], inputs, trigger="manual", immediate=True
    )


@app.post("/webhook/{workflow_id}")
async def webhook_trigger(
    workflow_id: str,
    payload: dict | None = Body(default=None),
    x_orbit_secret: str | None = Header(default=None),
):
    """Trigger a workflow run via HTTP. Request body is passed as workflow inputs."""
    # Validate webhook secret if configured on the workflow
    with _db() as con:
        row = con.execute(
            "SELECT graph FROM workflows WHERE id=?", (workflow_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Workflow not found")

    graph_data = json.loads(row["graph"])
    expected_secret = graph_data.get("global", {}).get("webhook_secret", "")
    if expected_secret and x_orbit_secret != expected_secret:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    inputs = payload or {}
    return await _trigger_workflow(
        workflow_id, inputs, trigger="webhook", immediate=True
    )


# ── Cron scheduler ────────────────────────────────────────────────────────────

async def _scheduler_tick(tick: datetime.datetime) -> list[dict]:
    """Run one scheduling pass for `tick`; returns what fired. Never raises."""
    try:
        from croniter import croniter
    except ImportError:
        return []

    fired: list[dict] = []
    try:
        with _db() as con:
            rows = con.execute("SELECT id, graph FROM workflows").fetchall()
    except Exception:
        return fired

    for row in rows:
        try:
            graph_data = json.loads(row["graph"])
        except Exception:
            continue  # corrupt graph — skip this workflow only
        triggers = graph_data.get("global", {}).get("triggers", [])
        for t in triggers:
            if not t.get("enabled", True):
                continue
            if t.get("type") != "cron":
                continue
            cron_expr = str(t.get("cron", "")).strip()
            if not cron_expr:
                continue
            try:
                matched = croniter.match(cron_expr, tick)
            except Exception:
                continue  # malformed cron expression — skip, keep other workflows
            if not matched:
                continue
            try:
                result = await _trigger_workflow(
                    row["id"], t.get("inputs", {}), trigger="cron", immediate=False
                )
                fired.append({"workflow_id": row["id"], "cron": cron_expr, **result})
            except Exception as e:
                fired.append(
                    {"workflow_id": row["id"], "cron": cron_expr,
                     "status": "error", "message": str(e)}
                )
    return fired


async def _scheduler_loop():
    """Check every minute (aligned to clock boundaries) for cron triggers."""
    try:
        from croniter import croniter  # noqa: F401
    except ImportError:
        import logging
        logging.getLogger("orbit").warning(
            "croniter not installed — cron scheduling disabled. Rebuild the container."
        )
        return

    while True:
        # Sleep until the top of the next minute so matches are always exact
        now = datetime.datetime.now()
        seconds_until_next_minute = 60 - now.second - now.microsecond / 1_000_000
        await asyncio.sleep(seconds_until_next_minute)

        try:
            tick = datetime.datetime.now().replace(second=0, microsecond=0)
            await _scheduler_tick(tick)
        except Exception:
            pass  # never crash the scheduler loop


@app.get("/debug/cron")
async def debug_cron():
    """Diagnose cron scheduling — shows what the scheduler sees right now."""
    result = {"croniter_installed": False, "now": None, "workflows": []}
    try:
        from croniter import croniter
        result["croniter_installed"] = True
    except ImportError:
        return result

    now = datetime.datetime.now().replace(second=0, microsecond=0)
    result["now"] = now.isoformat()

    with _db() as con:
        rows = con.execute("SELECT id, name, graph FROM workflows").fetchall()

    for row in rows:
        graph_data = json.loads(row["graph"])
        triggers = graph_data.get("global", {}).get("triggers", [])
        wf_entry = {"id": row["id"], "name": row["name"], "triggers": []}
        for t in triggers:
            match = None
            error = None
            try:
                match = croniter.match(t.get("cron", ""), now)
            except Exception as e:
                error = str(e)
            wf_entry["triggers"].append({
                "type": t.get("type"),
                "cron": t.get("cron"),
                "enabled": t.get("enabled"),
                "would_match_now": match,
                "error": error,
            })
        result["workflows"].append(wf_entry)

    return result


@app.get("/runs")
async def list_runs(workflow_id: str):
    with _db() as con:
        rows = con.execute(
            "SELECT id, workflow_name, started_at, finished_at, status, "
            "trigger, queued_at FROM runs "
            "WHERE workflow_id=? ORDER BY started_at DESC LIMIT 20",
            (workflow_id,),
        ).fetchall()
    return {"runs": [dict(r) for r in rows]}


@app.get("/runs/{run_id}/log")
async def get_run_log(run_id: str):
    with _db() as con:
        row = con.execute("SELECT log_file FROM runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    log_path = Path(row["log_file"])
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="Log file not found")

    def _iter():
        with open(log_path, encoding="utf-8") as f:
            yield from f

    return StreamingResponse(_iter(), media_type="text/plain")


@app.post("/interrupt")
async def interrupt():
    pause_event.clear()
    return {"status": "paused", "message": "Pause requested — will pause after current step completes."}


@app.post("/resume")
async def resume():
    pause_event.set()
    # Queued triggers that arrived while paused may start now.
    _wake_dispatcher()
    return {"status": "resumed", "pending": len(_pending_queue)}


@app.post("/stop")
async def stop():
    global agent_task, _logically_completed
    async with _start_lock:
        stopped = False
        if agent_task and not agent_task.done():
            agent_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(agent_task), timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            # If task is still alive after 5 s, it swallowed CancelledError — force-clear it
            if not agent_task.done():
                agent_task.cancel()
                agent_task = None
            stopped = True
            _logically_completed = False
            _state.report_node("__workflow__", "stopped")
        # Stop also abandons everything waiting to run — the user asked for a halt,
        # not for the queue to immediately relaunch.
        cancelled = _cancel_pending_locked()
    if stopped:
        return {"status": "stopped", "cancelled_pending": cancelled}
    if cancelled:
        return {"status": "not_running", "cancelled_pending": cancelled}
    return {"status": "not_running"}


@app.get("/status")
async def status():
    running = _is_task_alive()
    executing = _is_executing()
    paused = not pause_event.is_set()
    pending = [
        {
            "run_id": item["run_id"],
            "workflow_id": item["workflow_id"],
            "workflow_name": item["workflow_name"],
            "trigger": item["trigger"],
            "queued_at": item["queued_at"],
        }
        for item in _pending_queue
    ]
    return {
        # Original fields — `running` stays true while the browser session is
        # held open after a successful run, so the UI behaves as before.
        "running": running,
        "paused": paused,
        "state": "paused" if paused else ("running" if running else "idle"),
        # Queue-aware additions.
        "executing": executing,  # a workflow is doing actual work right now
        "pending": len(pending),  # triggers waiting for the execution slot
        "pending_runs": pending,
        "current_run_id": _current_run_id if running else None,
        "current_workflow_id": _current_run_workflow_id if running else None,
        "current_trigger": _current_trigger if running else None,
    }


# ── File manager endpoints ────────────────────────────────────────────────────


def _safe_path(rel: str) -> Path:
    """Resolve rel inside FILES_ROOT; raise 400 if it would escape."""
    p = (FILES_ROOT / rel).resolve()
    if not str(p).startswith(str(FILES_ROOT.resolve())):
        raise HTTPException(status_code=400, detail="Invalid path")
    return p


@app.get("/files")
async def list_files(path: str = ""):
    dir_path = _safe_path(path)
    if not dir_path.exists() or not dir_path.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")
    entries = []
    for item in sorted(
        dir_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())
    ):
        entries.append(
            {
                "name": item.name,
                "path": str(item.relative_to(FILES_ROOT)).replace("\\", "/"),
                "is_dir": item.is_dir(),
                "size": item.stat().st_size if item.is_file() else None,
                "modified": item.stat().st_mtime,
            }
        )
    return {"path": path, "entries": entries}


@app.post("/files/upload")
async def upload_file(path: str = "", file: UploadFile = File(...)):
    dir_path = _safe_path(path)
    dir_path.mkdir(parents=True, exist_ok=True)
    dest = _safe_path(str(Path(path) / file.filename))
    with open(dest, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            f.write(chunk)
    return {
        "status": "uploaded",
        "path": str(dest.relative_to(FILES_ROOT)).replace("\\", "/"),
    }


@app.get("/files/download")
async def download_file(path: str):
    file_path = _safe_path(path)
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    mime, _ = mimetypes.guess_type(str(file_path))
    return StreamingResponse(
        open(file_path, "rb"),
        media_type=mime or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{file_path.name}"'},
    )


@app.delete("/files")
async def delete_file(path: str):
    target = _safe_path(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")
    if target.is_dir():
        target.rmdir()  # only empty dirs — prevents accidental data loss
    else:
        target.unlink()
    return {"status": "deleted"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend:app", host="0.0.0.0", port=8000, reload=False)
