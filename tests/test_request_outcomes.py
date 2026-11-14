#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
Tests for per-request outcome recording.

Everything here is hermetic:
  * a local threaded HTTP server serves self-made pages (5xx once, 404,
    a parent page linking to a child page);
  * a replaceable fake download function (Request._fetch) scripts
    responses and exceptions without touching any socket;
  * the connection-error case hits a freshly closed local port.

No external network is ever used.
"""
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socket import socket, AF_INET, SOCK_STREAM
from urllib.parse import urljoin

import requests
from lxml import etree

from talospider import Request, Spider
from talospider.downloader import RequestOutcome


# ---------------------------------------------------------------------------
# Local self-made HTTP server
# ---------------------------------------------------------------------------

class _ServerState(object):
    """Shared, resettable state for the test HTTP server."""

    def __init__(self):
        self.lock = threading.Lock()
        self.flaky_hits = 0

    def reset(self):
        with self.lock:
            self.flaky_hits = 0


STATE = _ServerState()


class _LocalHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        # Keep the test output clean.
        return

    def do_GET(self):
        path = self.path
        if path.startswith('/flaky'):
            with STATE.lock:
                STATE.flaky_hits += 1
                hit = STATE.flaky_hits
            if hit == 1:
                self._send(500, b'internal server error')
            else:
                self._send(200, b'<html><body>flaky ok</body></html>')
        elif path.startswith('/not-found'):
            self._send(404, b'page not found')
        elif path.startswith('/parent'):
            body = ('<html><body><a href="/child">child link</a>'
                    '</body></html>').encode('utf-8')
            self._send(200, body)
        elif path.startswith('/child'):
            self._send(200, b'<html><body>child page</body></html>')
        elif path.startswith('/redirect'):
            self.send_response(302)
            self.send_header('Location', '/child')
            self.send_header('Content-Length', '0')
            self.end_headers()
        else:
            self._send(404, b'unknown path')

    def _send(self, status, body):
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _start_server():
    server = ThreadingHTTPServer(('127.0.0.1', 0), _LocalHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _closed_local_url():
    """A http URL on a port that is guaranteed closed right now."""
    sock = socket(AF_INET, SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return 'http://127.0.0.1:%d/' % port


# ---------------------------------------------------------------------------
# Replaceable fake download function
# ---------------------------------------------------------------------------

class FakeResponse(object):
    def __init__(self, status_code=200, content=b'<html>ok</html>',
                 json_data=None, json_error=None):
        self.status_code = status_code
        self.content = content
        self.raw = object()
        self._json_data = json_data
        self._json_error = json_error

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                '%s server error' % self.status_code, response=self)

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._json_data


def make_fake_fetch(script):
    """
    Build a replacement for ``Request._fetch``.

    ``script`` is a list consumed one item per call: a FakeResponse is
    returned, an Exception instance is raised.
    """
    calls = []

    def _fake_fetch(self, url, method, headers, proxies, params, data,
                    cookies, verify, **kwargs):
        calls.append({'url': url, 'method': method})
        item = script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    _fake_fetch.calls = calls
    return _fake_fetch


# ---------------------------------------------------------------------------
# Spider subclasses (top-level so they remain picklable for the pool on
# Windows' spawn-based multiprocessing)
# ---------------------------------------------------------------------------

class _OutcomeSpiderMixin(object):
    request_config = {'RETRIES': 3, 'DELAY': 0, 'TIMEOUT': 3}
    start_urls = []

    def parse(self, res):
        if res is None:
            return
        tree = etree.HTML(res.html)
        for href in tree.xpath('//a/@href'):
            yield Request(urljoin(res.url, href),
                          request_config=self.request_config,
                          callback=self.parse_child)

    def parse_child(self, res):
        return None


class SingleProcessOutcomeSpider(_OutcomeSpiderMixin, Spider):
    set_mul = False


class MultiProcessOutcomeSpider(_OutcomeSpiderMixin, Spider):
    set_mul = True
    pool_size = 2


# ---------------------------------------------------------------------------
# Request-level tests
# ---------------------------------------------------------------------------

class TestRequestOutcomes(unittest.TestCase):
    def setUp(self):
        STATE.reset()

    def _run(self, script, **request_kwargs):
        req = Request('http://example.test/path',
                      request_config={'RETRIES': 3, 'DELAY': 0, 'TIMEOUT': 3},
                      **request_kwargs)
        original_fetch = Request._fetch
        Request._fetch = make_fake_fetch(script)
        try:
            req()
        finally:
            Request._fetch = original_fetch
        self.assertIsInstance(req.outcome, RequestOutcome)
        return req.outcome

    def test_5xx_then_success_records_two_attempts(self):
        outcome = self._run([FakeResponse(500), FakeResponse(200)])
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.attempts, 2)
        self.assertEqual(outcome.status_code, 200)
        self.assertIsNone(outcome.error_type)
        self.assertEqual(outcome.url, 'http://example.test/path')
        self.assertEqual(outcome.method, 'GET')
        self.assertIsNotNone(outcome.elapsed)
        self.assertGreaterEqual(outcome.elapsed, 0)

    def test_post_method_is_recorded(self):
        outcome = self._run([FakeResponse(200)], method='POST',
                            data={'k': 'v'})
        self.assertEqual(outcome.method, 'POST')
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.attempts, 1)

    def test_4xx_is_not_retried_and_is_distinguishable(self):
        outcome = self._run([FakeResponse(404)])
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.attempts, 1)
        self.assertEqual(outcome.status_code, 404)
        self.assertEqual(outcome.error_type, 'http_4xx')

    def test_connection_error_is_not_retried_and_is_distinguishable(self):
        outcome = self._run([
            requests.exceptions.ConnectionError('boom')
        ])
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.attempts, 1)
        self.assertIsNone(outcome.status_code)
        self.assertEqual(outcome.error_type, 'connection_error')

    def test_decode_failure_leaves_its_own_outcome(self):
        import talospider.downloader as downloader_module
        original_detect = downloader_module.cchardet.detect
        downloader_module.cchardet.detect = lambda content: {'encoding': 'utf-8'}
        try:
            # 0xff cannot be decoded as utf-8, so the text branch fails.
            outcome = self._run([FakeResponse(200, content=b'\xff\xfe')])
        finally:
            downloader_module.cchardet.detect = original_detect
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.attempts, 1)
        self.assertEqual(outcome.status_code, 200)
        self.assertEqual(outcome.error_type, 'decode_error')

    def test_5xx_retries_are_exhausted_and_counted(self):
        outcome = self._run([
            FakeResponse(500), FakeResponse(500),
            FakeResponse(500), FakeResponse(500)
        ])
        self.assertFalse(outcome.success)
        # RETRIES=3 -> one initial attempt plus three retries.
        self.assertEqual(outcome.attempts, 4)
        self.assertEqual(outcome.status_code, 500)
        self.assertEqual(outcome.error_type, 'http_5xx')

    def test_missing_schema_is_distinguishable(self):
        outcome = self._run([
            requests.exceptions.MissingSchema('no schema')
        ])
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.attempts, 1)
        self.assertEqual(outcome.error_type, 'missing_schema')

    def test_timeout_is_distinguishable_from_connection_error(self):
        outcome = self._run([
            requests.exceptions.Timeout('timed out')
        ])
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.attempts, 1)
        self.assertIsNone(outcome.status_code)
        self.assertEqual(outcome.error_type, 'request_error')

    def test_json_parse_failure_is_classified_as_decode_error(self):
        outcome = self._run([
            FakeResponse(200, json_error=ValueError('bad json'))
        ], file_type='json')
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.attempts, 1)
        self.assertEqual(outcome.status_code, 200)
        self.assertEqual(outcome.error_type, 'decode_error')

    def test_outcome_is_picklable_for_process_boundaries(self):
        import pickle
        outcome = self._run([FakeResponse(500), FakeResponse(200)])
        restored = pickle.loads(pickle.dumps(outcome))
        self.assertEqual(restored, outcome)
        self.assertEqual(restored.as_dict(), outcome.as_dict())


# ---------------------------------------------------------------------------
# Spider-level tests against the local server
# ---------------------------------------------------------------------------

class TestSpiderOutcomes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _start_server()
        cls.base_url = 'http://127.0.0.1:%d' % cls.server.server_address[1]
        cls.refused_url = _closed_local_url()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        STATE.reset()

    def _start_urls(self):
        return [
            self.base_url + '/flaky',
            self.base_url + '/not-found',
            self.refused_url,
            self.base_url + '/parent',
        ]

    def _expected_index(self):
        return {
            self.base_url + '/flaky': {
                'success': True, 'attempts': 2,
                'status_code': 200, 'error_type': None,
            },
            self.base_url + '/not-found': {
                'success': False, 'attempts': 1,
                'status_code': 404, 'error_type': 'http_4xx',
            },
            self.refused_url: {
                'success': False, 'attempts': 1,
                'status_code': None, 'error_type': 'connection_error',
            },
            self.base_url + '/parent': {
                'success': True, 'attempts': 1,
                'status_code': 200, 'error_type': None,
            },
            self.base_url + '/child': {
                'success': True, 'attempts': 1,
                'status_code': 200, 'error_type': None,
            },
        }

    def _assert_outcome_matches(self, outcome, expected):
        self.assertIsInstance(outcome, RequestOutcome)
        self.assertEqual(outcome.method, 'GET')
        self.assertEqual(outcome.success, expected['success'])
        self.assertEqual(outcome.attempts, expected['attempts'])
        self.assertEqual(outcome.status_code, expected['status_code'])
        self.assertEqual(outcome.error_type, expected['error_type'])
        self.assertIsNotNone(outcome.elapsed)
        self.assertGreaterEqual(outcome.elapsed, 0)

    def test_single_process_collects_every_outcome_in_order(self):
        SingleProcessOutcomeSpider.start_urls = self._start_urls()
        outcomes = SingleProcessOutcomeSpider.start()

        self.assertIs(outcomes, SingleProcessOutcomeSpider.request_outcomes)
        # flaky, not-found, refused, parent, then the child Request yielded
        # inside parse.
        self.assertEqual(len(outcomes), 5)
        expected = self._expected_index()
        # Single-process execution is depth-first, so completion order is
        # the start_urls order with the child right after its parent.
        ordered_urls = [o.url for o in outcomes]
        self.assertEqual(ordered_urls, [
            self.base_url + '/flaky',
            self.base_url + '/not-found',
            self.refused_url,
            self.base_url + '/parent',
            self.base_url + '/child',
        ])
        for outcome in outcomes:
            self._assert_outcome_matches(outcome, expected[outcome.url])

    def test_multi_process_brings_child_outcomes_back(self):
        MultiProcessOutcomeSpider.start_urls = self._start_urls()
        outcomes = MultiProcessOutcomeSpider.start()

        self.assertIs(outcomes, MultiProcessOutcomeSpider.request_outcomes)
        # The same five requests, none lost at the process boundary.
        self.assertEqual(len(outcomes), 5)
        expected = self._expected_index()
        seen_urls = set()
        for outcome in outcomes:
            self.assertIn(outcome.url, expected)
            self.assertNotIn(outcome.url, seen_urls)
            seen_urls.add(outcome.url)
            self._assert_outcome_matches(outcome, expected[outcome.url])
        self.assertEqual(seen_urls, set(expected.keys()))

    def test_each_run_resets_the_outcome_list(self):
        SingleProcessOutcomeSpider.start_urls = [self.base_url + '/child']
        first = SingleProcessOutcomeSpider.start()
        self.assertEqual(len(first), 1)
        second = SingleProcessOutcomeSpider.start()
        self.assertEqual(len(second), 1)
        self.assertIsNot(first, second)
        self.assertEqual(len(SingleProcessOutcomeSpider.request_outcomes), 1)

    def test_redirect_records_the_final_url(self):
        req = Request(self.base_url + '/redirect',
                      request_config={'RETRIES': 3, 'DELAY': 0, 'TIMEOUT': 3})
        req()
        self.assertTrue(req.outcome.success)
        self.assertEqual(req.outcome.url, self.base_url + '/child')
        self.assertEqual(req.outcome.attempts, 1)
        self.assertEqual(req.outcome.status_code, 200)


if __name__ == '__main__':
    unittest.main()
