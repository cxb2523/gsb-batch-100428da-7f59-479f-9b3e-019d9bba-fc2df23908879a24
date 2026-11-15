import asyncio
import logging
import unittest

from core.enumerator import SubdomainEnumerator


class _Resp:
    def __init__(self, status, payload=None):
        self.status = status
        self._payload = payload if payload is not None else []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self, content_type=None):
        return self._payload


class _Session:
    def __init__(self, status, payload=None):
        self._status = status
        self._payload = payload if payload is not None else []

    def get(self, *a, **k):
        return _Resp(self._status, self._payload)


class _RoutedSession:
    """Serves a different (status, payload) per URL substring."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, *a, **k):
        self.calls.append(url)
        for substr, status, payload in self.routes:
            if substr in url:
                return _Resp(status, payload)
        return _Resp(404, [])


class _RecordingLogger(logging.Logger):
    def __init__(self):
        super().__init__("rec")
        self.warnings = []

    def warning(self, msg, *a, **k):
        self.warnings.append(str(msg))

    def debug(self, msg, *a, **k):
        pass

    def info(self, msg, *a, **k):
        pass


class TestCrtShFailure(unittest.TestCase):
    def test_non_200_logs_warning_not_silent_zero(self):
        """A crt.sh outage must be distinguishable from 'no certificates exist'."""
        log = _RecordingLogger()
        e = SubdomainEnumerator({"example.com"}, log)
        e._aio_session = _Session(502)
        subs = asyncio.run(e._query_crtsh("example.com"))
        self.assertIsNone(subs)
        self.assertTrue(
            any("502" in w for w in log.warnings),
            f"expected a warning naming the HTTP status, got {log.warnings}")


class TestCrtShParsing(unittest.TestCase):
    """Covers the crt.sh success path, which the 502 outage left unexercised."""

    PAYLOAD = [
        {"name_value": "api.example.com"},
        {"name_value": "*.cdn.example.com"},
        {"name_value": "a.example.com\nb.example.com"},
        {"name_value": "example.com"},
        {"name_value": "notexample.com"},
        {"name_value": "other.co.uk"},
        {"name_value": "API.EXAMPLE.COM"},
        {"name_value": ""},
    ]

    def _query(self):
        log = _RecordingLogger()
        e = SubdomainEnumerator({"example.com"}, log)
        e._aio_session = _Session(200, self.PAYLOAD)
        return sorted(asyncio.run(e._query_crtsh("example.com"))), log

    def test_parses_and_normalises_names(self):
        subs, _ = self._query()
        self.assertEqual(
            subs,
            ["a.example.com", "api.example.com", "b.example.com",
             "cdn.example.com", "example.com"])

    def test_rejects_lookalike_and_foreign_domains(self):
        subs, _ = self._query()
        self.assertNotIn("notexample.com", subs)
        self.assertNotIn("other.co.uk", subs)

    def test_success_path_logs_no_warning(self):
        _, log = self._query()
        self.assertEqual(log.warnings, [])


CERTSPOTTER_PAYLOAD = [
    {"id": "1", "dns_names": ["example.com", "www.example.com"]},
    {"id": "2", "dns_names": ["*.api.example.com", "notexample.com"]},
    {"id": "3", "dns_names": ["DEEP.SUB.EXAMPLE.COM"]},
]


class TestCrtShFailureSignalling(unittest.TestCase):
    def test_failure_returns_none_not_empty_list(self):
        """None means 'source failed'; [] means 'no certificates exist'."""
        log = _RecordingLogger()
        e = SubdomainEnumerator({"example.com"}, log)
        e._aio_session = _Session(502)
        self.assertIsNone(asyncio.run(e._query_crtsh("example.com")))

    def test_success_with_no_certs_returns_empty_list(self):
        log = _RecordingLogger()
        e = SubdomainEnumerator({"example.com"}, log)
        e._aio_session = _Session(200, [])
        self.assertEqual(asyncio.run(e._query_crtsh("example.com")), [])


class TestCertspotter(unittest.TestCase):
    def _enum(self, status=200, payload=None):
        log = _RecordingLogger()
        e = SubdomainEnumerator({"example.com"}, log)
        e._aio_session = _Session(status, CERTSPOTTER_PAYLOAD if payload is None else payload)
        return e, log

    def test_parses_dns_names_with_same_rules_as_crtsh(self):
        e, _ = self._enum()
        subs = sorted(asyncio.run(e._query_certspotter("example.com")))
        self.assertEqual(
            subs,
            ["api.example.com", "deep.sub.example.com", "example.com", "www.example.com"])

    def test_rejects_lookalike_domain(self):
        e, _ = self._enum()
        self.assertNotIn("notexample.com", asyncio.run(e._query_certspotter("example.com")))

    def test_rate_limit_warns_and_returns_none(self):
        e, log = self._enum(status=429, payload=[])
        self.assertIsNone(asyncio.run(e._query_certspotter("example.com")))
        self.assertTrue(any("429" in w for w in log.warnings),
                        f"expected a warning naming the status, got {log.warnings}")


class TestPassiveFallback(unittest.TestCase):
    def _enum(self, routes):
        log = _RecordingLogger()
        e = SubdomainEnumerator({"example.com"}, log)
        sess = _RoutedSession(routes)
        e._aio_session = sess
        return e, log, sess

    def test_falls_back_to_certspotter_when_crtsh_fails(self):
        e, _, sess = self._enum([
            ("crt.sh", 502, []),
            ("certspotter", 200, CERTSPOTTER_PAYLOAD),
        ])
        subs, source = asyncio.run(e._query_passive_ct("example.com"))
        self.assertEqual(source, "certspotter")
        self.assertIn("api.example.com", subs)
        self.assertTrue(any("certspotter" in c for c in sess.calls))

    def test_does_not_call_certspotter_when_crtsh_succeeds(self):
        e, _, sess = self._enum([
            ("crt.sh", 200, [{"name_value": "api.example.com"}]),
            ("certspotter", 200, CERTSPOTTER_PAYLOAD),
        ])
        subs, source = asyncio.run(e._query_passive_ct("example.com"))
        self.assertEqual(source, "crt.sh")
        self.assertEqual(subs, ["api.example.com"])
        self.assertFalse(any("certspotter" in c for c in sess.calls),
                         "certspotter must not be called when crt.sh worked")

    def test_no_fallback_when_crtsh_legitimately_returns_nothing(self):
        """Zero certs is a real answer - don't burn the certspotter rate limit."""
        e, _, sess = self._enum([
            ("crt.sh", 200, []),
            ("certspotter", 200, CERTSPOTTER_PAYLOAD),
        ])
        subs, source = asyncio.run(e._query_passive_ct("example.com"))
        self.assertEqual(subs, [])
        self.assertEqual(source, "crt.sh")
        self.assertFalse(any("certspotter" in c for c in sess.calls))

    def test_both_sources_down_returns_empty(self):
        e, log, _ = self._enum([("crt.sh", 502, []), ("certspotter", 503, [])])
        subs, source = asyncio.run(e._query_passive_ct("example.com"))
        self.assertEqual(subs, [])
        joined = " | ".join(log.warnings)
        self.assertIn("crt.sh", joined)
        self.assertIn("certspotter", joined)
        self.assertIn("brute-force only", joined)


if __name__ == "__main__":
    unittest.main()
