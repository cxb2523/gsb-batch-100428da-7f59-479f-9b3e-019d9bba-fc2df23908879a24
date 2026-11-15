# -*- coding: utf-8 -*-
"""Acceptance test for the itemized /v1/session/import outcome.

The product requirement: one request must return a checkable, per-class
verdict instead of aborting the whole batch on the first rejected entry.

  * a localStorage key past the relay's size bound is refused BY NAME with its
    reason, but no longer aborts the import - every legal key still lands;
  * cookies and localStorage each carry expected / verified / missing (NAMES
    only, never values), plus their refused entries;
  * if EITHER class is incomplete the response is marked incomplete, however
    successful the other class was;
  * what did land is kept (the session is booked + persisted) so a retry only
    has to finish the missing pieces;
  * every missing/refused entry comes from THIS request: a prior site's names
    must never survive into a later response.

The task allows a replaced-transport harness when no Lightpanda is reachable
on ws://127.0.0.1:9222. This suite serves the REAL Handler (real auth, real
routing, real set_session import path) on an ephemeral port and only swaps the
CDP transport object - exactly the seam the real browser sits behind.

Scenario 1 (the acceptance sample): one cookie whose domain does not match the
target origin AND one oversized localStorage key, alongside one legal cookie
and one legal storage key. The response must list the missing cookie name and
the refused storage key name, be marked incomplete, and show the legal key
injected. Scenario 2 then resends an entirely legal request and must report
both classes with equal expected/verified counts and empty missing/refused.
"""
import http.client
import json
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "relay"))

import server  # noqa: E402

EXT_ID = "fcigkjkchglchhohedljlenopbkgnino"
TEST_TOKEN = "acceptance-token-not-a-secret-0001"


class _SelectiveTransport:
    """Simulates Lightpanda's two real behaviours this import depends on:

    1. Network.setCookies / getCookies keep exactly what a domain-matching
       navigation would hold. The relay already refuses a domain-mismatched
       cookie during conversion, so this transport additionally proves the
       browser-side drop path by filtering on domain.
    2. Runtime.evaluate writes localStorage and the per-key verification
       expression reads it back. A key the write carried is always present.

    Nothing about set_session is stubbed: cookies, navigation, storage
    injection and both verifications run through the product code.
    """

    def __init__(self, origin_host):
        self.origin_host = origin_host
        self.cookies = []
        self.storage = {}
        self.calls = []

    def request(self, method, params=None, session_id=None):
        params = params or {}
        self.calls.append(method)
        if method == "Network.setCookies":
            kept = []
            for cookie in params.get("cookies", []):
                domain = str(cookie.get("domain", "")).lstrip(".")
                # Mirror Lightpanda: a cookie whose domain the navigation does
                # not match is silently dropped, not rejected.
                if domain and (self.origin_host == domain
                               or self.origin_host.endswith("." + domain)):
                    kept.append(cookie)
            self.cookies = kept
            return {}
        if method == "Network.getCookies":
            urls = params.get("urls") or []
            if not urls:
                return {"cookies": [{"name": c.get("name")} for c in self.cookies]}
            hosts = [u.split("//", 1)[-1].rstrip("/") for u in urls]
            matched = [c for c in self.cookies
                       if any(h.endswith(str(c.get("domain", "")).lstrip("."))
                              for h in hosts)]
            return {"cookies": [{"name": c.get("name")} for c in matched]}
        if method == "Page.addScriptToEvaluateOnNewDocument":
            return {"identifier": "restore-1"}
        if method == "Page.removeScriptToEvaluateOnNewDocument":
            return {}
        if method == "Page.navigate":
            return {}
        if method == "Runtime.evaluate":
            expression = params.get("expression", "")
            if "localStorage.setItem" in expression:
                try:
                    body = expression.split("const data = ", 1)[1].split("; for", 1)[0]
                    self.storage.update(json.loads(body))
                except Exception:
                    pass
                return {"result": {"value": 0}}
            if "localStorage.getItem" in expression:
                want = json.loads(expression.split("const want = ", 1)[1]
                                  .split(";", 1)[0])
                missing = [k for k in want if k not in self.storage]
                return {"result": {"value": json.dumps({"missing": missing})}}
            return {"result": {"value": 0}}
        return {}


class _LiveRelay:
    """Real RelayServer + Handler on an ephemeral port, owned token, fake CDP."""

    def __init__(self, transport):
        self.transport = transport
        self._tmp = tempfile.mkdtemp(prefix="lp-acceptance-")
        os.environ["LP_BRIDGE_CONFIG_DIR"] = self._tmp

    def __enter__(self):
        self._saved = (
            server._load_secret, server._CDP_TRANSPORT, server._CDP_SESSION_ID,
            server._ensure_connection, server._NAV_SETTLE_SECONDS,
            server.valid_origin,
        )
        server._load_secret = lambda: TEST_TOKEN
        server._CDP_TRANSPORT = self.transport
        server._CDP_SESSION_ID = "acceptance-session"
        server._ensure_connection = lambda origin: None
        server._NAV_SETTLE_SECONDS = 0
        # The real check performs a DNS resolution and rejects reserved TLDs;
        # the origin policy itself is covered elsewhere. Here we only need the
        # import path, so accept the well-formed HTTPS origins this harness uses.
        server.valid_origin = lambda origin: isinstance(origin, str) \
            and origin.startswith("https://")
        server._SYNCED_SESSIONS.clear()
        server._LAST_SESSION = None
        self.srv = server.RelayServer(("127.0.0.1", 0), server.Handler)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.srv.shutdown()
        self.srv.server_close()
        (server._load_secret, server._CDP_TRANSPORT, server._CDP_SESSION_ID,
         server._ensure_connection, server._NAV_SETTLE_SECONDS,
         server.valid_origin) = self._saved
        server._SYNCED_SESSIONS.clear()
        server._LAST_SESSION = None
        shutil.rmtree(self._tmp, ignore_errors=True)
        os.environ.pop("LP_BRIDGE_CONFIG_DIR", None)
        return False

    def call(self, payload):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=25)
        try:
            conn.request(
                "POST", "/v1/session/import", body=json.dumps(payload),
                headers={"X-Bridge-Token": TEST_TOKEN,
                         "Origin": "chrome-extension://" + EXT_ID,
                         "Content-Type": "application/json"})
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
        finally:
            conn.close()


class ItemizedImportOutcomeTests(unittest.TestCase):
    ORIGIN = "https://shop.example.com"

    def setUp(self):
        self.transport = _SelectiveTransport("shop.example.com")

    def _payload_bad(self):
        return {
            "origin": self.ORIGIN,
            "cookies": [
                # Legal: domain matches the target origin.
                {"name": "session_id", "value": "legal-cookie-value",
                 "domain": ".shop.example.com", "path": "/",
                 "secure": True, "httpOnly": True},
                # Rejected on the way in: domain belongs to another site.
                {"name": "foreign_tracker", "value": "foreign-cookie-value",
                 "domain": ".other.example", "path": "/",
                 "secure": True, "httpOnly": False},
            ],
            "storage": {
                # Legal key: must be injected and verified.
                "user": '{"id": 42}',
                # Refused by the size bound: value past MAX_VALUE_CHARS.
                "huge_blob": "x" * (server.MAX_VALUE_CHARS + 1),
            },
        }

    def test_mixed_request_is_incomplete_but_keeps_the_legal_entries(self):
        with _LiveRelay(self.transport) as relay:
            status, body = relay.call(self._payload_bad())

            # The batch is no longer rejected wholesale: it answers 200 with an
            # honest, itemized verdict.
            self.assertEqual(status, 200, body)
            self.assertFalse(body["ok"], "an import with a refused key and a "
                                         "rejected cookie must never read as ok")
            self.assertFalse(body["complete"])
            report = body["report"]

            # --- cookies: one legal, one refused for domain mismatch ---------
            cookies = report["cookies"]
            self.assertEqual(cookies["expected"], 1)
            self.assertEqual(cookies["verified"], 1)
            self.assertEqual(cookies["missing"], [])
            self.assertEqual(len(cookies["refused"]), 1)
            self.assertEqual(cookies["refused"][0]["name"], "foreign_tracker")
            self.assertIn("domain", cookies["refused"][0]["reason"])

            # --- localStorage: one legal injected, one oversized refused ----
            storage = report["storage"]
            self.assertEqual(storage["expected"], 1)
            self.assertEqual(storage["verified"], 1)
            self.assertEqual(storage["missing"], [])
            self.assertEqual(len(storage["refused"]), 1)
            self.assertEqual(storage["refused"][0]["name"], "huge_blob")
            self.assertEqual(storage["refused"][0]["reason"], "value too large")
            self.assertEqual(storage["refused"][0]["size"],
                             server.MAX_VALUE_CHARS + 1)

            # The legal storage key really reached the page, and the refused one
            # never did.
            self.assertIn("user", self.transport.storage)
            self.assertNotIn("huge_blob", self.transport.storage)
            # The legal cookie really reached the jar; the foreign one did not.
            held = {c["name"] for c in self.transport.cookies}
            self.assertEqual(held, {"session_id"})

            # Names only: no cookie/storage VALUE may appear anywhere in the body.
            raw = json.dumps(body)
            for secret in ("legal-cookie-value", "foreign-cookie-value",
                           "legal-storage-token"):
                self.assertNotIn(secret, raw,
                                 "a value leaked into the import response: %r" % secret[:12])
            # The oversized value is a long repeated run: no run of its payload may
            # be echoed back beyond a trivial length.
            self.assertNotIn("x" * 64, raw)

            # What landed is kept, so a retry can finish the missing pieces.
            self.assertIn(self.ORIGIN, server._SYNCED_SESSIONS)
            state_path = pathlib.Path(relay._tmp) / "session.json"
            self.assertTrue(state_path.exists())

    def test_a_fully_legal_retry_reports_equal_counts_and_empty_gaps(self):
        with _LiveRelay(self.transport) as relay:
            # First the mixed request, then an entirely legal retry.
            status1, body1 = relay.call(self._payload_bad())
            self.assertEqual(status1, 200)
            self.assertFalse(body1["complete"])

            legal = {
                "origin": self.ORIGIN,
                "cookies": [
                    {"name": "session_id", "value": "legal-cookie-value",
                     "domain": ".shop.example.com", "path": "/",
                     "secure": True, "httpOnly": True},
                    {"name": "csrf", "value": "legal-csrf-value",
                     "domain": "shop.example.com", "path": "/",
                     "secure": True, "httpOnly": False},
                ],
                "storage": {"user": '{"id": 42}', "token": "legal-storage-token"},
            }
            status2, body2 = relay.call(legal)

        self.assertEqual(status2, 200, body2)
        self.assertTrue(body2["ok"])
        self.assertTrue(body2["complete"])
        report = body2["report"]
        self.assertEqual(report["cookies"]["expected"], 2)
        self.assertEqual(report["cookies"]["verified"], 2)
        self.assertEqual(report["cookies"]["missing"], [])
        self.assertEqual(report["cookies"]["refused"], [])
        self.assertEqual(report["storage"]["expected"], 2)
        self.assertEqual(report["storage"]["verified"], 2)
        self.assertEqual(report["storage"]["missing"], [])
        self.assertEqual(report["storage"]["refused"], [])
        # Both legal keys are live in the page.
        self.assertEqual(set(self.transport.storage), {"user", "token"})

    def test_response_never_carries_a_previous_site_names(self):
        """The cross-site leak the per-request reset exists to prevent."""
        with _LiveRelay(self.transport) as relay:
            relay.call(self._payload_bad())
            # A second, entirely legal request for a DIFFERENT origin must not
            # echo the first request's missing cookie or refused storage key.
            other_transport = _SelectiveTransport("dev.to")
            server._CDP_TRANSPORT = other_transport
            status, body = relay.call({
                "origin": "https://dev.to",
                "cookies": [{"name": "sid", "value": "v",
                             "domain": ".dev.to", "path": "/",
                             "secure": True, "httpOnly": True}],
                "storage": {"k": "v"},
            })
        self.assertEqual(status, 200, body)
        self.assertTrue(body["complete"])
        raw = json.dumps(body)
        for stale in ("foreign_tracker", "huge_blob"):
            self.assertNotIn(stale, raw,
                             "a previous site's %s leaked into this response" % stale)
        self.assertEqual(body["report"]["cookies"]["missing"], [])
        self.assertEqual(body["report"]["storage"]["refused"], [])

    def test_legacy_flat_fields_match_the_structured_report(self):
        """Older callers (popup.js, sync_session.py) read the flat fields; they
        must stay consistent with the nested canonical sections."""
        with _LiveRelay(self.transport) as relay:
            _, body = relay.call(self._payload_bad())
        self.assertEqual(body["cookie_count"], body["report"]["cookies"]["verified"])
        self.assertEqual(body["cookie_expected"],
                         body["report"]["cookies"]["expected"])
        self.assertEqual(body["storage_count"], body["report"]["storage"]["verified"])
        self.assertEqual(body["storage_expected"],
                         body["report"]["storage"]["expected"])
        self.assertEqual(body["cookie_refused"][0]["name"], "foreign_tracker")
        # The legacy storage_refused stays a preformatted name[reason,size] row.
        self.assertIn("huge_blob", body["storage_refused"][0])
        self.assertIn("value too large", body["storage_refused"][0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
