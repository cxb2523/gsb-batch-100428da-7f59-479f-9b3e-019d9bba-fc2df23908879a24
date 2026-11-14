"""Local acceptance checks for the batch email fetch/parse pipeline.

Run from the repository root with ``python -m pytest tests/ -q``.

No dependency on the real reporting database or the heavy parsing stack: a
local HTTP server serves the raw EML bodies and a fake parser records calls.
"""

import socket

import pytest

from crawlerbox import analyzer
from crawlerbox.analyzer import (
    EmailDownloadError,
    EmailOutcome,
    OutcomeStatus,
    analyze,
    count_outcomes,
    download_eml,
    format_report,
)
from crawlerbox.personalized_config import INBOX_ID_KEY, INBOX_RAW_URL_KEY


EML_OK = (
    b'From: attacker@example.com\r\n'
    b'To: victim@example.com\r\n'
    b'Subject: Please review your account\r\n'
    b'Date: Mon, 13 Oct 2025 10:00:00 +0000\r\n'
    b'Message-ID: <ok@example.com>\r\n'
    b'MIME-Version: 1.0\r\n'
    b'Content-Type: text/plain; charset="utf-8"\r\n'
    b'\r\n'
    b'Click http://phish.example/login to verify.\r\n'
)

EML_SECOND = (
    b'From: other@example.net\r\n'
    b'To: victim@example.com\r\n'
    b'Subject: Invoice\r\n'
    b'Date: Mon, 13 Oct 2025 11:00:00 +0000\r\n'
    b'Message-ID: <second@example.net>\r\n'
    b'MIME-Version: 1.0\r\n'
    b'Content-Type: text/html; charset="utf-8"\r\n'
    b'\r\n'
    b'<html><body><a href="http://phish.example/pay">pay</a></body></html>\r\n'
)


class LocalServer:
    def __init__(self):
        self.routes = {}
        self.requested = []
        self._server = None
        self._thread = None
        self.base_url = None

    def add_raw(self, path, body, status=200):
        self.routes[path] = (status, body)

    def start(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading

        routes = self.routes
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def do_GET(self):
                server.requested.append(self.path)
                status, body = routes.get(self.path, (404, b'not found'))
                self.send_response(status)
                self.send_header('Content-Type', 'message/rfc822')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # keep test output quiet
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


def closed_port_url():
    """URL of a port nobody listens on -> connection refused."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(('127.0.0.1', 0))
    port = probe.getsockname()[1]
    probe.close()
    return f'http://127.0.0.1:{port}/eml'


class RecordingParser:
    def __init__(self, fail_for=()):
        self.calls = []
        self.fail_for = set(fail_for)

    def __call__(self, phish_id, raw_bytes):
        self.calls.append((phish_id, raw_bytes))
        if phish_id in self.fail_for:
            raise RuntimeError(f'simulated parser crash for {phish_id}')
        return True

    @property
    def parsed_ids(self):
        return [phish_id for phish_id, _ in self.calls]


@pytest.fixture
def server():
    srv = LocalServer().start()
    srv.add_raw('/eml/one', EML_OK)
    srv.add_raw('/eml/two', EML_SECOND)
    try:
        yield srv
    finally:
        srv.stop()


# ---------------------------------------------------------------- contract ---

def test_inbox_contract_uses_phish_id_key():
    assert INBOX_ID_KEY == 'phish_id'
    assert INBOX_RAW_URL_KEY == 'rawUrl'


def test_legacy_id_key_is_reported_not_silently_ignored(server):
    outcomes = analyze(
        [{'id': 'abc', 'rawUrl': server.url('/eml/one')}],
        parser=RecordingParser(),
        timeout=5,
    )
    assert len(outcomes) == 1
    assert outcomes[0].status == OutcomeStatus.INVALID_ENTRY
    assert 'phish_id' in outcomes[0].reason and 'id' in outcomes[0].reason
    assert server.requested == []


# ------------------------------------------------------------ download -----

def test_download_eml_returns_body(server):
    assert download_eml(server.url('/eml/one'), timeout=5) == EML_OK


@pytest.mark.parametrize(
    'path, expected_fragment',
    [
        ('/eml/missing', 'HTTP 404'),
        ('/eml/error', 'HTTP 500'),
        ('/eml/empty', 'empty response body'),
    ],
)
def test_download_eml_failures(server, path, expected_fragment):
    if path == '/eml/error':
        server.add_raw('/eml/error', b'boom', status=500)
    if path == '/eml/empty':
        server.add_raw('/eml/empty', b'')
    with pytest.raises(EmailDownloadError) as excinfo:
        download_eml(server.url(path), timeout=5)
    assert expected_fragment in str(excinfo.value)


def test_download_eml_connection_failure():
    with pytest.raises(EmailDownloadError) as excinfo:
        download_eml(closed_port_url(), timeout=5)
    assert 'connection error' in str(excinfo.value).lower()


def test_download_eml_uses_finite_timeout(monkeypatch):
    seen = {}

    class FakeResponse:
        status_code = 200
        reason = 'OK'
        content = b'x'

    def fake_get(url, **kwargs):
        seen['timeout'] = kwargs.get('timeout')
        return FakeResponse()

    monkeypatch.setattr(analyzer.requests, 'get', fake_get)
    analyzer.download_eml('http://example.invalid/eml')
    assert seen['timeout'] is not None
    assert isinstance(seen['timeout'], tuple)
    assert all(isinstance(v, (int, float)) and v > 0 for v in seen['timeout'])


# ------------------------------------------------------------- analyze -----

def test_all_success(server):
    parser = RecordingParser()
    inbox = [
        {INBOX_ID_KEY: 'id-1', INBOX_RAW_URL_KEY: server.url('/eml/one')},
        {INBOX_ID_KEY: 'id-2', INBOX_RAW_URL_KEY: server.url('/eml/two')},
    ]
    outcomes = analyze(inbox, parser=parser, timeout=5)

    assert [o.status for o in outcomes] == [OutcomeStatus.SUCCESS, OutcomeStatus.SUCCESS]
    assert [o.phish_id for o in outcomes] == ['id-1', 'id-2']
    assert parser.parsed_ids == ['id-1', 'id-2']
    assert dict(parser.calls)['id-1'] == EML_OK
    assert dict(parser.calls)['id-2'] == EML_SECOND


def test_single_download_failures_do_not_stop_batch(server):
    server.add_raw('/eml/error', b'boom', status=500)
    server.add_raw('/eml/empty', b'')
    parser = RecordingParser()
    inbox = [
        {INBOX_ID_KEY: 'ok-1', INBOX_RAW_URL_KEY: server.url('/eml/one')},
        {INBOX_ID_KEY: 'missing', INBOX_RAW_URL_KEY: server.url('/eml/nope')},
        {INBOX_ID_KEY: 'refused', INBOX_RAW_URL_KEY: closed_port_url()},
        {INBOX_ID_KEY: 'empty', INBOX_RAW_URL_KEY: server.url('/eml/empty')},
        {INBOX_ID_KEY: 'server-err', INBOX_RAW_URL_KEY: server.url('/eml/error')},
        {INBOX_ID_KEY: 'ok-2', INBOX_RAW_URL_KEY: server.url('/eml/two')},
    ]
    outcomes = analyze(inbox, parser=parser, timeout=5)

    assert [o.phish_id for o in outcomes] == [m[INBOX_ID_KEY] for m in inbox]
    assert [o.status for o in outcomes] == [
        OutcomeStatus.SUCCESS,
        OutcomeStatus.DOWNLOAD_FAILED,
        OutcomeStatus.DOWNLOAD_FAILED,
        OutcomeStatus.DOWNLOAD_FAILED,
        OutcomeStatus.DOWNLOAD_FAILED,
        OutcomeStatus.SUCCESS,
    ]
    # Only the two downloadable emails reached the parser.
    assert parser.parsed_ids == ['ok-1', 'ok-2']

    reasons = {o.phish_id: o.reason for o in outcomes}
    assert 'HTTP 404' in reasons['missing']
    assert 'connection error' in reasons['refused'].lower()
    assert 'empty response body' in reasons['empty']
    assert 'HTTP 500' in reasons['server-err']
    assert all(o.reason is None for o in outcomes if o.status == OutcomeStatus.SUCCESS)


def test_duplicates_within_batch_are_parsed_once(server):
    parser = RecordingParser()
    inbox = [
        {INBOX_ID_KEY: 'dup', INBOX_RAW_URL_KEY: server.url('/eml/one')},
        {INBOX_ID_KEY: 'other', INBOX_RAW_URL_KEY: server.url('/eml/two')},
        {INBOX_ID_KEY: 'dup', INBOX_RAW_URL_KEY: server.url('/eml/one')},
    ]
    outcomes = analyze(inbox, parser=parser, timeout=5)

    assert [o.status for o in outcomes] == [
        OutcomeStatus.SUCCESS,
        OutcomeStatus.SUCCESS,
        OutcomeStatus.DUPLICATE_SKIPPED,
    ]
    assert parser.parsed_ids == ['dup', 'other']
    assert outcomes[2].phish_id == 'dup'
    assert 'duplicate' in outcomes[2].reason.lower()
    # The duplicate was never re-downloaded.
    assert server.requested.count('/eml/one') == 1


def test_same_phish_id_with_different_urls_is_still_a_duplicate(server):
    parser = RecordingParser()
    inbox = [
        {INBOX_ID_KEY: 'dup', INBOX_RAW_URL_KEY: server.url('/eml/one')},
        {INBOX_ID_KEY: 'dup', INBOX_RAW_URL_KEY: server.url('/eml/two')},
    ]
    outcomes = analyze(inbox, parser=parser, timeout=5)
    assert [o.status for o in outcomes] == [OutcomeStatus.SUCCESS, OutcomeStatus.DUPLICATE_SKIPPED]
    assert parser.parsed_ids == ['dup']
    assert '/eml/two' not in server.requested


def test_repeated_identifier_after_failed_download_is_skipped(server):
    """Deduplication is keyed on the identifier, whatever the first ending was."""
    parser = RecordingParser()
    missing = server.url('/eml/nope')
    inbox = [
        {INBOX_ID_KEY: 'flaky', INBOX_RAW_URL_KEY: missing},
        {INBOX_ID_KEY: 'flaky', INBOX_RAW_URL_KEY: server.url('/eml/one')},
    ]
    outcomes = analyze(inbox, parser=parser, timeout=5)
    # The repeated identifier is reported as skipped rather than parsed a
    # second time; the failed ending stays visible on the first entry.
    assert [o.status for o in outcomes] == [
        OutcomeStatus.DOWNLOAD_FAILED,
        OutcomeStatus.DUPLICATE_SKIPPED,
    ]
    assert parser.parsed_ids == []


def test_parser_failure_is_contained(server):
    parser = RecordingParser(fail_for={'boom'})
    inbox = [
        {INBOX_ID_KEY: 'boom', INBOX_RAW_URL_KEY: server.url('/eml/one')},
        {INBOX_ID_KEY: 'fine', INBOX_RAW_URL_KEY: server.url('/eml/two')},
    ]
    outcomes = analyze(inbox, parser=parser, timeout=5)
    assert [o.status for o in outcomes] == [OutcomeStatus.PARSE_FAILED, OutcomeStatus.SUCCESS]
    assert 'simulated parser crash' in outcomes[0].reason
    assert parser.parsed_ids == ['boom', 'fine']


def test_parser_returning_false_is_a_failure(server):
    def failing_parser(phish_id, raw_bytes):
        return False

    outcomes = analyze(
        [{INBOX_ID_KEY: 'x', INBOX_RAW_URL_KEY: server.url('/eml/one')}],
        parser=failing_parser,
        timeout=5,
    )
    assert outcomes[0].status == OutcomeStatus.PARSE_FAILED


def test_invalid_entries_are_reported_and_do_not_stop_batch(server):
    parser = RecordingParser()
    inbox = [
        'not-a-mapping',
        {'rawUrl': server.url('/eml/one')},
        {INBOX_ID_KEY: 'no-url'},
        {INBOX_ID_KEY: '', INBOX_RAW_URL_KEY: server.url('/eml/one')},
        {INBOX_ID_KEY: 'ok', INBOX_RAW_URL_KEY: server.url('/eml/one')},
    ]
    outcomes = analyze(inbox, parser=parser, timeout=5)
    assert [o.status for o in outcomes] == [
        OutcomeStatus.INVALID_ENTRY,
        OutcomeStatus.INVALID_ENTRY,
        OutcomeStatus.INVALID_ENTRY,
        OutcomeStatus.INVALID_ENTRY,
        OutcomeStatus.SUCCESS,
    ]
    assert all(o.reason for o in outcomes[:-1])
    assert parser.parsed_ids == ['ok']


def test_empty_and_none_inbox_are_safe():
    parser = RecordingParser()
    assert analyze([], parser=parser) == []
    assert analyze(None, parser=parser) == []
    assert analyze(0, parser=parser) == []
    assert parser.calls == []


def test_outcomes_are_exposed_to_callers(server):
    outcomes = analyze(
        [{INBOX_ID_KEY: 'id-1', INBOX_RAW_URL_KEY: server.url('/eml/one')}],
        parser=RecordingParser(),
        timeout=5,
    )
    outcome = outcomes[0]
    assert isinstance(outcome, EmailOutcome)
    assert outcome.to_dict() == {
        'phish_id': 'id-1',
        'raw_url': server.url('/eml/one'),
        'status': OutcomeStatus.SUCCESS,
        'reason': None,
    }
    assert outcome['phish_id'] == 'id-1'
    assert outcome.get('status') == OutcomeStatus.SUCCESS
    counts = count_outcomes(outcomes)
    assert counts[OutcomeStatus.SUCCESS] == 1
    assert sum(counts.values()) == len(outcomes)
    report = format_report(outcomes)
    assert 'phish_id=id-1' in report and 'status=success' in report


# --------------------------------------------------------------- entries ---

def _run_main(monkeypatch, argv, inbox_by_date=None, inbox_by_id=None):
    from crawlerbox import __main__ as entry

    calls = {'date': [], 'id': []}

    def fake_by_date(date):
        calls['date'].append(date)
        return (inbox_by_date or {}).get(date, [])

    def fake_by_id(id):
        calls['id'].append(id)
        return (inbox_by_id or {}).get(id, [])

    monkeypatch.setattr(entry, 'fetch_new_emails_by_date', fake_by_date)
    monkeypatch.setattr(entry, 'fetch_new_emails_by_id', fake_by_id)
    parsed = RecordingParser()
    monkeypatch.setattr(entry, 'analyze', _wrap_analyze(parsed))
    entry.main(argv)
    return calls, parsed


def _wrap_analyze(parser):
    def wrapped(inbox, timeout=None, parser_=None):
        return analyze(inbox, timeout=timeout or 5, parser=parser)

    return wrapped


def test_entry_point_id_option(server, monkeypatch, capsys):
    inbox = {INBOX_ID_KEY: 'single', INBOX_RAW_URL_KEY: server.url('/eml/one')}
    calls, parsed = _run_main(monkeypatch, ['-id', 'single'], inbox_by_id={'single': [inbox]})
    assert calls['id'] == ['single']
    assert parsed.parsed_ids == ['single']
    assert 'phish_id=single' in capsys.readouterr().out


def test_entry_point_date_option(server, monkeypatch, capsys):
    inbox = [{INBOX_ID_KEY: 'day-1', INBOX_RAW_URL_KEY: server.url('/eml/one')}]
    calls, parsed = _run_main(monkeypatch, ['-d', '2025-01-01'], inbox_by_date={'2025-01-01': inbox})
    assert calls['date'] == ['2025-01-01']
    assert parsed.parsed_ids == ['day-1']
    out = capsys.readouterr().out
    assert 'phish_id=day-1' in out and 'succeeded' in out


def test_scheduler_runs_every_600_seconds(server, monkeypatch):
    from crawlerbox import __main__ as entry

    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            raise KeyboardInterrupt

    runs = []
    monkeypatch.setattr(entry, 'run_crawler', lambda *a, **k: runs.append(1))
    monkeypatch.setattr(entry.time, 'sleep', fake_sleep)
    with pytest.raises(KeyboardInterrupt):
        entry.scheduler()
    assert len(runs) == 2
    assert len(sleeps) == 2
    # Each round is scheduled 600s after the previous round started.
    assert all(590 <= seconds <= 600 for seconds in sleeps)


def test_scheduler_survives_a_failing_round(monkeypatch):
    from crawlerbox import __main__ as entry

    sleeps = []
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise RuntimeError('inbox unavailable')

    def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(entry, 'run_crawler', boom)
    monkeypatch.setattr(entry.time, 'sleep', fake_sleep)
    with pytest.raises(KeyboardInterrupt):
        entry.scheduler()
    assert len(calls) == 2 and len(sleeps) == 2
    assert all(590 <= seconds <= 600 for seconds in sleeps)


def test_scheduler_does_not_wait_after_an_overlong_round(monkeypatch):
    from crawlerbox import __main__ as entry

    clock = {'now': 0.0}
    sleeps = []

    def fake_monotonic():
        return clock['now']

    def slow_round(*a, **k):
        clock['now'] += 700  # round took longer than the interval

    def fake_sleep(seconds):
        sleeps.append(seconds)
        raise KeyboardInterrupt

    monkeypatch.setattr(entry.time, 'monotonic', fake_monotonic)
    monkeypatch.setattr(entry, 'run_crawler', slow_round)
    monkeypatch.setattr(entry.time, 'sleep', fake_sleep)
    with pytest.raises(KeyboardInterrupt):
        entry.scheduler()
    assert sleeps == [0]


def test_default_scheduler_interval_is_600():
    import inspect

    from crawlerbox import __main__ as entry

    assert inspect.signature(entry.scheduler).parameters['interval'].default == 600
