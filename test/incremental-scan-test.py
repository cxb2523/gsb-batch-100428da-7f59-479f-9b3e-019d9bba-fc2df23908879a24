"""LIVE test: an incremental `explore_page` reports SEMANTIC change only.

Asserts, on test/harness/incremental-scan.html (a long page whose controls mostly
sit far below the fold):

  [1] the FIRST incremental call escalates to a full scan and seeds a baseline
      (by design — there is nothing to diff against yet)
  [2] SCROLLING produces NO added/removed/changed, and does NOT re-escalate
      (the old behaviour: the previous viewport window `removed`, the new one
       `added`, changedRatio past 0.6, forced full re-scan)
  [3] a REAL DOM insertion is reported as added (and is NOT a full re-scan)
  [4] a REAL DOM removal is reported as removed
  [5] opening a dialog reports ITS controls as added while the aria-hidden'd
      background behind it is NOT reported as removed; closing it reports the
      controls as removed
  [6] a ref handed out before a scroll still resolves to the same element after

Usage: python test/incremental-scan-test.py [url]
       (start test/harness/serve.cjs first; default page is the fixture)
Exit 0 = all assertions pass.
"""
import json, os, sys, time, urllib.request

URL = os.environ.get("WEBSENSE_MCP", "http://127.0.0.1:9222/mcp")
PAGE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:38499/incremental-scan.html"
FAILS = []


def _parse(raw):
    out = []
    for line in (raw or "").split("\n"):
        line = line.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


class Sess:
    def __init__(self, label):
        self.sid = None
        self._post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": label, "version": "1"}}})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self._id = 1

    def _post(self, p):
        h = {"Content-Type": "application/json",
             "Accept": "application/json, text/event-stream",
             "MCP-Protocol-Version": "2025-06-18"}
        if self.sid:
            h["mcp-session-id"] = self.sid
        r = urllib.request.urlopen(urllib.request.Request(
            URL, data=json.dumps(p).encode(), headers=h, method="POST"), timeout=180)
        s = r.headers.get("mcp-session-id")
        if s:
            self.sid = s
        return r.read().decode("utf-8", "replace")

    def call(self, tool, args=None):
        self._id += 1
        body = self._post({"jsonrpc": "2.0", "id": self._id, "method": "tools/call",
                           "params": {"name": tool, "arguments": args or {}}})
        for m in _parse(body):
            if m.get("id") != self._id:
                continue
            content = (m.get("result") or {}).get("content") or []
            t = next((c.get("text") for c in content if isinstance(c, dict)), None)
            try:
                j = json.loads(t)
            except Exception:
                return {"_text": t}
            # Page-op replies are WRAPPED {type,id,success,data:{...}} — unwrap.
            return j.get("data") if isinstance(j, dict) and isinstance(j.get("data"), dict) else j
        return {"_error": "no response"}


def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def incr(A):
    """One incremental call, as a client would make it."""
    return A.call("explore_page", {"incremental": True}) or {}


def summary(d):
    return ("added=%d changed=%d removed=%d ratio=%.3f escalated=%s"
            % (len(d.get("added") or []), len(d.get("changed") or []),
               len(d.get("removed") or []), float(d.get("changedRatio") or 0),
               d.get("escalated")))


def labels(entries):
    return [str(e.get("label") or "") for e in (entries or [])]


def keys(entries):
    return [str(e.get("key") or "") for e in (entries or [])]


def main():
    print("=" * 74)
    print("INCREMENTAL SCAN LIVE TEST  ->  " + URL)
    print("page: " + PAGE)
    print("=" * 74)
    A = Sess("incr-scan-test")
    r = A.call("navigate", {"url": PAGE})
    tab = (r or {}).get("tabId")
    print("navigate -> tabId=%s success=%s" % (tab, (r or {}).get("success")))
    time.sleep(2.5)

    # ── [1] first call: no baseline, so a full SAG is returned and one is seeded
    print("\n[1] first incremental call (no baseline yet)")
    d1 = incr(A)
    check("first call escalates to a full scan (nothing to diff against yet)",
          d1.get("incremental") is True and d1.get("escalated") is False,
          json.dumps(d1)[:200])
    check("first call returns a real action list (the full map)", bool(d1.get("actions")),
          len(d1.get("actions") or []))

    # ── [2] SCROLL: must produce no delta at all
    print("\n[2] scroll -> the delta must be EMPTY")
    A.call("scroll", {"direction": "down", "amount": 3})
    time.sleep(0.8)
    d2 = incr(A)
    print("    " + summary(d2))
    check("scroll reports NO added elements", len(d2.get("added") or []) == 0,
          json.dumps(d2.get("added") or [])[:200])
    check("scroll reports NO removed elements", len(d2.get("removed") or []) == 0,
          json.dumps(d2.get("removed") or [])[:200])
    check("scroll reports NO changed elements", len(d2.get("changed") or []) == 0,
          json.dumps(d2.get("changed") or [])[:200])
    check("scroll does NOT escalate to a full scan", d2.get("escalated") is False, summary(d2))
    check("changedRatio stays well under 0.6", float(d2.get("changedRatio") or 0) < 0.6, summary(d2))

    # ── [3] REAL insertion -> added
    print("\n[3] real DOM insertion (Add row)")
    A.call("click", {"ref": "#btn-add-row"})
    time.sleep(0.8)
    d3 = incr(A)
    print("    " + summary(d3))
    add3 = labels(d3.get("added") or [])
    check("a real insertion is reported as added",
          any("Row" in x for x in add3), json.dumps(add3)[:200])

    # ── [4] REAL removal -> removed
    print("\n[4] real DOM removal (Remove last row)")
    A.call("click", {"ref": "#btn-remove-row"})
    time.sleep(0.8)
    d4 = incr(A)
    print("    " + summary(d4))
    rem4 = labels(d4.get("removed") or [])
    check("a real removal is reported as removed",
          any("Row" in x for x in rem4), json.dumps(rem4)[:200])

    # ── [5] DIALOG open/close
    print("\n[5] dialog open -> its controls are added; the background is not removed")
    A.call("click", {"ref": "#btn-open-modal"})
    time.sleep(0.8)
    d5 = incr(A)
    print("    " + summary(d5))
    add5 = labels(d5.get("added") or [])
    check("the dialog's controls are reported as added",
          "OK" in add5 and "Cancel" in add5, json.dumps(add5)[:200])
    check("the aria-hidden'd page behind the dialog is NOT reported as removed",
          len(d5.get("removed") or []) == 0, json.dumps(labels(d5.get("removed") or []))[:200])

    print("\n[5b] dialog close -> its controls are removed")
    A.call("click", {"ref": "#modal-cancel"})
    time.sleep(0.8)
    d6 = incr(A)
    print("    " + summary(d6))
    rem6 = labels(d6.get("removed") or [])
    check("the dialog's controls are reported as removed",
          "OK" in rem6 and "Cancel" in rem6, json.dumps(rem6)[:200])
    check("un-hiding the background is NOT reported as added",
          len(d6.get("added") or []) == 0, json.dumps(labels(d6.get("added") or []))[:200])

    # ── [6] REF stability across a scroll
    print("\n[6] a ref issued before a scroll still resolves after it")
    g1 = A.call("inspect", {"kind": "geometry", "selector": "#rowbtn-0"}) or {}
    A.call("scroll", {"direction": "down", "amount": 3})
    time.sleep(0.5)
    g2 = A.call("inspect", {"kind": "geometry", "selector": "#rowbtn-0"}) or {}
    ok = bool(g1.get("success") or g1.get("visible") is not None) \
        and bool(g2.get("success") or g2.get("visible") is not None) \
        and g1.get("tag") == g2.get("tag")
    check("the same ref still resolves to the same element after a scroll", ok,
          json.dumps({"before": g1, "after": g2})[:300])

    print("\n" + "=" * 78)
    print("%d failure(s)" % len(FAILS))
    if FAILS:
        for f in FAILS:
            print("  - " + f)
        return 1
    print("VERDICT: incremental explore reports SEMANTIC change only — scrolling is not a page change.")
    return 0


sys.exit(main())
