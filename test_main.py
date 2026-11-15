import json
import logging
import os
from datetime import datetime, timedelta

import pytest

import main
from test_fakes import FakeDriver, FakeRow, FakeSection, FakeWebDriverWait


@pytest.fixture
def fast_waits(monkeypatch):
    """Replace Selenium waits with sleep-free fakes and remove paging delays."""
    monkeypatch.setattr(main, 'WebDriverWait', FakeWebDriverWait)
    monkeypatch.setattr(main, 'PAGE_LOAD_WAIT_SECONDS', 0)


@pytest.fixture
def log():
    logger = logging.getLogger('test-wealthsimpleton')
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    return logger


@pytest.fixture
def cutoff():
    # Generous default: all "recent" fake rows are inside this window.
    return datetime.now() - timedelta(days=main.DEFAULT_AFTER_DAYS)


def _row(description='XEQT', transaction_type='limit buy', amount='$54.50 CAD',
         date_text=None, extra_details=None, panel_timeout=False):
    date_text = date_text or (datetime.now() - timedelta(days=2)).strftime('%B %d, %Y')
    details = {'Date': date_text, 'Total cost': amount}
    if extra_details:
        details.update(extra_details)
    return FakeRow(description, transaction_type, amount,
                   details=details, panel_timeout=panel_timeout)


# --- IncrementalWriter / atomic output --------------------------------------

def test_incremental_file_is_always_valid_json(tmp_path, log):
    path = str(tmp_path / 'out.json')
    writer = main.IncrementalWriter(path, log)
    for i in range(4):
        writer.add({
            'description': f'TICKER{i}', 'type': 'dividend',
            'amount': f'${i}.00 CAD', 'date': '2026-11-01T00:00:00',
            'details': {'Date': 'November 1, 2026'},
        })
        # After every single add the file must already be a parseable array.
        with open(path, encoding='utf-8') as handle:
            on_disk = json.load(handle)
        assert isinstance(on_disk, list)
        assert len(on_disk) == i + 1
    assert writer.write_errors == 0


def test_write_failure_keeps_scraping_and_final_file_is_valid(tmp_path, log):
    path = str(tmp_path / 'out.json')
    calls = {'n': 0}

    def flaky_persist(target, records):
        calls['n'] += 1
        if calls['n'] == 2:
            raise OSError('simulated mid-run disk failure')
        return main.atomic_write_json(target, records)

    writer = main.IncrementalWriter(path, log, persist_fn=flaky_persist)
    for i in range(3):
        writer.add({
            'description': f'TICKER{i}', 'type': 'dividend',
            'amount': f'${i}.00 CAD', 'date': '2026-11-01T00:00:00',
            'details': {},
        })
    assert writer.write_errors == 1  # failure was counted, scraping continued
    # The failed attempt must not have left a half-written temp file behind.
    leftovers = [name for name in os.listdir(tmp_path) if name.startswith('.wealthsimpleton-')]
    assert leftovers == []

    writer.flush()  # final retry succeeds via the real atomic writer path
    with open(path, encoding='utf-8') as handle:
        on_disk = json.load(handle)
    assert [row['description'] for row in on_disk] == ['TICKER0', 'TICKER1', 'TICKER2']
    assert writer.write_errors == 0


def test_resume_does_not_duplicate_existing_records(tmp_path, log):
    path = str(tmp_path / 'out.json')
    existing = [{
        'description': 'XEQT', 'type': 'limit buy', 'amount': '$54.50 CAD',
        'date': '2026-11-01T00:00:00', 'details': {'Date': 'November 1, 2026'},
    }]
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(existing, handle)

    writer = main.IncrementalWriter(path, log)
    resumed = writer.add(dict(existing[0]))  # identical record scraped again
    assert resumed is True
    fresh = writer.add({
        'description': 'VEQT', 'type': 'dividend', 'amount': '$10.00 CAD',
        'date': '2026-11-02T00:00:00', 'details': {'Date': 'November 2, 2026'},
    })
    assert fresh is False
    with open(path, encoding='utf-8') as handle:
        on_disk = json.load(handle)
    assert [row['description'] for row in on_disk] == ['XEQT', 'VEQT']


def test_corrupt_existing_file_is_moved_aside_not_overwritten_silently(tmp_path, log):
    path = str(tmp_path / 'out.json')
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write('[{"description": broken')
    writer = main.IncrementalWriter(path, log)
    assert writer.records == []
    backups = [name for name in os.listdir(tmp_path) if name.startswith('out.json.corrupt-')]
    assert len(backups) == 1
    writer.add({
        'description': 'NEW', 'type': 'dividend', 'amount': '$1.00 CAD',
        'date': '2026-11-01T00:00:00', 'details': {},
    })
    with open(path, encoding='utf-8') as handle:
        assert json.load(handle)[0]['description'] == 'NEW'


# --- Happy-path scraping -----------------------------------------------------

def test_normal_scrape_of_multiple_transactions(fast_waits, tmp_path, log, cutoff, capsys):
    rows = [_row(description=f'TICKER{i}') for i in range(3)]
    driver = FakeDriver([[FakeSection('November 10, 2026', rows)]])
    path = str(tmp_path / 'out.json')

    stats, skipped, writer = main.run(driver, path, cutoff, log)

    assert stats['succeeded'] == 3
    assert stats['written'] == 3
    assert stats['skipped'] == 0
    assert skipped == []
    with open(path, encoding='utf-8') as handle:
        on_disk = json.load(handle)
    # Output schema is unchanged for myResultParser.js.
    assert set(on_disk[0].keys()) == {'description', 'type', 'amount', 'date', 'details'}
    assert [row['description'] for row in on_disk] == ['TICKER0', 'TICKER1', 'TICKER2']
    # stdout remains a pure JSON array.
    printed = json.loads(capsys.readouterr().out)
    assert len(printed) == 3


def test_no_file_means_stdout_only(fast_waits, tmp_path, log, cutoff, capsys):
    driver = FakeDriver([[FakeSection('November 10, 2026', [_row()])]])
    stats, skipped, writer = main.run(driver, '', cutoff, log)
    assert stats['succeeded'] == 1
    assert writer.path is None
    printed = json.loads(capsys.readouterr().out)
    assert printed[0]['description'] == 'XEQT'
    assert [name for name in os.listdir(tmp_path)] == []


# --- Skip handling ------------------------------------------------------------

def test_panel_timeout_is_skipped_with_index_description_and_reason(fast_waits, tmp_path, log, cutoff, caplog):
    good = _row(description='GOOD')
    broken = _row(description='STUCK', panel_timeout=True)
    driver = FakeDriver([[FakeSection('November 10, 2026', [good, broken])]])

    with caplog.at_level(logging.WARNING, logger='test-wealthsimpleton'):
        stats, skipped, _ = main.run(driver, str(tmp_path / 'out.json'), cutoff, log)

    assert stats['succeeded'] == 1
    assert stats['skipped'] == 1
    assert skipped[0]['index'] == 2
    assert skipped[0]['description'] == 'STUCK'
    assert 'panel did not open' in skipped[0]['reason']
    log_text = caplog.text
    assert 'Skipped 2/2: STUCK' in log_text
    assert 'panel did not open' in log_text
    with open(str(tmp_path / 'out.json'), encoding='utf-8') as handle:
        assert [row['description'] for row in json.load(handle)] == ['GOOD']


def test_missing_amount_is_skipped(fast_waits, tmp_path, log, cutoff, caplog):
    # Panel contains only share/price rows: no currency value anywhere.
    row = FakeRow('BROKE', 'limit buy', '', details={
        'Date': (datetime.now() - timedelta(days=1)).strftime('%B %d, %Y'),
        'Filled quantity': '1.1444 shares',
        'Price per share': '$47.62 CAD',
    })
    driver = FakeDriver([[FakeSection('November 10, 2026', [row])]])
    with caplog.at_level(logging.WARNING, logger='test-wealthsimpleton'):
        stats, skipped, _ = main.run(driver, str(tmp_path / 'out.json'), cutoff, log)
    assert stats['succeeded'] == 0
    assert stats['skipped'] == 1
    assert 'no transaction amount' in skipped[0]['reason']
    assert 'Skipped 1/1: BROKE' in caplog.text


def test_unparseable_date_is_skipped(fast_waits, tmp_path, log, cutoff, caplog):
    row = _row(date_text='not a real date')
    driver = FakeDriver([[FakeSection('November 10, 2026', [row])]])
    with caplog.at_level(logging.WARNING, logger='test-wealthsimpleton'):
        stats, skipped, _ = main.run(driver, str(tmp_path / 'out.json'), cutoff, log)
    assert stats['skipped'] == 1
    assert 'unparseable date' in skipped[0]['reason']
    assert 'not a real date' in skipped[0]['reason']


def test_inactive_status_is_skipped(fast_waits, tmp_path, log, cutoff, caplog):
    row = _row(extra_details={'Status': 'Cancelled'})
    driver = FakeDriver([[FakeSection('November 10, 2026', [row])]])
    with caplog.at_level(logging.WARNING, logger='test-wealthsimpleton'):
        stats, skipped, _ = main.run(driver, str(tmp_path / 'out.json'), cutoff, log)
    assert stats['skipped'] == 1
    assert 'inactive status: Cancelled' == skipped[0]['reason']


# --- Pagination robustness ----------------------------------------------------

def test_pagination_continues_past_unparseable_oldest_header(fast_waits, log, cutoff, caplog):
    recent = [FakeSection('November 10, 2026', [_row(description='RECENT')])]
    next_page = [
        FakeSection('??? unknown header ???', [_row(description='MIDDLE')]),
    ]
    old = [FakeSection('January 1, 2020', [_row(description='OLD')])]
    driver = FakeDriver([recent, next_page, old])

    with caplog.at_level(logging.WARNING, logger='test-wealthsimpleton'):
        clicks = main.load_all_activity(driver, cutoff, log)

    # The unparseable header must not stop pagination: the next click reveals
    # the old page, whose parseable header is confirmed past the cutoff.
    assert clicks == 2
    assert driver.revealed_pages == 3
    assert 'could not be parsed as a date' in caplog.text
    assert 'loading the next page anyway' in caplog.text


def test_pagination_stops_when_oldest_header_confirmed_before_cutoff(fast_waits, log):
    pages = [
        [FakeSection('November 10, 2026', [_row()])],
        [FakeSection('January 1, 2020', [_row()])],
    ]
    driver = FakeDriver(pages)
    cutoff_dt = datetime.now() - timedelta(days=45)
    clicks = main.load_all_activity(driver, cutoff_dt, log)
    assert clicks == 1
    assert driver.revealed_pages == 2


def test_pagination_stops_when_load_more_disappears(fast_waits, log, cutoff):
    driver = FakeDriver([[FakeSection('November 10, 2026', [_row()])]])
    assert main.load_all_activity(driver, cutoff, log) == 0


def test_scraping_stops_once_cutoff_transaction_reached(fast_waits, tmp_path, log):
    recent_row = _row(description='RECENT',
                      date_text=(datetime.now() - timedelta(days=2)).strftime('%B %d, %Y'))
    old_row = _row(description='OLD', date_text='January 1, 2020')
    driver = FakeDriver([[FakeSection('November 10, 2026', [recent_row, old_row])]])
    stats, skipped, _ = main.run(driver, str(tmp_path / 'out.json'),
                                 datetime.now() - timedelta(days=45), log)
    assert stats['succeeded'] == 1
    assert stats['skipped'] == 0  # cutoff stop is not a skip
    with open(str(tmp_path / 'out.json'), encoding='utf-8') as handle:
        assert [row['description'] for row in json.load(handle)] == ['RECENT']


# --- End-to-end summary --------------------------------------------------------

def test_final_summary_reports_success_and_skip_counts(fast_waits, tmp_path, log, cutoff, caplog):
    rows = [
        _row(description='OK1'),
        _row(description='TIMEOUT', panel_timeout=True),
        _row(description='OK2'),
    ]
    driver = FakeDriver([[FakeSection('November 10, 2026', rows)]])
    with caplog.at_level(logging.INFO, logger='test-wealthsimpleton'):
        stats, skipped, _ = main.run(driver, str(tmp_path / 'out.json'), cutoff, log)
    assert stats['succeeded'] == 2
    assert stats['skipped'] == 1
    assert 'Scrape complete: 2 succeeded (2 newly written, 0 already on file), 1 skipped' in caplog.text
