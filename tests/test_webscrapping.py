"""Offline tests for the failure-tolerant MyCareersFuture scraper.

No network access and no real browser are used: an in-memory fake driver is
injected into the scraping pipeline (see tests/fake_driver.py).
"""
import json

import pandas as pd
import pytest

import webscrapping as ws
from fake_driver import FakeDriver, build_pages


@pytest.fixture()
def config(tmp_path):
    return ws.ScrapeConfig(
        max_pages=3,
        cards_per_page=4,
        page_wait=0.0,
        detail_wait=0.0,
        back_wait=0.0,
        snackbar_wait=0.0,
        output_csv=str(tmp_path / 'analyst_job.csv'),
        outcome_file=str(tmp_path / 'scrape_outcome.json'),
        sleep=lambda _seconds: None,
    )


def read_outcome(config):
    with open(config.outcome_file, encoding='utf-8') as handle:
        return json.load(handle)


def test_defaults_match_original_hard_coded_values():
    cfg = ws.ScrapeConfig()
    assert cfg.driver_path == '/Users/janice/Developer/chromedriver'
    assert cfg.max_pages == 64
    assert cfg.cards_per_page == 20
    assert cfg.page_wait == 6.0
    assert cfg.detail_wait == 8.0
    assert cfg.back_wait == 3.0
    assert cfg.snackbar_wait == 2.0
    assert cfg.output_csv == 'analyst_job.csv'
    assert cfg.outcome_file == 'scrape_outcome.json'


def test_env_overrides(monkeypatch):
    monkeypatch.setenv('MCF_CHROMEDRIVER_PATH', '/tmp/fake-driver')
    monkeypatch.setenv('MCF_MAX_PAGES', '7')
    monkeypatch.setenv('MCF_PAGE_WAIT', '1.5')
    monkeypatch.setenv('MCF_OUTPUT_CSV', 'out.csv')
    cfg = ws.ScrapeConfig.from_env()
    assert cfg.driver_path == '/tmp/fake-driver'
    assert cfg.max_pages == 7
    assert cfg.page_wait == 1.5
    assert cfg.output_csv == 'out.csv'
    # Explicit overrides still win over the environment.
    cfg2 = ws.ScrapeConfig.from_env(max_pages=2)
    assert cfg2.max_pages == 2


def test_happy_path_collects_every_card(config):
    pages = build_pages(3, 4)
    driver = FakeDriver(pages)

    outcome = ws.run_scrape(config, driver)

    assert outcome.total_pages == 3
    assert outcome.successful_pages == 3
    assert outcome.failed_pages == []
    assert outcome.success_count == 12
    assert outcome.skipped_jobs == []
    assert driver.closed is False  # injected driver is not ours to close

    csv_df = pd.read_csv(config.output_csv)
    assert list(csv_df.columns) == ws.COLUMNS
    assert len(csv_df) == 12
    assert csv_df.iloc[0]['title'] == 'Job 1'
    assert csv_df.iloc[-1]['title'] == 'Job 12'

    saved = read_outcome(config)
    assert saved == {
        'total_pages': 3,
        'successful_pages': 3,
        'failed_pages': [],
        'success_count': 12,
        'skipped_jobs': [],
    }


def test_failed_pages_are_recorded_and_collection_continues(config):
    pages = build_pages(3, 4)
    driver = FakeDriver(
        pages,
        page_load_faults={0},       # cannot load page 0
        title_faults={2},           # page 2 loads but title is wrong
    )

    outcome = ws.run_scrape(config, driver)

    assert outcome.total_pages == 3
    assert outcome.successful_pages == 1
    assert [item['page'] for item in outcome.failed_pages] == [0, 2]
    assert 'ConnectionError' in outcome.failed_pages[0]['reason']
    assert 'AssertionError' in outcome.failed_pages[1]['reason']
    assert outcome.success_count == 4
    assert outcome.skipped_jobs == []


def test_failed_card_is_skipped_but_same_page_continues(config):
    config.max_pages = 2
    pages = build_pages(2, 4)
    driver = FakeDriver(
        pages,
        list_missing={(0, 2)},        # cannot even read card 2 on page 0
        detail_missing={(1, 3)},      # card 3 on page 1 opens, field missing
    )

    outcome = ws.run_scrape(config, driver)

    assert outcome.successful_pages == 2
    assert outcome.success_count == 6  # 8 cards minus 2 skipped
    assert outcome.skipped_jobs == [
        {
            'page': 0,
            'card_index': 2,
            'stage': 'list_extract',
            'error': outcome.skipped_jobs[0]['error'],
        },
        {
            'page': 1,
            'card_index': 3,
            'stage': 'detail_extract',
            'error': outcome.skipped_jobs[1]['error'],
        },
    ]
    assert 'NoSuchElementException' in outcome.skipped_jobs[0]['error']
    # The card after the skipped one on the same page was still collected.
    csv_df = pd.read_csv(config.output_csv)
    titles = set(csv_df['title'])
    assert 'Job 1' in titles and 'Job 3' in titles and 'Job 4' in titles


def test_click_failure_uses_snackbar_retry_then_skips(config):
    config.max_pages = 1
    pages = build_pages(1, 4)
    driver = FakeDriver(
        pages,
        click_recover={(0, 1)},   # first click intercepted, retry works
        click_always={(0, 2)},    # retry keeps failing -> skipped
    )

    outcome = ws.run_scrape(config, driver)

    assert outcome.success_count == 3
    assert len(outcome.skipped_jobs) == 1
    skipped = outcome.skipped_jobs[0]
    assert skipped['page'] == 0
    assert skipped['card_index'] == 2
    assert skipped['stage'] == 'click'
    assert driver.snackbar_dismissed == 2  # one recovery + one doomed retry
    assert driver.click_attempts[(0, 1)] == 2
    assert driver.click_attempts[(0, 2)] == 2
    # After every card the fake must be back on the list view.
    assert driver.view == 'list'


def test_missing_salary_still_stores_job_with_nulls(config):
    config.max_pages = 1
    config.cards_per_page = 2
    pages = build_pages(1, 2)
    driver = FakeDriver(pages, salary_missing={(0, 1)})

    outcome = ws.run_scrape(config, driver)

    assert outcome.success_count == 2
    assert outcome.skipped_jobs == []
    csv_df = pd.read_csv(config.output_csv)
    first = csv_df.iloc[0]
    assert pd.isna(first['salary_min'])
    assert pd.isna(first['salary_max'])
    assert pd.isna(first['salary_type'])
    assert pd.notna(csv_df.iloc[1]['salary_min'])


def test_all_pages_failing_still_writes_header_only_csv_and_outcome(config):
    config.max_pages = 2
    pages = build_pages(2, 3)
    driver = FakeDriver(pages, page_load_faults={0, 1})

    outcome = ws.run_scrape(config, driver)

    assert outcome.successful_pages == 0
    assert outcome.success_count == 0
    assert len(outcome.failed_pages) == 2
    csv_df = pd.read_csv(config.output_csv)
    assert list(csv_df.columns) == ws.COLUMNS
    assert len(csv_df) == 0
    saved = read_outcome(config)
    assert saved['success_count'] == 0


def test_missing_driver_raises_clear_error(config, monkeypatch):
    monkeypatch.setattr(ws.os.path, 'exists', lambda path: False)
    with pytest.raises(FileNotFoundError, match='Chrome driver not found'):
        ws.create_driver(config)


def test_driver_startup_failure_raises_clear_error(config, monkeypatch):
    monkeypatch.setattr(ws.os.path, 'exists', lambda path: True)

    def boom(*args, **kwargs):
        raise ws.WebDriverException('session not created')

    monkeypatch.setattr(ws.webdriver, 'Chrome', boom)
    with pytest.raises(RuntimeError, match='Failed to start Chrome'):
        ws.create_driver(config)


def test_main_reports_driver_error_without_silent_empty_csv(
    config, monkeypatch, capsys, tmp_path
):
    monkeypatch.setenv('MCF_OUTPUT_CSV', config.output_csv)
    monkeypatch.setenv('MCF_OUTCOME_FILE', config.outcome_file)
    monkeypatch.setenv('MCF_CHROMEDRIVER_PATH', '/no/such/driver')
    monkeypatch.setattr(ws.os.path, 'exists', lambda path: False)

    exit_code = ws.main([])

    assert exit_code == 1
    err = capsys.readouterr().err
    assert 'Chrome driver not found' in err
    # Nothing was written: a driver failure must not look like an empty result.
    assert not __import__('pathlib').Path(config.output_csv).exists()
    assert not __import__('pathlib').Path(config.outcome_file).exists()


def test_end_to_end_mixed_run_outputs_are_consistent(config):
    config.max_pages = 4
    config.cards_per_page = 5
    pages = build_pages(4, 5)
    driver = FakeDriver(
        pages,
        page_load_faults={1},
        list_missing={(0, 3), (2, 1)},
        detail_missing={(3, 5)},
        click_recover={(2, 4)},
        salary_missing={(0, 1)},
    )

    outcome = ws.run_scrape(config, driver)
    saved = read_outcome(config)

    assert saved['total_pages'] == 4
    assert saved['successful_pages'] == 3
    assert [p['page'] for p in saved['failed_pages']] == [1]
    assert saved['success_count'] == 3 * 5 - 3
    assert len(saved['skipped_jobs']) == 3
    # CSV row count matches the outcome bookkeeping exactly.
    assert len(pd.read_csv(config.output_csv)) == saved['success_count']
