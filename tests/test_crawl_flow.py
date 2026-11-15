"""Acceptance tests for the crawl-failure-reporting rework.

Uses only local resources:
- a local HTTP origin server serving a normal page, a 500 page and links
  to an unreachable address;
- a controllable fake proxy (a local forwarding proxy + dead ports).

Run from the repo root:
    python tests/test_crawl_flow.py
"""
import http.client
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
WEBSCRAPER_DIR = os.path.join(REPO_ROOT, "webscraper")
sys.path.insert(0, WEBSCRAPER_DIR)

import config  # noqa: E402
from utils.proxy_utils import proxy as proxy_module  # noqa: E402
from utils.request_client import ReqestClient, RequestFailure  # noqa: E402
import webscraper  # noqa: E402
from webscraper import Websitescrap  # noqa: E402


def get_free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# --------------------------------------------------------------------------
# local test servers
# --------------------------------------------------------------------------
class OriginHandler(BaseHTTPRequestHandler):
    """Serves a normal page, a 500 page and a 404 for anything else."""
    pages = {}
    requests_seen = {}

    def do_GET(self):
        OriginHandler.requests_seen[self.path] = \
            OriginHandler.requests_seen.get(self.path, 0) + 1
        if self.path == "/error":
            status, body = 500, b"<html><body>boom</body></html>"
        elif self.path in OriginHandler.pages:
            status, body = 200, OriginHandler.pages[self.path].encode()
        else:
            status, body = 404, b"not found"
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class ForwardProxyHandler(BaseHTTPRequestHandler):
    """Minimal controllable HTTP forward proxy used as the 'good' proxy."""
    requests_seen = 0

    def do_GET(self):
        ForwardProxyHandler.requests_seen += 1
        parts = urlsplit(self.path)  # absolute URI for proxied requests
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        try:
            conn = http.client.HTTPConnection(parts.hostname, parts.port or 80,
                                              timeout=10)
            conn.request("GET", path, headers={
                "User-Agent": self.headers.get("User-Agent", "test-proxy")})
            resp = conn.getresponse()
            body = resp.read()
            status = resp.status
            conn.close()
        except Exception:
            status, body = 502, b"bad gateway"
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def start_server(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# --------------------------------------------------------------------------
# crawl flow tests (non-proxy mode)
# --------------------------------------------------------------------------
class CrawlFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dead_port = get_free_port()  # closed -> unreachable address
        cls.origin = start_server(OriginHandler)
        cls.port = cls.origin.server_address[1]
        cls.base = "http://127.0.0.1:{}".format(cls.port)
        # userinfo trick keeps the unreachable URL "local" for the link
        # filter while requests still connects to the dead port
        cls.unreachable = "http://127.0.0.1:{}@127.0.0.1:{}/gone".format(
            cls.port, cls.dead_port)
        OriginHandler.pages = {
            "/": (
                '<html><body>'
                '<a href="/page1">p1</a>'
                '<a href="/page1">p1 again</a>'
                '<a href="/error">err</a>'
                '<a href="{unreachable}">gone</a>'
                '<a href="http://example.com/foreign">foreign</a>'
                '<a href="/image.png">img</a>'
                '</body></html>'
            ).format(unreachable=cls.unreachable),
            "/page1": (
                '<html><body>'
                '<a href="/">home</a>'
                '<a href="/error">err again</a>'
                '<a href="/page1">self</a>'
                '</body></html>'
            ),
        }
        cls._old_data_dir = config.DATA_DIR
        cls._tmp = tempfile.TemporaryDirectory()
        config.DATA_DIR = cls._tmp.name + "/"

    @classmethod
    def tearDownClass(cls):
        config.DATA_DIR = cls._old_data_dir
        cls._tmp.cleanup()
        cls.origin.shutdown()

    def setUp(self):
        config.USE_PROXY_SERVER = False

    def data_files(self):
        return os.listdir(config.DATA_DIR)

    def test_crawl_records_outcomes_and_writes_only_successes(self):
        OriginHandler.requests_seen = {}
        scraper = Websitescrap(self.base + "/", start_afresh=True)
        summary = scraper.crawl(0, 0)

        ok_urls = {self.base + "/", self.base + "/page1"}
        err_url = self.base + "/error"

        # successes and failures are accounted for
        self.assertEqual(set(summary["succeeded"]), ok_urls)
        self.assertEqual(summary["success_count"], 2)
        self.assertEqual(summary["failure_count"], 2)
        self.assertEqual(set(summary["failed"]), {err_url, self.unreachable})

        # failure reasons are identifiable
        self.assertIn("500", summary["failed"][err_url])
        self.assertIn("request failed", summary["failed"][self.unreachable])

        # each link crawled exactly once despite duplicate links
        for path in ("/", "/page1", "/error"):
            self.assertEqual(OriginHandler.requests_seen.get(path), 1,
                             "{} crawled more than once".format(path))

        # only successful pages were written to the data dir
        expected = {
            str(uuid.uuid3(uuid.NAMESPACE_URL, str(u))) + ".json"
            for u in ok_urls
        }
        self.assertEqual(set(self.data_files()), expected)
        known_bodies = set(OriginHandler.pages.values())
        for f in self.data_files():
            with open(os.path.join(config.DATA_DIR, f)) as fp:
                content = json.load(fp)
            self.assertEqual(len(content), 1)
            self.assertIn(list(content.values())[0], known_bodies)

        # foreign / media / unreachable links were never requested
        self.assertNotIn("/image.png", OriginHandler.requests_seen)
        self.assertNotIn("/gone", OriginHandler.requests_seen)

    def test_crawl_with_empty_proxy_list_gives_clear_outcome(self):
        """Proxy mode with no usable proxy: explicit failures, no exception."""
        config.USE_PROXY_SERVER = True
        saved_list = webscraper.request_client.proxy_list
        webscraper.request_client.proxy_list = []
        files_before = set(self.data_files())
        try:
            scraper = Websitescrap(self.base + "/", start_afresh=True)
            summary = scraper.crawl(0, 0)
        finally:
            webscraper.request_client.proxy_list = saved_list
            config.USE_PROXY_SERVER = False

        self.assertEqual(summary["success_count"], 0)
        self.assertGreaterEqual(summary["failure_count"], 1)
        for reason in summary["failed"].values():
            self.assertIn("no usable proxy", reason)
        # no new page was written to the data dir
        self.assertEqual(set(self.data_files()), files_before)


# --------------------------------------------------------------------------
# request client proxy retry / eviction tests
# --------------------------------------------------------------------------
class ProxyRetryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.origin = start_server(OriginHandler)
        cls.port = cls.origin.server_address[1]
        cls.ok_url = "http://127.0.0.1:{}/proxied".format(cls.port)
        cls.proxy_server = start_server(ForwardProxyHandler)
        cls.good_proxy = "127.0.0.1:{}".format(
            cls.proxy_server.server_address[1])
        cls.bad_proxy = "127.0.0.1:{}".format(get_free_port())
        cls.bad_proxy2 = "127.0.0.1:{}".format(get_free_port())

    @classmethod
    def tearDownClass(cls):
        cls.origin.shutdown()
        cls.proxy_server.shutdown()

    def setUp(self):
        config.USE_PROXY_SERVER = True
        # keep the repo's proxy_list.json untouched: point persistence at a
        # temp file seeded with our fake proxies
        self._old_file = proxy_module.PROXY_LIST_FILE
        self._tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False)
        json.dump([self.bad_proxy, self.good_proxy], self._tmp)
        self._tmp.close()
        proxy_module.PROXY_LIST_FILE = self._tmp.name

    def tearDown(self):
        proxy_module.PROXY_LIST_FILE = self._old_file
        os.unlink(self._tmp.name)
        config.USE_PROXY_SERVER = False

    def make_client(self, proxies):
        client = ReqestClient()
        client.proxy_list = list(proxies)
        client.proxy = None
        client.count = 0
        return client

    def test_bad_proxy_evicted_and_retried_with_another(self):
        OriginHandler.pages["/proxied"] = "<html><body>via proxy</body></html>"
        ForwardProxyHandler.requests_seen = 0
        client = self.make_client([self.bad_proxy, self.good_proxy])
        # make the first attempt deterministically use the bad proxy
        client.proxy = self.bad_proxy
        client.count = 1

        response = client.request_with_proxy_header(self.ok_url)

        self.assertIsNotNone(response)
        self.assertNotIsInstance(response, RequestFailure)
        self.assertEqual(response.status_code, 200)
        # the bad proxy was evicted, the good one kept and persisted
        self.assertNotIn(self.bad_proxy, client.proxy_list)
        self.assertIn(self.good_proxy, client.proxy_list)
        with open(self._tmp.name) as fp:
            self.assertEqual(json.load(fp), [self.good_proxy])
        # the request really went through the good proxy
        self.assertEqual(ForwardProxyHandler.requests_seen, 1)

    def test_all_proxies_dead_returns_identifiable_failure(self):
        client = self.make_client([self.bad_proxy, self.bad_proxy2])

        result = client.request_with_proxy_header(self.ok_url)

        self.assertIsInstance(result, RequestFailure)
        self.assertFalse(result)  # falsy, but not a bare None
        self.assertIsNone(result.status_code)
        self.assertIn("failed", result.reason)
        # both dead proxies were evicted
        self.assertEqual(client.proxy_list, [])

    def test_empty_proxy_list_returns_failure_not_exception(self):
        client = self.make_client([])
        result = client.request_with_proxy_header(self.ok_url)
        self.assertIsInstance(result, RequestFailure)
        self.assertIn("no usable proxy", result.reason)
        # generate_proxy itself must not raise on an empty list either
        self.assertIsNone(client.generate_proxy())

    def test_non_proxy_mode_keeps_original_behavior(self):
        config.USE_PROXY_SERVER = False
        client = self.make_client([])
        # unreachable address -> silent None, as before
        self.assertIsNone(client.request_with_proxy_header(
            "http://127.0.0.1:{}/x".format(get_free_port())))
        # reachable -> response
        OriginHandler.pages["/proxied"] = "<html><body>direct</body></html>"
        resp = client.request_with_proxy_header(self.ok_url)
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
