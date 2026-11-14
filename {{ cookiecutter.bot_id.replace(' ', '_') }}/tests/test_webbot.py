"""Offline tests for the configurable site crawler in webbot.py.

These tests never start a real browser. The crawl loop is exercised through a
small urllib-based page loader against a local HTTP server that simulates
normal pages, a page that times out and a page that fails with an error.
"""

import csv
import importlib.util
import threading
import time
import urllib.request
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urljoin

import pytest

# In the cookiecutter template this directory name is rendered by cookiecutter;
# in the raw template checkout it matches the literal directory name on disk.
PACKAGE_DIR = Path(__file__).resolve().parents[1] / "{{ cookiecutter.bot_id.replace(' ', '_') }}"
WEBBOT_PATH = PACKAGE_DIR / "webbot.py"
if not WEBBOT_PATH.exists():
    # On project generation webbot.py is renamed to bot.py (see hooks/).
    WEBBOT_PATH = PACKAGE_DIR / "bot.py"

spec = importlib.util.spec_from_file_location("webbot", WEBBOT_PATH)
webbot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(webbot)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _LinkParser(HTMLParser):
    def __init__(self, base_url):
        super().__init__()
        self._base_url = base_url
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value:
                self.links.append(urljoin(self._base_url, value))


class HttpPageLoader:
    """Minimal page loader (open_page/get_title/get_links) over urllib."""

    def __init__(self, timeout=0.5):
        self._timeout = timeout
        self._url = None
        self._html = ""

    def open_page(self, url):
        with urllib.request.urlopen(url, timeout=self._timeout) as response:
            self._html = response.read().decode("utf-8", "replace")
        self._url = url

    def get_title(self):
        parser = _TitleParser()
        parser.feed(self._html)
        return parser.title

    def get_links(self):
        parser = _LinkParser(self._url)
        parser.feed(self._html)
        return parser.links


class _TitleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._in_title = False
        self.title = ""

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def read_rows(csv_path):
    with open(csv_path, "r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ---------------------------------------------------------------------------
# Local HTTP server simulating normal, slow and broken pages
# ---------------------------------------------------------------------------

PAGE_ONE = (
    "<html><head><title>Page One</title></head><body>"
    "<a href='/two'>two</a>"
    "<a href='/slow'>slow</a>"
    "<a href='/boom'>boom</a>"
    "</body></html>"
)
PAGE_TWO = (
    "<html><head><title>Page Two</title></head><body>"
    "<a href='/one'>back to one</a>"
    "</body></html>"
)


class _CrawlHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/one":
            self._respond(200, PAGE_ONE)
        elif self.path == "/two":
            self._respond(200, PAGE_TWO)
        elif self.path == "/slow":
            # Longer than the loader timeout: simulates a page load timeout.
            time.sleep(3)
            self._respond(200, "<html><head><title>Slow</title></head></html>")
        elif self.path == "/boom":
            self._respond(500, "Internal Server Error")
        else:
            self._respond(404, "Not Found")

    def _respond(self, status, body):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture()
def http_server():
    class QuietServer(ThreadingHTTPServer):
        # Client aborts are expected when simulating page load timeouts.
        def handle_error(self, request, client_address):
            pass

    server = QuietServer(("127.0.0.1", 0), _CrawlHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:%d" % server.server_address[1]
    server.shutdown()
    server.server_close()


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------

class TestConfigValidation:
    def test_missing_start_url_fails(self):
        with pytest.raises(webbot.ConfigError, match=webbot.ENV_START_URL):
            webbot.load_config({webbot.ENV_MAX_PAGES: "5"})

    def test_blank_start_url_fails(self):
        with pytest.raises(webbot.ConfigError, match=webbot.ENV_START_URL):
            webbot.load_config(
                {webbot.ENV_START_URL: "   ", webbot.ENV_MAX_PAGES: "5"}
            )

    @pytest.mark.parametrize("value", ["", "0", "-3", "abc", "1.5", "  "])
    def test_invalid_max_pages_fails(self, value):
        env = {webbot.ENV_START_URL: "http://example.com"}
        if value.strip():
            env[webbot.ENV_MAX_PAGES] = value
        with pytest.raises(webbot.ConfigError, match=webbot.ENV_MAX_PAGES):
            webbot.load_config(env)

    def test_valid_config(self, tmp_path):
        env = {
            webbot.ENV_START_URL: "http://example.com",
            webbot.ENV_MAX_PAGES: "7",
            webbot.ENV_OUTPUT_CSV: str(tmp_path / "out.csv"),
            webbot.ENV_PAGE_TIMEOUT: "5",
        }
        config = webbot.load_config(env)
        assert config.start_url == "http://example.com"
        assert config.max_pages == 7
        assert config.output_csv == str(tmp_path / "out.csv")
        assert config.page_timeout == 5

    def test_action_exits_before_starting_browser(self, monkeypatch):
        for var in (
            webbot.ENV_START_URL,
            webbot.ENV_MAX_PAGES,
            webbot.ENV_OUTPUT_CSV,
            webbot.ENV_PAGE_TIMEOUT,
        ):
            monkeypatch.delenv(var, raising=False)
        bot = webbot.Bot()
        with pytest.raises(SystemExit):
            bot.action()
        # The browser must never have been started.
        assert bot.driver is None


# ---------------------------------------------------------------------------
# Crawl behaviour against the local HTTP server
# ---------------------------------------------------------------------------

class TestCrawl:
    def test_crawl_records_successes_and_failures(self, http_server, tmp_path):
        csv_path = tmp_path / "results.csv"
        with webbot.CSVRecorder(str(csv_path)) as recorder:
            crawler = webbot.SiteCrawler(
                HttpPageLoader(), recorder, http_server + "/one", max_pages=10
            )
            success, failed = crawler.run()

        assert success == 2
        assert failed == 2

        rows = read_rows(csv_path)
        assert len(rows) == 4
        assert list(rows[0].keys()) == ["url", "title", "links", "status"]

        by_url = {row["url"]: row for row in rows}

        one = by_url[http_server + "/one"]
        assert one["title"] == "Page One"
        assert one["status"] == "success"
        assert http_server + "/two" in one["links"]
        assert http_server + "/slow" in one["links"]
        assert http_server + "/boom" in one["links"]

        two = by_url[http_server + "/two"]
        assert two["title"] == "Page Two"
        assert two["status"] == "success"

        # Timeout and error pages are recorded with the failure reason.
        slow = by_url[http_server + "/slow"]
        assert slow["status"].startswith("failed:")
        assert len(slow["status"]) > len("failed:")

        boom = by_url[http_server + "/boom"]
        assert boom["status"].startswith("failed:")
        assert "500" in boom["status"]

    def test_rerun_does_not_duplicate_completed_pages(self, http_server, tmp_path):
        csv_path = tmp_path / "results.csv"

        # First run stops after two pages, simulating an interrupted crawl.
        with webbot.CSVRecorder(str(csv_path)) as recorder:
            crawler = webbot.SiteCrawler(
                HttpPageLoader(), recorder, http_server + "/one", max_pages=2
            )
            success, failed = crawler.run()
        assert (success, failed) == (2, 0)
        assert len(read_rows(csv_path)) == 2

        # Second run resumes and only processes the remaining pages.
        with webbot.CSVRecorder(str(csv_path)) as recorder:
            crawler = webbot.SiteCrawler(
                HttpPageLoader(), recorder, http_server + "/one", max_pages=10
            )
            success, failed = crawler.run()
        assert (success, failed) == (0, 2)

        rows = read_rows(csv_path)
        assert len(rows) == 4
        urls = [row["url"] for row in rows]
        assert len(urls) == len(set(urls)), "rerun duplicated pages"

        # A third run has nothing left to do.
        with webbot.CSVRecorder(str(csv_path)) as recorder:
            crawler = webbot.SiteCrawler(
                HttpPageLoader(), recorder, http_server + "/one", max_pages=10
            )
            assert crawler.run() == (0, 0)
        assert len(read_rows(csv_path)) == 4

    def test_extraction_error_is_recorded_and_crawl_continues(self, tmp_path):
        class FlakyLoader:
            def __init__(self):
                self._url = None

            def open_page(self, url):
                self._url = url

            def get_title(self):
                return "Title of " + self._url

            def get_links(self):
                if self._url.endswith("/bad"):
                    raise RuntimeError("extraction blew up")
                if self._url.endswith("/start"):
                    return ["http://local/bad", "http://local/good"]
                return []

        csv_path = tmp_path / "results.csv"
        with webbot.CSVRecorder(str(csv_path)) as recorder:
            crawler = webbot.SiteCrawler(
                FlakyLoader(), recorder, "http://local/start", max_pages=10
            )
            success, failed = crawler.run()

        assert (success, failed) == (2, 1)
        rows = read_rows(csv_path)
        by_url = {row["url"]: row for row in rows}
        bad = by_url["http://local/bad"]
        assert bad["status"].startswith("failed:")
        assert "extraction blew up" in bad["status"]
        assert by_url["http://local/good"]["status"] == "success"

    def test_max_pages_limits_the_crawl(self, http_server, tmp_path):
        csv_path = tmp_path / "results.csv"
        with webbot.CSVRecorder(str(csv_path)) as recorder:
            crawler = webbot.SiteCrawler(
                HttpPageLoader(), recorder, http_server + "/one", max_pages=1
            )
            success, failed = crawler.run()
        assert (success, failed) == (1, 0)
        rows = read_rows(csv_path)
        assert [row["url"] for row in rows] == [http_server + "/one"]
