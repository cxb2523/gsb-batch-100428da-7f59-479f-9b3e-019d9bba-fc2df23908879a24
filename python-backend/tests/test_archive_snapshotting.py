"""Acceptance tests for the Wayback snapshot collection chain.

CDX payloads and snapshot responses are injected locally — no external network
access is required or performed.
"""

import os

import requests

import archive_snapshotting as arch
from wayback_fixtures import (
    FakeResponse,
    FakeWaybackSession,
    cdx_payload,
    install_fake_requests,
)

DOMAIN = 'example.com'
FROM_DATE = '20200101'
TO_DATE = '20231231'


def snapshot_files(report_folder):
    folder = os.path.join(report_folder, 'wayback_snapshots')
    return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


def run_batch(wayback_env, monkeypatch, session):
    install_fake_requests(monkeypatch, session)
    return arch.download_snapshot(
        DOMAIN, FROM_DATE, TO_DATE, wayback_env['report_folder']
    )


def test_partial_failure_keeps_going_and_reports_both_sides(wayback_env, monkeypatch):
    rows = [
        ['20200101000000', 'http://example.com/', 'text/html'],
        ['20200102000000', 'http://example.com/', 'text/html'],
        ['20200103000000', 'http://example.com/about.html', 'text/html'],
    ]
    session = FakeWaybackSession(
        cdx_response=FakeResponse(json_payload=cdx_payload(rows)),
        snapshot_responses={
            ('20200101000000', 'http://example.com/'): FakeResponse(
                text='<html><head></head><body>one</body></html>'
            ),
            ('20200102000000', 'http://example.com/'): FakeResponse(status_code=503),
            ('20200103000000', 'http://example.com/about.html'): FakeResponse(
                text='<html><head></head><body>three</body></html>'
            ),
        },
    )

    result = run_batch(wayback_env, monkeypatch, session)

    assert isinstance(result, dict)
    assert result['status'] == 'partial_success'
    assert result['total_candidates'] == 3
    assert result['downloaded_count'] == 2
    assert result['failed_count'] == 1
    assert result['downloaded_files'] == [
        '1_20200101000000.html',
        '3_20200103000000.html',
    ]
    assert snapshot_files(wayback_env['report_folder']) == result['downloaded_files']

    failure = result['failed'][0]
    assert failure['timestamp'] == '20200102000000'
    assert failure['original_url'] == 'http://example.com/'
    assert failure['reason_type'] == arch.FAILURE_HTTP_ERROR
    assert '503' in failure['reason']

    # The successful downloads are valid HTML with the injected base tag.
    saved = os.path.join(
        wayback_env['report_folder'], 'wayback_snapshots', '1_20200101000000.html'
    )
    with open(saved, encoding='utf-8') as f:
        assert '<base href="http://example.com/">' in f.read()


def test_all_failures_are_reported_and_no_files_are_written(wayback_env, monkeypatch):
    rows = [
        ['20200101000000', 'http://example.com/', 'text/html'],
        ['20200102000000', 'http://example.com/', 'text/html'],
    ]
    session = FakeWaybackSession(
        cdx_response=FakeResponse(json_payload=cdx_payload(rows)),
        snapshot_responses={
            ('20200101000000', 'http://example.com/'): requests.exceptions.Timeout(
                'read timed out'
            ),
            ('20200102000000', 'http://example.com/'): FakeResponse(status_code=500),
        },
    )

    result = run_batch(wayback_env, monkeypatch, session)

    assert result['status'] == 'all_failed'
    assert result['downloaded_files'] == []
    assert result['downloaded_count'] == 0
    assert result['failed_count'] == 2
    assert snapshot_files(wayback_env['report_folder']) == []

    reasons = {entry['timestamp']: entry['reason_type'] for entry in result['failed']}
    assert reasons == {
        '20200101000000': arch.FAILURE_TIMEOUT,
        '20200102000000': arch.FAILURE_HTTP_ERROR,
    }


def test_duplicate_timestamps_download_once_and_are_counted(wayback_env, monkeypatch):
    rows = [
        ['20200101000000', 'http://example.com/', 'text/html'],
        ['20200101000000', 'http://example.com/', 'text/html'],
        ['20200101000000', 'http://example.com/index.html', 'text/html'],
        ['20200102000000', 'http://example.com/about.html', 'text/html'],
    ]
    session = FakeWaybackSession(
        cdx_response=FakeResponse(json_payload=cdx_payload(rows)),
        default_snapshot_response=FakeResponse(text='<html><head></head><body>x</body></html>'),
    )

    result = run_batch(wayback_env, monkeypatch, session)

    assert result['total_candidates'] == 4
    assert result['unique_candidates'] == 2
    assert result['duplicate_count'] == 2
    assert result['downloaded_count'] == 2
    assert result['status'] == 'success'
    assert [d['timestamp'] for d in result['duplicates']] == ['20200101000000'] * 2
    assert session.snapshot_call_count == 2
    assert snapshot_files(wayback_env['report_folder']) == [
        '1_20200101000000.html',
        '2_20200102000000.html',
    ]


def test_no_html_snapshots_returns_empty_result_not_none(wayback_env, monkeypatch):
    session = FakeWaybackSession(
        cdx_response=FakeResponse(
            json_payload=cdx_payload([['20200101000000', 'http://example.com/a.pdf', 'application/pdf']])
        )
    )

    result = run_batch(wayback_env, monkeypatch, session)

    assert result is not None
    assert result['status'] == 'no_snapshots'
    assert result['total_candidates'] == 0
    assert result['downloaded_files'] == []
    assert result['failed'] == []
    assert result['duplicate_count'] == 0
    assert session.snapshot_call_count == 0


def test_parse_failure_is_categorized(wayback_env, monkeypatch):
    rows = [['20200101000000', 'http://example.com/', 'text/html']]
    session = FakeWaybackSession(
        cdx_response=FakeResponse(json_payload=cdx_payload(rows)),
        snapshot_responses={
            ('20200101000000', 'http://example.com/'): FakeResponse(
                text_error=UnicodeEncodeError('utf-8', '\ud800', 0, 1, 'bad surrogate')
            ),
        },
    )

    result = run_batch(wayback_env, monkeypatch, session)

    assert result['status'] == 'all_failed'
    assert result['failed_count'] == 1
    assert result['failed'][0]['reason_type'] == arch.FAILURE_PARSE_ERROR
    assert result['failed'][0]['reason']


def test_cdx_query_failure_is_reported_instead_of_raising(wayback_env, monkeypatch):
    session = FakeWaybackSession(cdx_response=requests.exceptions.ConnectionError('boom'))

    result = run_batch(wayback_env, monkeypatch, session)

    assert result is not None
    assert result['status'] == arch.STATUS_CDX_ERROR
    assert result['error']
    assert result['downloaded_files'] == []
