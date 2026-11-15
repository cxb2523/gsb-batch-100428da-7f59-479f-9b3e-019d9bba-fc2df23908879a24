"""
Batch-outcome contract for the web UI, without touching the network.

The batch flow is the part that reports "which book did what", so it is exercised
against a stubbed `jmcore.run_download` (and a stubbed id parser) instead of the real
site: the test then asserts exact per-book verdicts and the terminal `done` event.

Covered:
  * one failing book does not stop the books after it
  * an unparseable id becomes a failed row rather than a silently dropped token
  * cancel while a book is downloading: that book is "cancelled", the rest "pending",
    and neither counts as success or failure
  * all-success vs partial-failure vs cancelled produce distinct wording
  * a second submit while a task is active still returns 409
  * token and Host checks are unchanged
  * an all-failed batch says 全部失败 rather than 部分失败
"""
import json
import queue
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "webui"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import jmcore  # noqa: E402
import server  # noqa: E402

FAILURES = []


def check(name, condition, detail=""):
    print(f"{'PASS' if condition else 'FAIL'}  {name}{'' if condition else '  ' + detail}")
    if not condition:
        FAILURES.append(name)


# --------------------------------------------------------------------------- #
# stubs
# --------------------------------------------------------------------------- #

class FakeJmcomicText:
    @staticmethod
    def parse_to_jm_id(value):
        text = str(value).strip().upper()
        if text.startswith("JM"):
            text = text[2:]
        if not text.isdigit():
            raise ValueError(f"not a jm id: {value}")
        return text


class FakeJmcomic:
    JmcomicText = FakeJmcomicText


class Entered(threading.Event):
    pass


def fake_run_download(settings, jmcomic=None, progress_sink=None):
    jm_id = str(settings.targets[0])
    if progress_sink:
        progress_sink(f"stub download {jm_id}")
    if jm_id == "222":
        raise jmcore.OperationError("网络错误 222", hint="稍后重试")
    if jm_id == "444":
        # Blocks long enough for the test to press cancel mid-flight.
        time.sleep(1.5)
        return {"id": jm_id, "title": "slow", "imageCount": 9,
                "exportFiles": [], "savePath": f"/tmp/{jm_id}"}
    if jm_id == "555":
        raise RuntimeError("unexpected boom")
    return {"id": jm_id, "title": f"title-{jm_id}", "imageCount": 3,
            "exportFiles": [f"/tmp/JM{jm_id}.pdf"], "savePath": f"/tmp/{jm_id}"}


server.jmcore.load_jmcomic = lambda: FakeJmcomic()
server.jmcore.run_download = fake_run_download


# --------------------------------------------------------------------------- #
# server harness
# --------------------------------------------------------------------------- #

def start_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    server.Handler.token = "test-token"
    server.Handler.allowed_hosts = {"127.0.0.1", "localhost", "[::1]"}
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def post_json(url, payload, token="test-token"):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url + f"?token={token}", data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def get(url, token="test-token"):
    try:
        with urllib.request.urlopen(url + f"?token={token}", timeout=10) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def subscribe(base, token="test-token"):
    events = []
    ready = threading.Event()

    def listen():
        try:
            with urllib.request.urlopen(f"{base}/api/events?token={token}",
                                        timeout=60) as r:
                ready.set()
                for raw in r:
                    line = raw.decode("utf-8").strip()
                    if line.startswith("data: "):
                        events.append(json.loads(line[6:]))
        except Exception:
            pass

    threading.Thread(target=listen, daemon=True).start()
    ready.wait(5)
    time.sleep(0.2)
    return events


def wait_done(events, timeout=30):
    """Wait for a done event that is newer than anything already in `events`."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for event in events:
            if event.get("type") == "done":
                return event
        time.sleep(0.05)
    raise AssertionError("no done event")


def wait_for(predicate, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def reset_job():
    server.JOB.cancel.clear()
    server.JOB.cancel_at = None
    server.JOB.thread = None
    server.JOB.release()


def statuses(done):
    return {item["id"]: item["status"] for item in done.get("items", [])}


def run_batch(base, body, _events=None, token="test-token"):
    """Submit a batch, then subscribe.

    Subscribing *after* the POST is deliberate: the hub replays this batch's plan,
    item and done events to a late subscriber, so the test cannot accidentally latch
    onto a previous batch's terminal event still sitting in its own event list.
    """
    status, payload = post_json(f"{base}/api/download", body, token=token)
    assert status == 200, (status, payload)
    events = subscribe(base, token=token)
    done = wait_done(events)
    return done, events


# --------------------------------------------------------------------------- #
# cases
# --------------------------------------------------------------------------- #

def main():
    httpd, base = start_server()
    try:
        # security contract unchanged
        status, _ = get(f"{base}/api/config", token="wrong")
        check("bad token rejected", status == 403, f"got {status}")

        req = urllib.request.Request(f"{base}/api/config?token=test-token",
                                     headers={"Host": "evil.example"})
        try:
            urllib.request.urlopen(req, timeout=10)
            check("bad Host rejected", False, "accepted")
        except urllib.error.HTTPError as e:
            check("bad Host rejected", e.code == 403, f"got {e.code}")

        # 1. partial failure: success, bad id, failure, success, crash
        reset_job()
        done, events = run_batch(base, {"ids": "111 bad-id 222 333 555", "kind": "photo",
                                        "exports": ["pdf"], "saveDir": "/tmp/stub",
                                        "threads": 2, "backend": "requests"})
        got = statuses(done)
        check("partial: 111 success", got.get("111") == "success", str(got))
        check("partial: 333 success", got.get("333") == "success", str(got))
        check("partial: bad id failed", got.get("bad-id") == "failed", str(got))
        check("partial: bad id marked unresolved",
              next(i for i in done["items"] if i["input"] == "bad-id")["resolved"] is False,
              str(done["items"]))
        check("partial: 222 failed", got.get("222") == "failed", str(got))
        check("partial: unexpected exception still a row",
              got.get("555") == "failed", str(got))
        check("partial: outcome", done.get("outcome") == "partial", str(done.get("outcome")))
        check("partial: wording", "部分失败" in done.get("text", ""), done.get("text", ""))
        check("partial: summary",
              done.get("summary") == {"total": 5, "success": 2, "failed": 3,
                                      "cancelled": 0, "pending": 0, "running": 0},
              str(done.get("summary")))
        success_row = next(i for i in done["items"] if i["id"] == "111")
        check("success row has imageCount and exports",
              success_row["imageCount"] == 3
              and success_row["exportFiles"] == ["/tmp/JM111.pdf"], str(success_row))
        fail_row = next(i for i in done["items"] if i["id"] == "222")
        check("failure row carries the error",
              "网络错误 222" in (fail_row["error"] or ""), str(fail_row))
        check("failure row has no image count", fail_row["imageCount"] is None,
              str(fail_row))
        check("per-row item events were streamed",
              any(e.get("type") == "item" and e["item"]["id"] == "111" for e in events))

        # 2. all success wording
        reset_job()
        done, events = run_batch(base, {"ids": "111 333", "kind": "photo", "exports": [],
                                        "saveDir": "/tmp/stub", "threads": 2})
        check("all-success: outcome", done.get("outcome") == "success", str(done))
        check("all-success: wording", done.get("text") == "全部完成", done.get("text"))
        check("all-success: summary",
              done["summary"]["success"] == 2 and done["summary"]["failed"] == 0,
              str(done["summary"]))

        # 2b. nothing succeeded -> "全部失败", not a misleading "部分失败"
        reset_job()
        done, _ = run_batch(base, {"ids": "222 bad-id", "kind": "photo", "exports": [],
                                   "saveDir": "/tmp/stub", "threads": 2})
        check("all-failed: outcome", done.get("outcome") == "failed", str(done.get("outcome")))
        check("all-failed: wording", "全部失败" in done.get("text", ""), done.get("text", ""))

        # 3. duplicate submit while a task runs -> 409
        reset_job()
        status, _ = post_json(f"{base}/api/download",
                              {"ids": "444", "kind": "photo", "exports": [],
                               "saveDir": "/tmp/stub", "threads": 2})
        check("first submit accepted", status == 200, f"got {status}")
        events = subscribe(base)
        time.sleep(0.2)
        status, payload = post_json(f"{base}/api/download",
                                    {"ids": "111", "kind": "photo", "exports": [],
                                     "saveDir": "/tmp/stub", "threads": 2})
        check("second submit returns 409", status == 409, f"got {status} {payload}")
        # cancel while 444 downloads
        status, _ = post_json(f"{base}/api/cancel", {})
        check("cancel accepted", status == 200, f"got {status}")
        done = wait_done(events)
        got = statuses(done)
        check("cancel: in-flight book is cancelled", got.get("444") == "cancelled", str(got))
        check("cancel: outcome", done.get("outcome") == "cancelled", str(done.get("outcome")))
        check("cancel: wording", "已取消" in done.get("text", ""), done.get("text", ""))
        check("cancel: summary excludes cancelled from success/failure",
              done["summary"] == {"total": 1, "success": 0, "failed": 0,
                                  "cancelled": 1, "pending": 0, "running": 0},
              str(done["summary"]))
        row = done["items"][0]
        check("cancel: cancelled row keeps no success data",
              row["imageCount"] is None and not row["exportFiles"], str(row))

        # 4. cancel between books: the untouched one is 未开始 (pending)
        reset_job()
        status, _ = post_json(f"{base}/api/download",
                              {"ids": "444 111", "kind": "photo", "exports": [],
                               "saveDir": "/tmp/stub", "threads": 2})
        check("cancel-between: accepted", status == 200, f"got {status}")
        events = subscribe(base)
        time.sleep(0.2)
        post_json(f"{base}/api/cancel", {})
        done = wait_done(events)
        got = statuses(done)
        check("cancel-between: first cancelled", got.get("444") == "cancelled", str(got))
        check("cancel-between: second pending", got.get("111") == "pending", str(got))
        check("cancel-between: summary pending",
              done["summary"]["pending"] == 1 and done["summary"]["cancelled"] == 1,
              str(done["summary"]))
        pending_row = next(i for i in done["items"] if i["id"] == "111")
        check("cancel-between: pending row labelled 未开始",
              pending_row["label"] == "未开始", str(pending_row))

        # 5. replay buffer lets a late subscriber rebuild the table
        late = subscribe(base)
        time.sleep(0.3)
        check("late subscriber receives plan/rows/done",
              any(e.get("type") == "done" for e in late)
              and any(e.get("type") == "plan" for e in late), str([e.get("type") for e in late]))

        # 6. the slot is released after a batch: the next submit is accepted again
        reset_job()
        done, _ = run_batch(base, {"ids": "111", "kind": "photo", "exports": [],
                                   "saveDir": "/tmp/stub", "threads": 2})
        check("slot released after batch", done.get("outcome") == "success", str(done))
        reset_job()

        # 6b. the page knows every outcome label and both status states it must show
        page = server.INDEX_HTML
        for label in ("全部成功", "部分失败", "全部失败", "已取消", "未开始", "成功", "失败", "取消"):
            check(f"page contains {label}", label in page)
        check("page renders a result table", 'id="resBody"' in page and 'id="resTable"' in page)

        # 7. a rejected submit does not leak the job slot
        status, _ = post_json(f"{base}/api/download",
                              {"ids": "not-an-id", "kind": "photo", "exports": [],
                               "saveDir": "/tmp/stub", "threads": 2})
        check("unusable batch rejected with 400", status == 400, f"got {status}")
        check("rejected batch released the slot", not server.JOB.running,
              "slot leaked")
        status, _ = post_json(f"{base}/api/download",
                              {"ids": "111", "kind": "photo", "exports": [],
                               "saveDir": "/tmp/stub", "threads": 2})
        check("submit after rejection accepted", status == 200, f"got {status}")
        wait_for(lambda: not server.JOB.running, timeout=20)

        # 8. simultaneous submits: exactly one wins, the rest get 409
        results = []
        gate = threading.Barrier(6)

        def submit():
            gate.wait()
            status, _ = post_json(f"{base}/api/download",
                                  {"ids": "444", "kind": "photo", "exports": [],
                                   "saveDir": "/tmp/stub", "threads": 2})
            results.append(status)

        threads = [threading.Thread(target=submit) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(20)
        post_json(f"{base}/api/cancel", {})
        check("concurrent submits: exactly one accepted",
              results.count(200) == 1 and results.count(409) == 5, str(results))
        wait_for(lambda: not server.JOB.running, timeout=20)
    finally:
        httpd.shutdown()
        httpd.server_close()

    print()
    if FAILURES:
        print("BATCH OUTCOME TEST: FAIL ->", ", ".join(FAILURES))
        return 1
    print("BATCH OUTCOME TEST: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
