"""Tests for the paginated Alibaba scraping pipeline.

All tests use a fake `make_scrapingant_request` — no real ScrapingAnt calls.
"""
import csv
import urllib.parse
from pathlib import Path

import pytest
from click.testing import CliRunner

import main
from main import PageOutcome


def make_html(n_items, prefix='item'):
    """Build fake search-result HTML that extract_data_from_html can parse."""
    parts = []
    for i in range(n_items):
        parts.append(f'''
        <div data-role="item">
          <span class="organic-gallery-title__content">{prefix} title {i}</span>
          <a flasher-type="supplierName" href="//shop{i}.example.com">Seller {i}</a>
          <span class="seller-tag__country" title="CN">CN</span>
          <img flasher-type="mainImage" data-image="//img{i}.example.com/x.jpg"/>
          <span class="gallery-offer-price">$1.00</span>
          <a class="organic-gallery-title" href="//product{i}.example.com">link</a>
        </div>''')
    return '<html><body>' + ''.join(parts) + '</body></html>'


EMPTY_HTML = '<html><body><p>nothing here</p></body></html>'


def page_number_of(url):
    return int(urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get('page', ['1'])[0])


def install_fake_request(monkeypatch, behavior):
    """Replace make_scrapingant_request with a fake driven by `behavior`.

    behavior: dict page_number -> ('ok', n_items) | ('empty',) | ('fail',)
    Returns the list of requested page numbers (one entry per attempt,
    including retries).
    """
    requested_pages = []

    def fake_request(target_url, rapidapi_key, js_snippet=None, proxy_country=None):
        page = page_number_of(target_url)
        requested_pages.append(page)
        action = behavior.get(page, ('ok', 2))
        if action[0] == 'fail':
            raise ConnectionError(f'boom on page {page}')
        if action[0] == 'empty':
            return EMPTY_HTML
        return make_html(action[1], prefix=f'page{page}')

    monkeypatch.setattr(main, 'make_scrapingant_request', fake_request)
    return requested_pages


def test_multiple_pages_all_success(monkeypatch):
    requested = install_fake_request(monkeypatch, {
        1: ('ok', 3), 2: ('ok', 2), 3: ('ok', 4),
    })
    items, reports = main.get_search_results('shoes', 'key', pages=3, country='us')

    assert len(items) == 3 + 2 + 4
    assert [r['outcome'] for r in reports] == [PageOutcome.SUCCESS] * 3
    assert [r['page'] for r in reports] == [1, 2, 3]
    assert [r['count'] for r in reports] == [3, 2, 4]
    assert sum(r['count'] for r in reports) == len(items)
    assert requested == [1, 2, 3]
    assert items[0]['listing_title'] == 'page1 title 0'
    assert items[-1]['listing_title'] == 'page3 title 3'


def test_failure_mid_run_keeps_collected_items(monkeypatch):
    requested = install_fake_request(monkeypatch, {
        1: ('ok', 3), 2: ('fail',), 3: ('ok', 5),
    })
    items, reports = main.get_search_results('shoes', 'key', pages=3, country='us')

    # page 1 items preserved, page 3 never attempted
    assert len(items) == 3
    assert [r['outcome'] for r in reports] == [PageOutcome.SUCCESS, PageOutcome.FAILED]
    assert [r['page'] for r in reports] == [1, 2]
    assert 3 not in requested
    # the failing page was retried (3 attempts) before being declared failed
    assert requested.count(2) == 3


def test_first_page_failure_aborts_run(monkeypatch):
    requested = install_fake_request(monkeypatch, {1: ('fail',)})
    items, reports = main.get_search_results('shoes', 'key', pages=3, country='us')

    assert items == []
    assert reports == [{'page': 1, 'outcome': PageOutcome.FAILED, 'count': 0}]
    assert set(requested) == {1}  # no further pages attempted


def test_empty_page_stops_pagination_and_keeps_items(monkeypatch):
    requested = install_fake_request(monkeypatch, {
        1: ('ok', 2), 2: ('empty',), 3: ('ok', 9),
    })
    items, reports = main.get_search_results('shoes', 'key', pages=3, country='us')

    assert len(items) == 2
    assert [r['outcome'] for r in reports] == [PageOutcome.SUCCESS, PageOutcome.EMPTY]
    assert 3 not in requested


def test_failed_and_empty_are_distinct_outcomes(monkeypatch):
    """A retried-then-failed page must not be confused with an empty page."""
    install_fake_request(monkeypatch, {1: ('fail',)})
    _, reports_fail = main.get_search_results('shoes', 'key', pages=1, country='us')
    install_fake_request(monkeypatch, {1: ('empty',)})
    _, reports_empty = main.get_search_results('shoes', 'key', pages=1, country='us')

    assert reports_fail[0]['outcome'] is PageOutcome.FAILED
    assert reports_empty[0]['outcome'] is PageOutcome.EMPTY


def test_summary_totals_add_up(monkeypatch, capsys):
    install_fake_request(monkeypatch, {1: ('ok', 3), 2: ('ok', 2), 3: ('fail',)})
    items, reports = main.get_search_results('shoes', 'key', pages=4, country='us')
    main.print_run_summary(reports, len(items))

    out = capsys.readouterr().out
    assert 'page 1: success (3 items)' in out
    assert 'page 2: success (2 items)' in out
    assert 'page 3: failed (0 items)' in out
    assert 'successful pages: 2, failed pages: 1, empty pages: 0, total items: 5' in out


def test_cli_writes_csv_with_partial_results(monkeypatch, tmp_path):
    """Mid-run failure still yields a CSV containing the collected items."""
    monkeypatch.chdir(tmp_path)
    install_fake_request(monkeypatch, {1: ('ok', 2), 2: ('fail',)})

    result = CliRunner().invoke(
        main.main, ['shoes', '--rapidapi_key', 'key', '--pages', '3'])
    assert result.exit_code == 0, result.output

    csv_files = list(Path('data').glob('shoes_*.csv'))
    assert len(csv_files) == 1
    with open(csv_files[0], newline='') as f:
        rows = list(csv.DictReader(f, delimiter='\t', quotechar='|'))
    assert len(rows) == 2
    assert list(rows[0].keys()) == ['item_url', 'listing_title', 'seller_name',
                                    'store_url', 'price', 'image_url', 'seller_location']
    assert rows[0]['listing_title'] == 'page1 title 0'
    assert 'failed pages: 1' in result.output
    assert 'total items: 2' in result.output


def test_cli_first_page_failure_writes_no_csv(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    install_fake_request(monkeypatch, {1: ('fail',)})

    result = CliRunner().invoke(
        main.main, ['shoes', '--rapidapi_key', 'key', '--pages', '2'])
    assert result.exit_code == 0, result.output
    assert 'no items found' in result.output
    assert 'page 1: failed' in result.output
    assert not Path('data').exists() or not list(Path('data').glob('*.csv'))
