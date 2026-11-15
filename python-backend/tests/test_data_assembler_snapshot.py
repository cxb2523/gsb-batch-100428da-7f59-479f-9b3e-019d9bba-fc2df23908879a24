"""Tests for the data_assembler snapshot branch and report context mapping."""

import os
import sys
import types

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(TESTS_DIR)


def _stub_missing_module(name):
    """Provide a minimal stub for optional third-party deps missing locally."""
    try:
        __import__(name)
    except ImportError:
        sys.modules.setdefault(name, types.ModuleType(name))


@pytest.fixture
def data_assembler(monkeypatch):
    _stub_missing_module('whois')
    _stub_missing_module('builtwith')

    import data_assembler as module

    monkeypatch.setattr(
        module,
        'read_config',
        lambda: {'installed_browser': 'firefox', 'wayback_retries_amount': '1',
                 'wayback_requests_pause': '0'},
    )
    return module


@pytest.fixture
def report_module():
    import html_report_creation as module
    return module


def _wire_scan(monkeypatch, module, snapshot_result):
    """Replace every network-bound gather step of data_gathering."""
    cp = module.cp
    npmod = module.np

    monkeypatch.setattr(cp, 'ip_gather', lambda domain: '1.1.1.1')
    monkeypatch.setattr(cp, 'whois_gather', lambda domain: {
        'registrar': 'r', 'creation_date': 'c', 'expiration_date': 'e',
        'name_servers': ['n'], 'org': 'o',
    })
    monkeypatch.setattr(cp, 'contact_mail_gather', lambda url: [])
    monkeypatch.setattr(cp, 'subdomains_gather', lambda url, domain: ([], 0))
    monkeypatch.setattr(cp, 'sm_gather', lambda url: {})
    monkeypatch.setattr(cp, 'domains_reverse_research', lambda subs, rtype: ([], [], {}))

    monkeypatch.setattr(npmod, 'get_ssl_certificate',
                        lambda domain: ('issuer', 'subject', 'nb', 'na', 'cn', 'sn'))
    monkeypatch.setattr(npmod, 'get_dns_info', lambda domain, rtype: 'mx')
    monkeypatch.setattr(npmod, 'get_robots_txt', lambda domain, path: 'robots ok')
    monkeypatch.setattr(npmod, 'get_sitemap_xml', lambda domain, path: 'sitemap ok')
    monkeypatch.setattr(npmod, 'extract_links_from_sitemap', lambda lp, sp: 'links ok')
    monkeypatch.setattr(npmod, 'get_technologies', lambda url: ([], [], [], [], [], []))
    monkeypatch.setattr(npmod, 'query_internetdb', lambda ip, rtype: ([], [], [], [], []))

    monkeypatch.setattr(module.api_hudsonrock, 'build_hudsonrock_intelligence',
                        lambda **kwargs: {'risk_score': 0, 'risk_level': 'none'})
    monkeypatch.setattr(module.api_lunarcyber, 'build_lunarcyber_intelligence',
                        lambda domain: {'status': 'UNKNOWN'})
    monkeypatch.setattr(module, 'run_wayback_snapshotting', lambda *a, **k: snapshot_result)


def _run_scan(module, tmp_path, snapshot_flag='w'):
    return module.DataProcessing().data_gathering(
        'example.com', 'http://example.com/', 'html', 'n', '', 0, 'n',
        ['Empty'], snapshot_flag, None, '20200101', '20231231',
    )


def test_scan_with_no_snapshots_records_outcome(data_assembler, monkeypatch, tmp_path):
    result = {
        'status': 'no_snapshots', 'total_candidates': 0, 'unique_candidates': 0,
        'duplicate_count': 0, 'duplicates': [], 'downloaded_files': [],
        'failed': [], 'downloaded_count': 0, 'failed_count': 0,
        'summary': 'No HTML snapshots were available for download.', 'error': None,
    }
    _wire_scan(monkeypatch, data_assembler, result)

    data_array, _ = _run_scan(data_assembler, tmp_path)

    assert data_array[53]['status'] == 'no_snapshots'
    assert data_array[53]['downloaded_files'] == []


def test_scan_with_all_failed_is_distinguishable(data_assembler, monkeypatch, tmp_path):
    result = {
        'status': 'all_failed', 'total_candidates': 2, 'unique_candidates': 2,
        'duplicate_count': 0, 'duplicates': [], 'downloaded_files': [],
        'failed': [
            {'timestamp': '20200101000000', 'original_url': 'http://example.com/',
             'archive_url': 'x', 'reason_type': 'http_error', 'reason': 'HTTP 500'},
        ],
        'downloaded_count': 0, 'failed_count': 1,
        'summary': 'All 1 snapshot download(s) failed.', 'error': None,
    }
    _wire_scan(monkeypatch, data_assembler, result)

    data_array, _ = _run_scan(data_assembler, tmp_path)

    assert data_array[53]['status'] == 'all_failed'
    assert data_array[53]['failed_count'] == 1

    # The report layer turns this into an explicit "all failed" context.
    import html_report_creation as report_module
    context = report_module.build_wayback_context(data_array[53])
    assert context['wayback_status'] == 'all_failed'
    assert context['wayback_all_failed'] is True
    assert context['wayback_no_snapshots'] is False
    assert context['wayback_failed_count'] == 1
    assert context['wayback_failed'][0]['timestamp'] == '20200101000000'


def test_scan_without_wayback_uses_not_requested_placeholder(data_assembler, monkeypatch, tmp_path):
    _wire_scan(monkeypatch, data_assembler, None)

    data_array, _ = _run_scan(data_assembler, tmp_path, snapshot_flag='n')

    assert data_array[53]['status'] == 'not_requested'

    import html_report_creation as report_module
    context = report_module.build_wayback_context(data_array[53])
    assert context['wayback_enabled'] is False
    assert context['wayback_status'] == 'not_requested'


def test_run_wayback_snapshotting_converts_crash_into_result(data_assembler, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError('unexpected failure')

    monkeypatch.setattr(data_assembler, 'download_snapshot', boom)

    result = data_assembler.run_wayback_snapshotting(
        'example.com', '20200101', '20231231', 'report_x'
    )

    assert result['status'] == data_assembler.snapshot_error_result('x')['status']
    assert 'unexpected failure' in result['error']
    assert result['downloaded_files'] == []
    assert result['failed'] == []


def test_run_wayback_snapshotting_passes_through_result(data_assembler, monkeypatch):
    sentinel = {'status': 'success', 'downloaded_files': ['1_20200101000000.html'],
                'failed': [], 'downloaded_count': 1, 'failed_count': 0,
                'duplicate_count': 0, 'duplicates': [], 'total_candidates': 1,
                'unique_candidates': 1, 'summary': 'ok', 'error': None}
    monkeypatch.setattr(data_assembler, 'download_snapshot', lambda *a, **k: sentinel)

    assert data_assembler.run_wayback_snapshotting('example.com', 'a', 'b', 'c') is sentinel


@pytest.mark.parametrize('status,expected', [
    ('no_snapshots', {'wayback_no_snapshots': True, 'wayback_all_failed': False}),
    ('all_failed', {'wayback_no_snapshots': False, 'wayback_all_failed': True}),
    ('partial_success', {'wayback_no_snapshots': False, 'wayback_all_failed': False,
                         'wayback_partial': True}),
])
def test_report_context_distinguishes_outcomes(report_module, status, expected):
    context = report_module.build_wayback_context({
        'status': status, 'total_candidates': 3, 'unique_candidates': 2,
        'duplicate_count': 1,
        'duplicates': [{'timestamp': '20200101000000', 'original_url': 'u'}],
        'downloaded_files': ['1_x.html'] if status == 'partial_success' else [],
        'failed': [] if status != 'all_failed' else [
            {'timestamp': 't', 'original_url': 'u', 'reason_type': 'timeout',
             'reason': 'timed out'},
        ],
        'downloaded_count': 1 if status == 'partial_success' else 0,
        'failed_count': 1 if status == 'all_failed' else 0,
        'summary': 's', 'error': None,
    })
    for key, value in expected.items():
        assert context[key] is value
    assert context['wayback_duplicate_count'] == 1
    assert context['wayback_duplicates'][0]['timestamp'] == '20200101000000'


def test_report_context_handles_missing_result(report_module):
    context = report_module.build_wayback_context(None)
    assert context['wayback_status'] == 'not_requested'
    assert context['wayback_enabled'] is False
