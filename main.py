import csv
import json
import base64
import urllib.parse
from datetime import datetime
from enum import Enum
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt
import click

SUPPORTED_COUNTRIES = [
    'ae',  # United Arab Emirates (the)
    'br',  # Brasilia
    'cn',  # China
    'de',  # Germany
    'es',  # Spain
    'fr',  # France
    'gb',  # United Kingdom (the)
    'hk',  # Hong Kong
    'in',  # India
    'it',  # Italy
    'il',  # Israel
    'jp',  # Japan
    'nl',  # Netherlands (the)
    'ru',  # Russia
    'sa',  # Saudi Arabia
    'us',  # USA
]


class PageOutcome(Enum):
    """Outcome of scraping a single search page."""
    SUCCESS = 'success'    # page fetched and items parsed
    FAILED = 'failed'      # request/parse error, still failing after retries
    EMPTY = 'empty'        # page fetched but 0 items parsed


def make_scrapingant_request(target_url, rapidapi_key, js_snippet=None, proxy_country=None):
    print(f'getting page {target_url}')
    headers = {
        'x-rapidapi-host': "scrapingant.p.rapidapi.com",
        'x-rapidapi-key': rapidapi_key,
    }
    request_data = {'url': target_url}
    if js_snippet:
        encoded_js_snippet = base64.b64encode(js_snippet.encode()).decode()
        request_data['js_snippet'] = encoded_js_snippet
    if proxy_country:
        request_data['proxy_country'] = proxy_country.lower()

    r = requests.post(
        'https://scrapingant.p.rapidapi.com/post',
        data=json.dumps(request_data),
        headers=headers
    )
    r.raise_for_status()
    return r.text


def extract_data_from_html(page_html):
    soup = BeautifulSoup(page_html, 'html.parser')
    items_elements = soup.find_all(attrs={'data-role': 'item'})
    items_data_list = []
    for item_element in items_elements:
        title_element = item_element.find(attrs={'class': ['organic-gallery-title__content', 'ic-offer__title-text']})
        if not title_element:
            continue
        item_data = {'listing_title': title_element.getText()}
        seller_element = item_element.find(attrs={'flasher-type': 'supplierName'})
        if seller_element:
            item_data['seller_name'] = seller_element.getText()
            item_data['store_url'] = seller_element['href'][2:]
        seller_country_element = item_element.find(attrs={'class': 'seller-tag__country'})
        if seller_country_element:
            item_data['seller_location'] = seller_country_element['title']
        image_element = item_element.find(attrs={"flasher-type": "mainImage"})
        if image_element:
            item_data['image_url'] = image_element['data-image'][2:]
        price_element = item_element.find(attrs={'class': ['gallery-offer-price', 'ic-offer-price']})
        if price_element:
            item_data['price'] = price_element.getText()
        link_element = item_element.find(attrs={'class': ['organic-gallery-title', 'ic-offer__title-link-wrapper']})
        if link_element:
            item_data['item_url'] = link_element['href'][2:]
        items_data_list.append(item_data)
    print(f'Got {len(items_data_list)} items')

    return items_data_list


@retry(stop=stop_after_attempt(3))
def extract_items_from_url(url_string, rapidapi_key, country):
    """Fetch one page and parse items.

    Raises the underlying exception if the request/parse still fails after
    retries. A successfully fetched page with no items returns an empty
    list, so callers can distinguish "still failing" from "genuinely empty".
    """
    js_snippet = """
    window.scrollTo(0,document.body.scrollHeight);
    await new Promise(r => setTimeout(r, 5000));
    """  # scroll to the end of page and sleep 5 seconds to wait lazy load complete
    page_html = make_scrapingant_request(url_string, rapidapi_key, js_snippet=js_snippet, proxy_country=country)
    return extract_data_from_html(page_html)


def fetch_page(page_number, page_url, rapidapi_key, country):
    """Scrape one page, returning (PageOutcome, items).

    Never raises: request/parse errors become PageOutcome.FAILED, a page
    without items becomes PageOutcome.EMPTY.
    """
    try:
        items = extract_items_from_url(page_url, rapidapi_key, country)
    except Exception as exc:
        print(f'page {page_number}: request/parse failed after retries: {exc!r}')
        return PageOutcome.FAILED, []
    if not items:
        return PageOutcome.EMPTY, []
    return PageOutcome.SUCCESS, items


def get_search_results(search_string, rapidapi_key, pages, country):
    """Scrape up to `pages` search pages.

    Returns (items_list, page_reports). page_reports is a list of dicts
    {'page', 'outcome', 'count'} — one entry per attempted page, so every
    page's fate is auditable. Stops paginating at the first FAILED or EMPTY
    page; items already collected are always preserved.
    """
    search_params = urllib.parse.urlencode({'SearchText': search_string})
    search_url = f'https://www.alibaba.com/trade/search?{search_params}'
    items_list = []
    page_reports = []
    for page in range(1, pages + 1):
        page_url = search_url if page == 1 else f'{search_url}&page={page}'
        outcome, new_items = fetch_page(page, page_url, rapidapi_key, country)
        page_reports.append({'page': page, 'outcome': outcome, 'count': len(new_items)})
        if outcome is not PageOutcome.SUCCESS:
            # First page failed -> abort the run; a later page failed/empty ->
            # keep what we have and stop. Either way: do not continue.
            break
        items_list.extend(new_items)
    return items_list, page_reports


def print_run_summary(page_reports, total_items):
    """Print a per-page summary plus totals that must add up."""
    print('\n===== Per-page summary =====')
    for report in page_reports:
        print(f"page {report['page']}: {report['outcome'].value} ({report['count']} items)")
    success_pages = sum(1 for r in page_reports if r['outcome'] is PageOutcome.SUCCESS)
    failed_pages = sum(1 for r in page_reports if r['outcome'] is PageOutcome.FAILED)
    empty_pages = sum(1 for r in page_reports if r['outcome'] is PageOutcome.EMPTY)
    print(f'successful pages: {success_pages}, failed pages: {failed_pages}, '
          f'empty pages: {empty_pages}, total items: {total_items}')


@click.command()
@click.argument('search_string', type=str, required=True, )
@click.option("--rapidapi_key", type=str, required=True,
              help="""\b
              Api key from https://rapidapi.com/okami4kak/api/scrapingant""")
@click.option("--pages", type=int, default=2, help="Number of search pages to parse")
@click.option("--country", type=click.Choice(SUPPORTED_COUNTRIES, case_sensitive=False),
              default="us", help="Country of proxies location ")
def main(search_string, rapidapi_key, pages, country):
    results, page_reports = get_search_results(search_string, rapidapi_key, pages, country)
    print_run_summary(page_reports, len(results))
    if results:
        header = ['item_url', 'listing_title', 'seller_name', 'store_url', 'price', 'image_url', 'seller_location']
        filename = f'data/{search_string}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
        Path("data/").mkdir(parents=True, exist_ok=True)
        with open(filename, "w", newline="") as f:
            cw = csv.DictWriter(f, header, delimiter='\t', quotechar='|', quoting=csv.QUOTE_MINIMAL)
            cw.writeheader()
            cw.writerows(results)
        print(f'Data saved to {filename}')
    else:
        print('no items found')


if __name__ == '__main__':
    main()
