"""
End-to-end batch-outcome check: the REAL `webui/server.py` subprocess, the REAL
`jmcore.run_download` engine, and a local fake `jmcomic` package (no network).

This is the "local, self-made id sample" check: the batch contains a good id, an
unparseable id, and an id that exists syntactically but cannot be downloaded. It
asserts the per-book table a user sees, both for a partial failure and for a cancel
that arrives mid-download.

The fake package lives in tests/fixtures/fake_jmcomic and is put on PYTHONPATH only
for the child server; the test process itself never imports it.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SERVER = PROJECT_ROOT / "webui" / "server.py"
FAKE_PACKAGE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "fake_jmcomic"

FAILURES = []


def check(name, condition, detail=""):
    print(f"{'PASS' if condition else 'FAIL'}  {name}{'' if condition else '  ' + detail}")
    if not condition:
        FAILURES.append(name)


def start_server():
    env = dict(os.environ)
    # The fake jmcomic must win over a real installation, if any.
    env["PYTHONPATH"] = str(FAKE_PACKAGE_DIR)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        [sys.executable, "-u", str(SERVER), "--no-browser", "--port", "0"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", env=env)
    url = None
    deadline = time.time() + 40
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                raise SystemExit(f"server exited early: {proc.returncode}")
            continue
        match = re.search(r"(http://127\.0\.0\.1:\d+/\?token=\S+)", line)
        if match:
            url = match.group(1)
            break
    if url is None:
        proc.kill()
        raise SystemExit("could not read the server URL")

    def drain():
        try:
            for _ in proc.stdout:
                pass
        except Exception:
            pass

    threading.Thread(target=drain, daemon=True).start()
    return proc, url


def post_json(url, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def listen(base, token, events, timeout=180):
    def run():
        req = urllib.request.Request(f"{base}/api/events?token={token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                for raw in r:
                    line = raw.decode("utf-8").strip()
                    if line.startswith("data: "):
                        events.append(json.loads(line[6:]))
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()


def wait_done(events, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        done = [e for e in events if e.get("type") == "done"]
        if done:
            return done[-1]
        time.sleep(0.1)
    raise AssertionError("no done event")


def wait_job_free(base, token, timeout=30):
    """Poll /api/status until the previous batch has released the single job slot."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with urllib.request.urlopen(f"{base}/api/status?token={token}", timeout=10) as r:
            if not json.loads(r.read().decode("utf-8")).get("running"):
                return True
        time.sleep(0.1)
    return False


def main():
    proc, url = start_server()
    base, token = url.split("/?token=")
    base = base.rstrip("/")
    print("server:", url)
    try:
        tmp = tempfile.mkdtemp(prefix="jm-batch-e2e-")

        # ---- 1. partial failure: good, unparseable, unavailable, good ----
        status, submitted = post_json(
            f"{base}/api/download?token={token}",
            {"ids": "111 abc 999999999 333", "kind": "photo",
             "exports": ["zip", "png"], "saveDir": tmp, "threads": 3,
             "backend": "requests"})
        check("submit accepted", status == 200, f"got {status}")
        check("submit accounts for an unparseable id",
              (submitted.get("summary") or {}).get("failed") == 1,
              str(submitted.get("summary")))

        events = []
        listen(base, token, events)
        done = wait_done(events)
        rows = {row.get("id"): row for row in done.get("items", [])}

        check("outcome is partial", done.get("outcome") == "partial", str(done.get("outcome")))
        check("wording says 部分失败", "部分失败" in done.get("text", ""), done.get("text", ""))
        check("summary counts", done.get("summary") == {
            "total": 4, "success": 2, "failed": 2,
            "cancelled": 0, "pending": 0, "running": 0}, str(done.get("summary")))
        check("good id #1 succeeded", rows.get("111", {}).get("status") == "success",
              str(rows.get("111")))
        check("good id #2 succeeded after failures",
              rows.get("333", {}).get("status") == "success", str(rows.get("333")))
        check("unavailable id failed", rows.get("999999999", {}).get("status") == "failed",
              str(rows.get("999999999")))
        check("unavailable id has an error message",
              bool(rows.get("999999999", {}).get("error")), str(rows.get("999999999")))
        bad_row = next((r for r in done["items"] if r.get("input") == "abc"), None)
        check("unparseable id is a failed row",
              bad_row is not None and bad_row.get("status") == "failed"
              and bool(bad_row.get("error")), str(bad_row))
        for jm_id in ("111", "333"):
            row = rows.get(jm_id, {})
            check(f"{jm_id} reports image count", bool(row.get("imageCount")), str(row))
            check(f"{jm_id} lists export files",
                  len(row.get("exportFiles") or []) >= 1, str(row))

        zips = [p for p in Path(tmp).rglob("*.zip")]
        check("exports landed on disk", bool(zips), str(len(zips)))

        # ---- 2. cancel while a book is downloading ----
        check("job slot released after batch", wait_job_free(base, token))
        cancel_events = []
        status, _ = post_json(
            f"{base}/api/download?token={token}",
            {"ids": "777777777 333", "kind": "photo", "exports": [],
             "saveDir": tmp, "threads": 2})
        check("cancel batch accepted", status == 200, f"got {status}")
        listen(base, token, cancel_events)
        time.sleep(0.5)
        post_json(f"{base}/api/cancel?token={token}", {})
        cdone = wait_done(cancel_events)
        crows = {row.get("id"): row for row in cdone.get("items", [])}

        check("cancel outcome", cdone.get("outcome") == "cancelled", str(cdone.get("outcome")))
        check("cancel wording says 已取消", "已取消" in cdone.get("text", ""),
              cdone.get("text", ""))
        check("in-flight book is cancelled",
              crows.get("777777777", {}).get("status") == "cancelled",
              str(crows.get("777777777")))
        check("in-flight cancel is not a success",
              crows.get("777777777", {}).get("imageCount") in (None, 0)
              and not crows.get("777777777", {}).get("exportFiles"),
              str(crows.get("777777777")))
        check("unstarted book is pending",
              crows.get("333", {}).get("status") == "pending", str(crows.get("333")))
        check("cancelled summary excludes success/failure",
              cdone.get("summary", {}).get("success") == 0
              and cdone.get("summary", {}).get("failed") == 0
              and cdone.get("summary", {}).get("cancelled") == 1
              and cdone.get("summary", {}).get("pending") == 1,
              str(cdone.get("summary")))

        # ---- 3. a fresh batch still works after a cancelled one ----
        check("job slot released after cancel", wait_job_free(base, token))
        fresh_events = []
        status, _ = post_json(
            f"{base}/api/download?token={token}",
            {"ids": "111", "kind": "photo", "exports": [], "saveDir": tmp,
             "threads": 2})
        check("post-cancel submit accepted", status == 200, f"got {status}")
        listen(base, token, fresh_events)
        fdone = wait_done(fresh_events)
        check("post-cancel batch succeeds", fdone.get("outcome") == "success",
              str(fdone.get("outcome")))
        check("post-cancel wording", fdone.get("text") == "全部完成", fdone.get("text", ""))

        post_json(f"{base}/api/shutdown?token={token}", {})
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
    finally:
        if proc.poll() is None:
            proc.kill()

    print()
    if FAILURES:
        print("BATCH E2E TEST: FAIL ->", ", ".join(FAILURES))
        return 1
    print("BATCH E2E TEST: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
