"""Trigger-queue tests for backend.py. No browser is used.

Builds real workflow graphs (Code nodes only), compiles them through codegen.py
and drives the FastAPI app directly via TestClient, exactly the way /start,
/webhook and the cron scheduler do.

Run from the repo root:
    python backend/_queue_tests.py

A stub `orbit` module is provided on sys.path so the generated workflow.py
(which does `from orbit import session`) can import outside the container.
"""
import asyncio
import os
import sys
import tempfile
import textwrap
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

# The app resolves /workspace from ORBIT_WORKSPACE. Point it at a scratch dir
# before importing backend so the DB, runs/ and workflow.py stay out of the repo.
_WORKSPACE = tempfile.mkdtemp(prefix="orbit-queue-test-")
os.environ["ORBIT_WORKSPACE"] = _WORKSPACE
os.environ.pop("DBUS_SESSION_BUS_ADDRESS", None)

# ── Stub `orbit` so generated code imports without the real container ─────────
_STUB_DIR = Path(tempfile.mkdtemp(prefix="orbit-stub-"))
(_STUB_DIR / "orbit.py").write_text(
    textwrap.dedent(
        '''
        import contextlib


        @contextlib.asynccontextmanager
        async def session(*args, **kwargs):
            yield object()
        '''
    ),
    encoding="utf-8",
)
sys.path.insert(0, str(_STUB_DIR))

from fastapi.testclient import TestClient  # noqa: E402

import backend as app_module  # noqa: E402

client = TestClient(app_module.app)

FAILURES: list[str] = []


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"  PASS  {msg}")
    else:
        print(f"  FAIL  {msg}")
        FAILURES.append(msg)


def make_graph(code: str, name: str = "wf") -> dict:
    return {
        "version": "1",
        "global": {"llm": "gpt-4", "verbose": False},
        "nodes": [
            {"id": "n1", "type": "Code", "label": name, "config": {"code": code}}
        ],
        "edges": [],
    }


def make_workflow(name: str, code: str, triggers: list | None = None) -> str:
    wid = client.post("/workflows", json={"name": name}).json()["id"]
    graph = make_graph(code, name)
    if triggers is not None:
        graph["global"]["triggers"] = triggers
    r = client.post("/workflow/save", json={"id": wid, "graph": graph})
    assert r.status_code == 200, r.text
    return wid


def get_status() -> dict:
    return client.get("/status").json()


def get_runs(wid: str) -> list[dict]:
    return client.get(f"/runs?workflow_id={wid}").json()["runs"]


def wait_for(pred, timeout: float = 10.0, interval: float = 0.05) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return pred()


def wait_idle(timeout: float = 10.0) -> bool:
    """Wait until nothing is queued and no workflow is doing work.

    A successful generated workflow then holds the browser session open, so
    `running` stays true by design — `executing` is the real-work signal.
    """

    def _idle():
        s = get_status()
        return s["pending"] == 0 and not s["executing"]

    return wait_for(_idle, timeout)


def statuses(wid: str) -> list[str]:
    return [r["status"] for r in get_runs(wid)]


def run_tick() -> list[dict]:
    """Run one scheduler pass on the app's own event loop."""
    return client.portal.call(
        app_module._scheduler_tick, app_module.datetime.datetime.now()
    )


def _reset_state() -> None:
    app_module._pending_queue.clear()
    app_module._pending_by_workflow.clear()
    app_module._logically_completed = False
    app_module.pause_event.set()


def reset_runtime() -> None:
    """Clear in-memory queue/holding state between scenarios."""
    client.post("/stop")
    client.portal.call(_reset_state)
    wait_idle(5)


# ── Scenarios ────────────────────────────────────────────────────────────────


def test_trigger_while_running_is_queued_and_runs_after() -> None:
    print("\nTEST 1: trigger while running is queued, then runs automatically")
    reset_runtime()
    long_wid = make_workflow(
        "long", "import asyncio\nawait asyncio.sleep(0.8)\nprint('long done')"
    )
    short_wid = make_workflow("short", "print('short done')")

    r1 = client.post(f"/start?id={long_wid}", json={"inputs": {}}).json()
    check(r1["status"] == "started", f"first /start started (got {r1['status']})")

    r2 = client.post(f"/webhook/{short_wid}", json={}).json()
    check(r2["status"] == "queued", f"webhook while running queued (got {r2['status']})")
    s = get_status()
    check(s["pending"] == 1, f"/status reports pending=1 (got {s['pending']})")
    check(s["executing"] is True, "current run still executing")
    check(
        any(
            p["workflow_id"] == short_wid and p["trigger"] == "webhook"
            for p in s["pending_runs"]
        ),
        "/status pending_runs names the webhook workflow",
    )
    queued_statuses = statuses(short_wid)
    check(
        "queued" in queued_statuses,
        f"queued firing visible in /runs (got {queued_statuses})",
    )

    ok = wait_idle(15)
    check(ok, "queue drained after the long run finished")
    check("success" in statuses(long_wid), f"long run success: {statuses(long_wid)}")
    check(
        statuses(short_wid).count("success") == 1,
        f"queued run executed once: {statuses(short_wid)}",
    )
    # The queued run must actually be the one that ran (same run id).
    run_ids = {r["id"] for r in get_runs(short_wid)}
    check(r2["run_id"] in run_ids, "queued run id is the one persisted")
    trig = [r["trigger"] for r in get_runs(short_wid)]
    check("webhook" in trig, f"trigger source recorded (got {trig})")


def test_same_workflow_keeps_single_pending() -> None:
    print("\nTEST 2: only one pending entry per workflow; older firing cancelled")
    reset_runtime()
    long_wid = make_workflow("long2", "import asyncio\nawait asyncio.sleep(0.9)")
    other_wid = make_workflow("burst", "import asyncio\nawait asyncio.sleep(0.05)")

    client.post(f"/start?id={long_wid}")
    first = client.post(f"/webhook/{other_wid}", json={"n": 1}).json()
    second = client.post(f"/webhook/{other_wid}", json={"n": 2}).json()
    third = client.post(f"/webhook/{other_wid}", json={"n": 3}).json()

    s = get_status()
    check(s["pending"] == 1, f"still one pending entry (got {s['pending']})")
    check(
        s["pending_runs"][0]["run_id"] == third["run_id"],
        "newest firing supersedes the older queued one",
    )
    cancelled = [r["id"] for r in get_runs(other_wid) if r["status"] == "cancelled"]
    check(
        first["run_id"] in cancelled and second["run_id"] in cancelled,
        f"superseded firings recorded as cancelled (got {cancelled})",
    )

    wait_idle(15)
    final = statuses(other_wid)
    check(
        final.count("success") == 1 and final.count("cancelled") == 2,
        f"exactly one run executed, rest cancelled: {final}",
    )


def test_paused_cron_firings_queue_and_resume_runs_them() -> None:
    print("\nTEST 3: cron firings while paused are queued, then run on resume")
    reset_runtime()
    wid = make_workflow("paused", "print('ran after resume')")

    client.post("/interrupt")
    check(get_status()["paused"] is True, "system paused")

    graph = client.get(f"/workflow/load?id={wid}").json()["graph"]
    graph["global"]["triggers"] = [
        {"type": "cron", "cron": "* * * * *", "enabled": True, "inputs": {"a": 1}}
    ]
    client.post("/workflow/save", json={"id": wid, "graph": graph})

    fired = run_tick()
    matches = [f for f in fired if f["workflow_id"] == wid]
    check(
        len(matches) == 1 and matches[0]["status"] == "queued",
        f"cron firing while paused is queued (got {matches})",
    )
    s = get_status()
    check(s["paused"] is True and s["pending"] == 1, "still paused with 1 pending")

    # Paused queue must not drain on its own.
    time.sleep(0.4)
    check(get_status()["pending"] == 1, "paused queue is not drained")
    check(
        statuses(wid) == ["queued"],
        f"queued run stays queued in /runs while paused: {statuses(wid)}",
    )

    client.post("/resume")
    ok = wait_idle(15)
    check(ok, "queue drained after resume")
    check("success" in statuses(wid), f"queued run ran after resume: {statuses(wid)}")


def test_invalid_cron_does_not_block_others() -> None:
    print("\nTEST 4: invalid cron expression is skipped, other workflows still fire")
    reset_runtime()
    bad_wid = make_workflow("badcron", "print('never')")
    good_wid = make_workflow("goodcron", "print('fired')")

    bad = client.get(f"/workflow/load?id={bad_wid}").json()["graph"]
    bad["global"]["triggers"] = [
        {"type": "cron", "cron": "not a cron", "enabled": True}
    ]
    client.post("/workflow/save", json={"id": bad_wid, "graph": bad})

    good = client.get(f"/workflow/load?id={good_wid}").json()["graph"]
    good["global"]["triggers"] = [
        {"type": "cron", "cron": "* * * * *", "enabled": True},
        {"type": "cron", "cron": "61 99 * * *", "enabled": True},
    ]
    client.post("/workflow/save", json={"id": good_wid, "graph": good})

    fired = run_tick()
    good_fired = [f for f in fired if f["workflow_id"] == good_wid]
    bad_fired = [f for f in fired if f["workflow_id"] == bad_wid]
    check(len(good_fired) >= 1, f"valid workflow fired (got {good_fired})")
    check(bad_fired == [], f"invalid cron produced no firing (got {bad_fired})")
    wait_idle(10)
    check(statuses(bad_wid) == [], f"bad-cron workflow created no runs: {statuses(bad_wid)}")
    check("success" in statuses(good_wid), f"good workflow ran: {statuses(good_wid)}")


def test_run_statuses_error_and_stopped() -> None:
    print("\nTEST 5: /runs distinguishes error and stopped")
    reset_runtime()
    err_wid = make_workflow("boom", "raise RuntimeError('kaboom')")
    client.post(f"/start?id={err_wid}")
    wait_idle(10)
    check("error" in statuses(err_wid), f"failed run recorded as error: {statuses(err_wid)}")

    slow_wid = make_workflow("slow", "import asyncio\nawait asyncio.sleep(30)")
    client.post(f"/start?id={slow_wid}")
    wait_for(lambda: get_status()["executing"], 5)
    time.sleep(0.2)
    r = client.post("/stop").json()
    check(r["status"] == "stopped", f"/stop stopped the run (got {r})")
    wait_for(lambda: "stopped" in statuses(slow_wid), 5)
    check("stopped" in statuses(slow_wid), f"stopped run recorded: {statuses(slow_wid)}")


def test_manual_start_and_webhook_keep_immediate_semantics() -> None:
    print("\nTEST 6: /start and webhook run immediately when idle")
    reset_runtime()
    wid = make_workflow("immediate", "import asyncio\nawait asyncio.sleep(0.1)")
    r = client.post(f"/start?id={wid}").json()
    check(r["status"] == "started", f"/start started immediately (got {r['status']})")
    s = get_status()
    check(s["pending"] == 0 and s["executing"] is True, "no queueing when idle")
    wait_idle(10)

    r = client.post(f"/webhook/{wid}", json={}).json()
    check(r["status"] == "started", f"webhook started immediately (got {r['status']})")
    wait_idle(10)
    check(statuses(wid).count("success") == 2, f"both runs succeeded: {statuses(wid)}")


def test_queue_survives_error_in_running_workflow() -> None:
    print("\nTEST 7: a failing run still releases the slot for queued triggers")
    reset_runtime()
    err_wid = make_workflow(
        "failfirst", "import asyncio\nawait asyncio.sleep(0.4)\nraise RuntimeError('x')"
    )
    nxt_wid = make_workflow("afterfail", "print('after failure')")
    client.post(f"/start?id={err_wid}")
    q = client.post(f"/webhook/{nxt_wid}", json={}).json()
    check(q["status"] == "queued", "webhook queued behind failing run")
    ok = wait_idle(15)
    check(ok, "queue drained despite failure")
    check("error" in statuses(err_wid), f"first run error: {statuses(err_wid)}")
    check("success" in statuses(nxt_wid), f"queued run still ran: {statuses(nxt_wid)}")


def test_trigger_after_success_hold_takes_over() -> None:
    print("\nTEST 8: a trigger after success retires the hold-screen loop")
    reset_runtime()
    wid = make_workflow("quick", "print('quick')")
    client.post(f"/start?id={wid}")
    # Wait until the workflow finished its work and is only holding the screen.
    wait_for(lambda: get_status()["running"] and not get_status()["executing"], 10)

    r = client.post(f"/webhook/{wid}", json={}).json()
    check(
        r["status"] == "started",
        f"new trigger starts instead of being dropped (got {r['status']})",
    )
    wait_idle(10)
    runs = get_runs(wid)
    check(
        statuses(wid).count("success") == 2,
        f"first run stays success after hold-loop cancel: {statuses(wid)}",
    )
    check(
        all(r["status"] == "success" for r in runs),
        f"no run was mislabelled as stopped: {statuses(wid)}",
    )


def test_stop_cancels_pending_queue() -> None:
    print("\nTEST 9: /stop records the running and queued firings correctly")
    reset_runtime()
    long_wid = make_workflow("stopl", "import asyncio\nawait asyncio.sleep(30)")
    queued_wid = make_workflow("stopq", "print('should never run')")
    client.post(f"/start?id={long_wid}")
    wait_for(lambda: get_status()["executing"], 5)
    q = client.post(f"/webhook/{queued_wid}", json={}).json()
    check(q["status"] == "queued", "second workflow is queued")

    r = client.post("/stop").json()
    check(r["status"] == "stopped" and r["cancelled_pending"] == 1, f"/stop result: {r}")
    s = get_status()
    check(s["pending"] == 0 and not s["executing"], "nothing left running or pending")
    check("stopped" in statuses(long_wid), f"running run recorded stopped: {statuses(long_wid)}")
    check(
        statuses(queued_wid) == ["cancelled"],
        f"queued firing recorded cancelled: {statuses(queued_wid)}",
    )
    time.sleep(0.5)
    check(
        statuses(queued_wid) == ["cancelled"],
        "cancelled firing is not launched by the dispatcher",
    )


def run() -> None:
    with client:
        for fn in (
            test_trigger_while_running_is_queued_and_runs_after,
            test_same_workflow_keeps_single_pending,
            test_paused_cron_firings_queue_and_resume_runs_them,
            test_invalid_cron_does_not_block_others,
            test_run_statuses_error_and_stopped,
            test_manual_start_and_webhook_keep_immediate_semantics,
            test_queue_survives_error_in_running_workflow,
            test_trigger_after_success_hold_takes_over,
            test_stop_cancels_pending_queue,
        ):
            fn()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print(f"  - {f}")
        raise SystemExit(1)
    print("ALL QUEUE TESTS PASSED")


if __name__ == "__main__":
    run()
