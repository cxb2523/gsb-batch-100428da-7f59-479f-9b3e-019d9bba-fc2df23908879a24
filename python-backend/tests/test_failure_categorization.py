"""Unit tests for the failure categorization helpers."""

import os
import sys

import pytest
import requests

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)

import archive_snapshotting as arch  # noqa: E402


@pytest.mark.parametrize('exc,expected', [
    (requests.exceptions.ConnectTimeout('connect timed out'), arch.FAILURE_TIMEOUT),
    (requests.exceptions.ReadTimeout('read timed out'), arch.FAILURE_TIMEOUT),
    (requests.exceptions.HTTPError('404 Client Error'), arch.FAILURE_HTTP_ERROR),
    (requests.exceptions.ConnectionError('connection reset'), arch.FAILURE_HTTP_ERROR),
    (OSError('disk full'), arch.FAILURE_PARSE_ERROR),
    (UnicodeEncodeError('utf-8', '\ud800', 0, 1, 'bad surrogate'), arch.FAILURE_PARSE_ERROR),
    (ValueError('cannot parse'), arch.FAILURE_PARSE_ERROR),
])
def test_categorize_failure(exc, expected):
    reason_type, reason = arch.categorize_failure(exc)
    assert reason_type == expected
    assert reason


def test_categorize_http_error_reports_status_code():
    response = requests.Response()
    response.status_code = 503
    exc = requests.exceptions.HTTPError('503 Server Error', response=response)

    reason_type, reason = arch.categorize_failure(exc)

    assert reason_type == arch.FAILURE_HTTP_ERROR
    assert '503' in reason


def test_categorize_failure_message_always_present():
    reason_type, reason = arch.categorize_failure(Exception())
    assert reason_type == arch.FAILURE_PARSE_ERROR
    assert reason


@pytest.mark.parametrize('entry,expected', [
    (['20200101000000', 'http://example.com/', 'text/html'], True),
    (['20200101000000', 'example.com', 'text/html'], True),
    (['20200101000000', 'http://example.com/page.html', 'text/html'], True),
    (['20200101000000', 'http://example.com/logo.png', 'image/png'], False),
    (['20200101000000'], False),
    ([], False),
])
def test_is_html_snapshot(entry, expected):
    assert arch.is_html_snapshot(entry, 'example.com') is expected
