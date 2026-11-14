"""End-to-end acceptance checks for the batch pipeline (local, self-contained).

Uses a local HTTP server for the raw EML bodies and faked inbox fetchers; no
real reporting database, no heavy parsing stack.
"""

import socket
import sys
import types

import pytest
import requests

from crawlerbox import analyzer
from crawlerbox.analyzer import OutcomeStatus, analyze, format_report
from crawlerbox.personalized_config import INBOX_ID_KEY, INBOX_RAW_URL_KEY


EML_A = (
    b'From: a@example.com\r\nTo: b@example.com\r\nSubject: test A\r\n'
    b'Date: Mon, 13 Oct 2025 10:00:00 +0000\r\nMessage-ID: <a@example.com>\r\n'
    b'Content-Type: text/plain\r\n\r\nhello\r\n'
)
EML_B = (
    b'From: c@example.net\r\nTo: b@example.com\r\nSubject: test B\r\n'
    b'Date: Mon, 13 Oct 2025 11:00:00 +0000\r\nMessage-ID: <b@example.net>\r\n'
    b'Content-Type: text/html\r\n\r\n<html><body>hi</body></html>\r\n'
)


class LocalServer:
    """Tiny threaded HTTP server serving fixed raw bodies."""

    def __init__(self):
        self.routes = {}
        self._server = None
        self._thread = None
        self.base_url = None

    def add_raw(self, path, body, status=200):
        self.routes[path] = (status, body)

    def start(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading

        routes = self.routes

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def do_GET(self):
                status, body = routes.get(self.path, (404, b'not found'))
                self.send_response(status)
                self.send_header('Content-Type', 'message/rfc822')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        host, port = self._server.server_address
        self.base_url = f'http://{host}:{port}'
        return self

    def url(self, path):
        return self.base_url + path

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._thread.join(timeout=5)
            self._server = None


@pytest.fixture
def server():
    srv = LocalServer().start()
    srv.add_raw('/a', EML_A)
    srv.add_raw('/b', EML_B)
    srv.add_raw('/empty', b'')
    srv.add_raw('/error', b'boom', status=500)
    try:
        yield srv
    finally:
        srv.stop()


def dead_url():
    probe = socket.socket()
    probe.bind(('127.0.0.1', 0))
    port = probe.getsockname()[1]
    probe.close()
    return f'http://127.0.0.1:{port}/eml'


def test_analyzer_forwards_the_inbox_phish_id_to_the_real_parser_entry(monkeypatch):
    """`crawlerbox.analyzer` must call `phishparser.parse_data(phish_id, bytes)`."""
    received = []

    fake_parser_module = types.ModuleType('crawlerbox.phishparser')

    def parse_data(phish_id, rawemail_inbytes):
        received.append((phish_id, rawemail_inbytes))

    fake_parser_module.parse_data = parse_data
    monkeypatch.setitem(sys.modules, 'crawlerbox.phishparser', fake_parser_module)

    analyzer.parse_data('the-id', b'raw-bytes')
    assert received == [('the-id', b'raw-bytes')]


def test_mixed_batch_cli_report(server, monkeypatch, capsys):
    import crawlerbox.__main__ as entry

    inbox = [
        {INBOX_ID_KEY: 'one', INBOX_RAW_URL_KEY: server.url('/a')},
        {INBOX_ID_KEY: 'not-found', INBOX_RAW_URL_KEY: server.url('/missing')},
        {INBOX_ID_KEY: 'refused', INBOX_RAW_URL_KEY: dead_url()},
        {INBOX_ID_KEY: 'empty-body', INBOX_RAW_URL_KEY: server.url('/empty')},
        {INBOX_ID_KEY: 'server-error', INBOX_RAW_URL_KEY: server.url('/error')},
        {INBOX_ID_KEY: 'two', INBOX_RAW_URL_KEY: server.url('/b')},
        {INBOX_ID_KEY: 'one', INBOX_RAW_URL_KEY: server.url('/a')},
    ]

    parsed = []

    def fake_parser(phish_id, raw_bytes):
        parsed.append(phish_id)

    real_analyze = analyze

    def analyze_with_fake_parser(inbox_, timeout=None):
        return real_analyze(inbox_, timeout=timeout or 5, parser=fake_parser)

    monkeypatch.setattr(entry, 'fetch_new_emails_by_id', lambda id: inbox)
    monkeypatch.setattr(entry, 'analyze', analyze_with_fake_parser)

    entry.main(['-id', 'batch'])

    out = capsys.readouterr().out
    assert 'phish_id=one status=success' in out
    assert 'phish_id=not-found status=download_failed' in out and 'HTTP 404' in out
    assert 'phish_id=refused status=download_failed' in out and 'connection error' in out
    assert 'phish_id=empty-body status=download_failed' in out and 'empty response body' in out
    assert 'phish_id=server-error status=download_failed' in out and 'HTTP 500' in out
    assert 'phish_id=two status=success' in out
    assert 'phish_id=one status=duplicate_skipped' in out
    assert 'duplicate' in out
    # Only the two downloadable, non-duplicate emails were parsed.
    assert parsed == ['one', 'two']


def test_outcome_list_matches_the_cli_report(server):
    inbox = [
        {INBOX_ID_KEY: 'one', INBOX_RAW_URL_KEY: server.url('/a')},
        {INBOX_ID_KEY: 'bad', INBOX_RAW_URL_KEY: server.url('/error')},
        {INBOX_ID_KEY: 'one', INBOX_RAW_URL_KEY: server.url('/a')},
    ]
    outcomes = analyze(inbox, timeout=5, parser=lambda phish_id, raw: True)

    assert [o.status for o in outcomes] == [
        OutcomeStatus.SUCCESS,
        OutcomeStatus.DOWNLOAD_FAILED,
        OutcomeStatus.DUPLICATE_SKIPPED,
    ]
    report = format_report(outcomes)
    for outcome, position in zip(outcomes, range(1, 4)):
        assert f'[{position}] phish_id={outcome.phish_id} status={outcome.status}' in report
    assert '1 succeeded' in report and '1 failed to download' in report and '1 duplicate(s) skipped' in report


def test_one_slow_download_does_not_block_the_batch(server, monkeypatch):
    """A download that never returns must hit its timeout and let the batch finish."""
    server.add_raw('/slow', b'slow')
    real_get = requests.get
    calls = {'slow': 0}

    def fake_get(url, **kwargs):
        if url == server.url('/slow'):
            calls['slow'] += 1
            raise requests.Timeout('read timed out')
        assert kwargs.get('timeout'), 'download must always be given a finite timeout'
        return real_get(url, **kwargs)

    monkeypatch.setattr(analyzer.requests, 'get', fake_get)
    inbox = [
        {INBOX_ID_KEY: 'slow', INBOX_RAW_URL_KEY: server.url('/slow')},
        {INBOX_ID_KEY: 'fine', INBOX_RAW_URL_KEY: server.url('/a')},
    ]
    outcomes = analyze(inbox, timeout=1, parser=lambda phish_id, raw: True)
    assert [o.status for o in outcomes] == [OutcomeStatus.DOWNLOAD_FAILED, OutcomeStatus.SUCCESS]
    assert 'timed out' in outcomes[0].reason
    assert calls['slow'] == 1
