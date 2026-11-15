"""Wildcard DNS filtering across the CT branch of subdomain enumeration.

The bug being pinned: wildcard detection only suppressed DNS brute-force.
CT-discovered hosts were still resolved and HTTP-probed one by one, so every
random host under a `*.target.com` wildcard was recorded as a real asset and
fed to the crawler as a seed. These tests drive the whole pipeline with a fake
resolver and a fake HTTP session so the filtering, the retained audit records,
and the unchanged non-wildcard behaviour are all observable.
"""

import asyncio
import json
import logging
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core import enumerator as enum_mod
from core.enumerator import SubdomainEnumerator, SubdomainResult, recon_summary


def _save_recon(recon, output_dir):
    """Call jsrip.save_recon_report with a scoped stub for core/patterns.py.

    jsrip imports core.analyzer, which imports core.patterns - absent from
    this snapshot, so the import would fail. The stub and any modules this
    call pulled in are removed afterwards, and modules that were already
    loaded are left exactly as they were.
    """
    added = []
    if "core.patterns" not in sys.modules:
        stub = types.ModuleType("core.patterns")
        stub.patterns = {}
        stub.pattern_contexts = {}
        sys.modules["core.patterns"] = stub
        added.append("core.patterns")
    for name in ("jsrip", "core.analyzer"):
        if name not in sys.modules:
            added.append(name)
    try:
        import jsrip
        jsrip.save_recon_report(recon, output_dir)
    finally:
        for name in added:
            sys.modules.pop(name, None)


WILDCARD_IP = "203.0.113.10"
REAL_IP = "198.51.100.7"


class _FakeResolver:
    """Answers A queries from an explicit host->ip(s) map.

    A value may be a single IP or a list of them. Unknown names fall back to
    `default`, which is how a real wildcard behaves for garbage labels.
    """

    def __init__(self, answers, default=None):
        self.answers = dict(answers)
        self.default = default
        self.queries = []

    async def query(self, name, qtype):
        self.queries.append(name)
        ip = self.answers.get(name, self.default)
        if ip is None:
            raise RuntimeError("NXDOMAIN")
        return [SimpleNamespace(host=h) for h in ([ip] if isinstance(ip, str) else ip)]


class _Resp:
    def __init__(self, status):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _AliveSession:
    """HTTP session where a host is alive iff it maps to a 2xx status."""

    def __init__(self, statuses):
        self.statuses = statuses
        self.probed = []

    def get(self, url, *a, **k):
        host = url.split("://", 1)[1]
        self.probed.append(host)
        status = self.statuses.get(host)
        if status is None:
            return _FailingResp()
        return _Resp(status)


class _FailingResp:
    async def __aenter__(self):
        raise ConnectionError("unreachable")

    async def __aexit__(self, *a):
        return False


class _CtSession:
    """CT HTTP session serving crt.sh payloads."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, *a, **k):
        self.calls.append(url)
        return _CtResp(self.payload)


class _CtResp:
    def __init__(self, payload):
        self.status = 200
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self, content_type=None):
        return self._payload


class _Logger(logging.Logger):
    def __init__(self):
        super().__init__("rec")
        self.messages = []

    def info(self, msg, *a, **k):
        self.messages.append(str(msg))

    def warning(self, msg, *a, **k):
        self.messages.append(str(msg))

    def error(self, msg, *a, **k):
        self.messages.append(str(msg))

    def debug(self, msg, *a, **k):
        pass


def _run_domain(answers, ct_names, statuses, default=None, domain="target.com",
                words=("www",)):
    """Run _enumerate_domain end-to-end against injected fakes."""
    log = _Logger()
    e = SubdomainEnumerator({domain}, log)
    resolver = _FakeResolver(answers, default=default)
    ct = _CtSession([{"name_value": n} for n in ct_names])
    http = _AliveSession(statuses)

    e._resolver = resolver
    e._aio_session = ct
    e._load_wordlist = lambda: list(words)
    # _check_alive fetches its own session; hand it the HTTP fake directly.
    orig_check_alive = e._check_alive

    async def run():
        e._aio_session = ct
        results = None
        # Phase 3 is internal; swap the session just for the probe phase by
        # monkeypatching the accessor after CT has already been queried.
        async def check_alive(resolved):
            e._aio_session = http
            return await orig_check_alive(resolved)
        e._check_alive = check_alive
        results = await e._enumerate_domain(domain)
        return results, http

    with mock.patch.object(enum_mod, "aiodns", SimpleNamespace()):
        results, http = asyncio.run(run())
    return results, resolver, http, log


class TestWildcardFiltersCtHosts(unittest.TestCase):
    """A wildcard must apply to CT-discovered names, not just brute-force."""

    CT_NAMES = ["api.target.com", "random1.target.com", "random2.target.com"]

    def _run(self):
        return _run_domain(
            answers={
                "api.target.com": REAL_IP,
                "random1.target.com": WILDCARD_IP,
                "random2.target.com": WILDCARD_IP,
            },
            ct_names=self.CT_NAMES,
            statuses={
                "api.target.com": 200,
                "random1.target.com": 200,   # wildcard answers HTTP too
                "random2.target.com": 200,
            },
            default=WILDCARD_IP,           # garbage labels hit the wildcard
        )

    def test_wildcard_ip_hosts_are_not_alive(self):
        results, _, _, _ = self._run()
        by_name = {r.subdomain: r for r in results}
        self.assertFalse(by_name["random1.target.com"].alive)
        self.assertFalse(by_name["random2.target.com"].alive)

    def test_wildcard_ip_hosts_are_never_http_probed(self):
        _, _, http, _ = self._run()
        self.assertEqual(http.probed, ["api.target.com"],
                         "filtered hosts must not reach the HTTP probe")

    def test_non_wildcard_ip_host_stays_alive(self):
        results, _, _, _ = self._run()
        api = next(r for r in results if r.subdomain == "api.target.com")
        self.assertTrue(api.alive)
        self.assertEqual(api.status_code, 200)
        self.assertEqual(api.ip, REAL_IP)

    def test_filtered_hosts_are_kept_in_the_domain_results(self):
        results, _, _, _ = self._run()
        names = {r.subdomain for r in results}
        self.assertEqual(names, set(self.CT_NAMES),
                         "filtered hosts must be retained, not dropped")

    def test_filtered_hosts_are_marked_wildcard_with_a_reason(self):
        results, _, _, _ = self._run()
        for name in ("random1.target.com", "random2.target.com"):
            r = next(x for x in results if x.subdomain == name)
            with self.subTest(host=name):
                self.assertEqual(r.classification, "wildcard")
                self.assertIn("wildcard", r.filter_reason.lower())
                self.assertIn(WILDCARD_IP, r.filter_reason)
                self.assertEqual(r.source, "crt.sh")

    def test_log_reports_the_filtered_count(self):
        _, _, _, log = self._run()
        joined = " | ".join(log.messages)
        self.assertIn("wildcard-filtered: 2", joined)
        self.assertIn("target.com", joined)

    def test_ct_still_queried_under_wildcard(self):
        """Passive discovery must not be skipped just because DNS is wild."""
        _, _, _, log = self._run()
        joined = " | ".join(log.messages)
        self.assertIn("crt.sh: 3 subdomains", joined)
        self.assertNotIn("bruteforce: 0 resolved", joined)

    def test_bruteforce_still_skipped_under_wildcard(self):
        _, resolver, _, log = self._run()
        joined = " | ".join(log.messages)
        self.assertIn("bruteforce: SKIPPED (wildcard DNS)", joined)
        # The wordlist is the only thing that would issue non-CT lookups.
        ct_lookups = [q for q in resolver.queries if q in self.CT_NAMES]
        self.assertEqual(set(ct_lookups), set(self.CT_NAMES))


class TestThreeWayClassification(unittest.TestCase):
    """subdomains.json must distinguish alive / wildcard / unresolved."""

    def test_unresolved_and_wildcard_are_separate_classes(self):
        results, _, _, _ = _run_domain(
            answers={
                "api.target.com": REAL_IP,
                "gone.target.com": None,        # explicit NXDOMAIN
                "junk.target.com": WILDCARD_IP,
            },
            ct_names=["api.target.com", "gone.target.com", "junk.target.com"],
            statuses={"api.target.com": 200},
            default=WILDCARD_IP,
        )
        by_name = {r.subdomain: r for r in results}
        self.assertEqual(by_name["api.target.com"].classification, "alive")
        self.assertEqual(by_name["junk.target.com"].classification, "wildcard")
        self.assertEqual(by_name["gone.target.com"].classification, "unresolved")
        self.assertEqual(by_name["gone.target.com"].ip, "")
        self.assertIn("resolution", by_name["gone.target.com"].filter_reason)

    def test_saved_json_has_all_three_counts_and_per_host_classification(self):
        domain_results, _, _, _ = _run_domain(
            answers={
                "api.target.com": REAL_IP,
                "gone.target.com": None,
                "junk.target.com": WILDCARD_IP,
            },
            ct_names=["api.target.com", "gone.target.com", "junk.target.com"],
            statuses={"api.target.com": 200},
            default=WILDCARD_IP,
        )
        with tempfile.TemporaryDirectory() as d:
            _save_recon({"target.com": domain_results}, d)
            data = json.loads(
                (Path(d) / "reports" / "subdomains.json").read_text(encoding="utf-8"))
        self.assertEqual(data["target.com"]["alive"], 1)
        self.assertEqual(data["target.com"]["wildcard_filtered"], 1)
        self.assertEqual(data["target.com"]["unresolved"], 1)
        self.assertEqual(data["target.com"]["total"], 3)
        classes = {r["subdomain"]: r["classification"]
                   for r in data["target.com"]["results"]}
        self.assertEqual(classes, {
            "api.target.com": "alive",
            "junk.target.com": "wildcard",
            "gone.target.com": "unresolved",
        })


class TestNonWildcardBehaviourUnchanged(unittest.TestCase):
    """No wildcard -> exactly the old pipeline, including CT-only probing."""

    def _run(self):
        return _run_domain(
            answers={"api.target.com": REAL_IP},
            ct_names=["api.target.com", "stale.target.com"],
            statuses={"api.target.com": 200},
            default=None,                   # garbage labels: NXDOMAIN
        )

    def test_stale_ct_names_still_dropped_without_wildcard(self):
        """Historical record, not a scan regression: no wildcard means an
        unresolvable CT name is silently skipped, as before."""
        results, _, _, _ = self._run()
        self.assertEqual({r.subdomain for r in results}, {"api.target.com"})

    def test_ct_hosts_are_still_http_probed(self):
        _, _, http, _ = self._run()
        self.assertEqual(http.probed, ["api.target.com"])

    def test_live_host_classified_alive(self):
        results, _, _, _ = self._run()
        self.assertEqual(results[0].classification, "alive")
        self.assertTrue(results[0].alive)

    def test_no_wildcard_log_line_when_none_detected(self):
        _, _, _, log = self._run()
        self.assertFalse(any("WILDCARD DNS detected" in m for m in log.messages))
        self.assertFalse(any("wildcard-filtered" in m for m in log.messages))

    def test_bruteforce_runs_normally(self):
        _, resolver, _, log = self._run()
        joined = " | ".join(log.messages)
        self.assertIn("bruteforce:", joined)
        self.assertNotIn("SKIPPED", joined)


class TestWildcardDetectionCollectsAllIps(unittest.TestCase):
    """A wildcard with several A records must contribute all of them."""

    def test_every_answer_ip_is_in_the_wildcard_set(self):
        class _MultiResolver:
            async def query(self, name, qtype):
                return [SimpleNamespace(host="203.0.113.10"),
                        SimpleNamespace(host="203.0.113.11")]

        e = SubdomainEnumerator({"target.com"}, _Logger())
        e._resolver = _MultiResolver()
        with mock.patch.object(enum_mod, "aiodns", SimpleNamespace()):
            ips = asyncio.run(e._detect_wildcard("target.com"))
        self.assertEqual(ips, {"203.0.113.10", "203.0.113.11"})

    def test_any_wildcard_answer_taints_a_multi_homed_host(self):
        """A name resolving to a real IP *and* a wildcard IP is not trustworthy:
        the wildcard record alone explains it, so it must be filtered."""
        results, _, http, _ = _run_domain(
            answers={"mixed.target.com": [REAL_IP, WILDCARD_IP]},
            ct_names=["mixed.target.com"],
            statuses={"mixed.target.com": 200},
            default=WILDCARD_IP,
        )
        mixed = next(r for r in results if r.subdomain == "mixed.target.com")
        self.assertEqual(mixed.classification, "wildcard")
        self.assertFalse(mixed.alive)
        self.assertEqual(http.probed, [])


class TestReporterRendersClassification(unittest.TestCase):
    """The HTML report must expose the four classes, not just ALIVE/DEAD."""

    def test_html_filters_and_rows_carry_classification(self):
        from utils.reporter import ReportGenerator

        results = [
            SubdomainResult("api.target.com", REAL_IP, 200, True, "crt.sh",
                            SubdomainResult.ALIVE),
            SubdomainResult("junk.target.com", WILDCARD_IP, 0, False, "crt.sh",
                            SubdomainResult.WILDCARD,
                            f"resolved to wildcard IP {WILDCARD_IP}"),
            SubdomainResult("gone.target.com", "", 0, False, "crt.sh",
                            SubdomainResult.UNRESOLVED, "DNS resolution failed"),
            SubdomainResult("down.target.com", "198.51.100.9", 0, False,
                            "crt.sh", SubdomainResult.DEAD),
        ]
        scan = {
            "scan_info": {"urls": ["https://target.com"], "urls_count": 1,
                          "duration": "0:00:10"},
            "statistics": {"js_files_found": 0, "secrets_found": 0,
                           "endpoints_found": 0},
            "findings": {"secrets": [], "endpoints": [], "js_files": []},
            "subdomain_recon": {"target.com": recon_summary(results)},
        }
        with tempfile.TemporaryDirectory() as d:
            ReportGenerator(d, logging.getLogger("t")).generate_report(scan, "html")
            html = (Path(d) / "reports" / "report.html").read_text(encoding="utf-8")
        for token in ('ALIVE (1)', 'DEAD (1)', 'WILDCARD (1)', 'UNRESOLVED (1)',
                      '"classification":"wildcard"',
                      f'"filter_reason":"resolved to wildcard IP {WILDCARD_IP}"'):
            with self.subTest(token=token):
                self.assertIn(token, html)


if __name__ == "__main__":
    unittest.main()
