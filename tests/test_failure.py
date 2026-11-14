# Acceptance tests for failed-task handling, resend, and allow_codes fix.
# Run from the repo root:  python tests/test_failure.py
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.biu as biu  # Must be first biu import: applies gevent monkey-patch.

import gevent
from gevent.pywsgi import WSGIServer

# ---------------- local controllable HTTP server ----------------
state = {"flaky_hits": 0, "flaky_fail_times": 2}  # /flaky fails twice, then 200


def app(environ, start_response):
    path = environ["PATH_INFO"]
    if path == "/flaky":
        state["flaky_hits"] += 1
        if state["flaky_hits"] <= state["flaky_fail_times"]:
            start_response("500 Internal Server Error", [("Content-Type", "text/plain")])
            return [b"boom"]
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"flaky-ok"]
    if path == "/always500":
        start_response("500 Internal Server Error", [("Content-Type", "text/plain")])
        return [b"always fails"]
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [b"ok"]


server = WSGIServer(("127.0.0.1", 0), app, log=None)
server.init_socket()  # bind now so the ephemeral port is known
gevent.spawn(server.serve_forever)
BASE = "http://127.0.0.1:%d" % server.socket.getsockname()[1]


# ---------------- test project ----------------
class TestProject(biu.Project):
    def __init__(self, **kw):
        super().__init__(concurrent=2, interval=0, max_retry=1, retry_delay=0, **kw)
        self.errorback_calls = []
        self.results = []
        self.callback_seen = []

    def errorback(self, request, exception, retried):
        self.errorback_calls.append((request, exception, retried))

    def parse(self, resp):
        self.callback_seen.append((resp.url, resp.status_code, dict(resp.save)))
        yield {"url": resp.url, "save": dict(resp.save)}

    def result_handler(self, rv):
        self.results.append(rv)
        return rv


def make_core():
    return biu.BiuCore(TestProject())


def test_retry_exhaustion_records_failure():
    core = make_core()
    proj = core.project
    req = biu.Request(url=BASE + "/flaky", callback=proj.parse, save={"origin": "flaky"})
    core.send_request(req)
    core._pool.join()

    assert len(proj.errorback_calls) == 1, proj.errorback_calls
    eb_req, eb_exc, eb_retried = proj.errorback_calls[0]
    assert eb_req is req
    assert isinstance(eb_exc, Exception), eb_exc  # last exception handed over
    assert eb_retried == proj.max_retry, eb_retried

    failed = core.get_failed_requests()
    assert len(failed) == 1, failed
    assert failed[0]["request"].url == BASE + "/flaky"
    assert failed[0]["request"] is req
    assert failed[0]["retried"] == proj.max_retry
    assert not proj.results  # nothing succeeded
    print("ok: retry exhaustion -> errorback called, failure recorded")

    # ---- resend the failed task; /flaky now returns 200 ----
    core.resend_failed()
    core._pool.join()
    assert core.get_failed_requests() == [], "failure list must be cleared"
    assert proj.callback_seen == [
        (BASE + "/flaky", 200, {"origin": "flaky"})
    ], proj.callback_seen
    assert proj.results == [
        {"url": BASE + "/flaky", "save": {"origin": "flaky"}}
    ], proj.results
    print("ok: resent task ran callback, result reached result_handler, records cleared")


def test_resend_failure_is_recorded_again():
    core = make_core()
    proj = core.project
    req = biu.Request(url=BASE + "/always500", callback=proj.parse)
    core.send_request(req)
    core._pool.join()
    assert len(core.get_failed_requests()) == 1

    core.resend_failed()  # pops and re-sends; endpoint still fails
    core._pool.join()
    failed = core.get_failed_requests()
    assert len(failed) == 1, failed  # failed again -> recorded again, not lost
    assert failed[0]["request"] is req
    assert len(proj.errorback_calls) == 2
    print("ok: re-failed resend is recorded again")


def test_allow_codes_passes_real_response():
    core = make_core()
    proj = core.project
    seen = []

    def cb(resp):
        seen.append(resp)

    req = biu.Request(url=BASE + "/always500", allow_codes={500}, callback=cb)
    core.send_request(req)
    core._pool.join()

    assert len(seen) == 1, "callback must run for allowed status"
    resp = seen[0]
    assert isinstance(resp, biu.Response)
    assert resp.status_code == 500
    assert resp.text == "always fails"
    assert core.get_failed_requests() == []
    print("ok: allow_codes branch hands a real Response to the callback")


def test_concurrent_failures_are_safe():
    core = make_core()
    proj = core.project
    reqs = [
        biu.Request(url=BASE + "/always500?i=%d" % i, callback=proj.parse)
        for i in range(10)
    ]
    greenlets = [gevent.spawn(core.send_request, r) for r in reqs]
    gevent.joinall(greenlets)
    core._pool.join()
    failed = core.get_failed_requests()
    assert len(failed) == 10, len(failed)
    assert len({f["request"].url for f in failed}) == 10
    assert len(proj.errorback_calls) == 10
    print("ok: 10 concurrent failures all recorded")


def test_default_errorback_does_not_raise():
    class DefaultProj(biu.Project):
        def parse(self, resp):
            return None

    core = biu.BiuCore(
        DefaultProj(concurrent=1, interval=0, max_retry=0, retry_delay=0)
    )
    core.send_request(biu.Request(url=BASE + "/always500", callback=lambda r: None))
    core._pool.join()
    assert len(core.get_failed_requests()) == 1
    print("ok: default Project.errorback is a safe no-op")


if __name__ == "__main__":
    test_retry_exhaustion_records_failure()
    test_resend_failure_is_recorded_again()
    test_allow_codes_passes_real_response()
    test_concurrent_failures_are_safe()
    test_default_errorback_does_not_raise()
    server.stop()
    print("ALL TESTS PASSED")
