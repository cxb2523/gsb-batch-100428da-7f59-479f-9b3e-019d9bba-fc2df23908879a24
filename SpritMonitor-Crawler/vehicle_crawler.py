"""Spritmonitor.de EV charging-record crawler.

The crawl is split into a pure parsing part and a replaceable *page reader*
(any callable ``reader(url) -> html``).  This keeps the module usable from other
collection flows and makes the whole flow testable without a browser or network.

Public API
----------
``crawl_vehicle(csv_path, url, manufacturer_ecr, page_reader=None, ...)``
    Crawl all pages of one vehicle and return a :class:`CrawlResult` that
    contains one :class:`PageResult` per processed page.
``initialize_csv_reader(path)`` / ``append_to_csv(record, csv_file, writer)``
    Low level CSV helpers kept for backwards compatibility.
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

# The column order below is the on-disk contract.  Do not change it: existing
# consumers rely on both the order and the meaning of these fields.
CSV_HEADER = ['manufacturer', 'model', 'version', 'power(kW)', 'fuel_date', 'odometer', 'trip_distance(km)',
              'quantity(kWh)', 'fuel_type', 'tire_type', 'city', 'motor_way', 'country_roads', 'driving_style',
              'consumption(kWh/100km)', 'A/C', 'park_heating', 'avg_speed(km/h)', 'ecr_deviation', 'fuel_note']
csv_header = CSV_HEADER  # backwards compatible name

DEFAULT_ENCODING = 'utf-8'

# Termination reasons returned in PageResult.termination_reason.  Pages that are
# followed by another page carry ``None`` (no termination happened there).
REASON_LAST_PAGE = '正常末页'
REASON_TABLE_MISSING = '表格缺失'
REASON_OTHER_ERROR = '其他异常'
REASON_PAGE_LIMIT = '达到页数上限'  # only used when the caller sets max_pages

# How many times to wait for the page after driver.get() in the default reader.
DEFAULT_WAIT_SECONDS = 2.0

PageReader = Callable[[str], str]


@dataclass
class PageResult:
    """Outcome of one crawled page."""

    page_number: int
    written_rows: int
    skipped_rows: int
    termination_reason: Optional[str] = None
    error_summary: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'page_number': self.page_number,
            'written_rows': self.written_rows,
            'skipped_rows': self.skipped_rows,
            'termination_reason': self.termination_reason,
            'error_summary': self.error_summary,
        }


@dataclass
class CrawlResult:
    """Result of a whole vehicle crawl, including the per-page outcomes."""

    url: str
    csv_path: str
    manufacturer: Optional[str]
    model: Optional[str]
    version: Optional[str]
    pages: List[PageResult]

    @property
    def written_rows(self) -> int:
        return sum(page.written_rows for page in self.pages)

    @property
    def skipped_rows(self) -> int:
        return sum(page.skipped_rows for page in self.pages)

    @property
    def stopped_page(self) -> Optional[int]:
        return self.pages[-1].page_number if self.pages else None

    @property
    def termination_reason(self) -> Optional[str]:
        return self.pages[-1].termination_reason if self.pages else None

    @property
    def error_summary(self) -> Optional[str]:
        return self.pages[-1].error_summary if self.pages else None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'url': self.url,
            'csv_path': self.csv_path,
            'manufacturer': self.manufacturer,
            'model': self.model,
            'version': self.version,
            'written_rows': self.written_rows,
            'skipped_rows': self.skipped_rows,
            'stopped_page': self.stopped_page,
            'termination_reason': self.termination_reason,
            'error_summary': self.error_summary,
            'pages': [page.to_dict() for page in self.pages],
        }


class SeleniumPageReader:
    """Default page reader backed by Selenium/Chrome.

    Selenium is imported lazily so that the module (and its tests) can be used
    without selenium installed as long as another page reader is supplied.
    """

    def __init__(self, driver: Any = None, wait_seconds: float = DEFAULT_WAIT_SECONDS):
        self._driver = driver
        self._owns_driver = driver is None
        self.wait_seconds = wait_seconds

    @property
    def driver(self) -> Any:
        return self._driver

    def _ensure_driver(self) -> Any:
        if self._driver is None:
            from selenium import webdriver  # imported lazily, see class docstring
            self._driver = webdriver.Chrome()
        return self._driver

    def read(self, url: str) -> str:
        driver = self._ensure_driver()
        driver.get(url)
        if self.wait_seconds:
            time.sleep(self.wait_seconds)
        return driver.page_source

    def __call__(self, url: str) -> str:
        return self.read(url)

    def close(self) -> None:
        """Quit the browser, but only if this reader created it."""
        if self._owns_driver and self._driver is not None:
            try:
                self._driver.quit()
            finally:
                self._driver = None


def initialize_csv_reader(path, encoding: str = DEFAULT_ENCODING):
    """Open ``path`` for appending and write the header only if the file is empty.

    Calling this repeatedly on the same path never appends a second header.
    """
    csv_file = open(path, mode='a+', newline='', encoding=encoding)
    csv_file.seek(0, os.SEEK_END)
    writer = csv.writer(csv_file, delimiter=',', quotechar='"')
    if csv_file.tell() == 0:
        writer.writerow(CSV_HEADER)
        csv_file.flush()
    return writer, csv_file


def append_to_csv(record, csv_file, writer) -> None:
    writer.writerow(record)
    csv_file.flush()


def _page_url(url: str, page_number: int) -> str:
    """Build the URL for a page, always addressing it through the page parameter."""
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != 'page']
    query.append(('page', str(page_number)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _exception_summary(exc: BaseException, limit: int = 500) -> str:
    message = str(exc).strip()
    summary = '%s: %s' % (type(exc).__name__, message) if message else type(exc).__name__
    if len(summary) > limit:
        summary = summary[:limit - 3] + '...'
    return summary


# ---------------------------------------------------------------------------
# HTML parsing helpers
# ---------------------------------------------------------------------------

def _find_item_table(soup: BeautifulSoup):
    """Return the ``table.itemtable`` element, or ``None`` if it is absent."""
    for table in soup.find_all('table'):
        classes = table.get('class') or []
        if 'itemtable' in classes:
            return table
    return None


def _table_rows(table) -> List[Any]:
    tbody = table.find('tbody')
    return (tbody if tbody is not None else table).find_all('tr')


def _cell(row, class_name: str):
    return row.find('td', class_=class_name)


def _cell_text(cell) -> Optional[str]:
    if cell is None:
        return None
    return cell.get_text().strip()


def _mouseover_label(element) -> Optional[str]:
    """Extract the first quoted label of an ``onmouseover`` handler."""
    if element is None:
        return None
    raw = element.get('onmouseover')
    if not raw:
        return None
    parts = raw.split("'")
    if len(parts) < 2:
        return None
    return parts[1]


def _cell_label(cell) -> Optional[str]:
    """Label of a cell whose ``onmouseover`` sits on the cell or on its icon."""
    label = _mouseover_label(cell)
    if label is None and cell is not None:
        label = _mouseover_label(cell.find('img'))
    return label


def _parse_float(text: Optional[str]) -> Optional[float]:
    """Parse a decimal number that may use a comma as decimal separator."""
    if text is None:
        return None
    text = text.strip().replace(',', '.')
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _parse_odometer(text: Optional[str]) -> Optional[int]:
    """Parse the odometer cell, interpreting a dot as a thousands separator."""
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    try:
        if '.' in text:
            return int(float(text) * 1000)
        return int(text)
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_vehicle_details(soup: BeautifulSoup):
    """Return ``(manufacturer, model, version, engine_power)`` from the detail header."""
    details = soup.find(id='vehicledetails')
    if details is None:
        return None, None, None, None

    h1 = details.find('h1')
    title = h1.get_text().strip() if h1 is not None else ''
    parts = [part.strip() for part in title.split(' - ')] if title else []
    manufacturer = parts[0] if parts and parts[0] else None
    model = parts[1] if len(parts) > 1 and parts[1] else None
    version = parts[2] if len(parts) > 2 and parts[2] else None

    engine_power = None
    words = details.get_text().split()
    for index, word in enumerate(words):
        if word == 'kW' and index > 0:
            engine_power = words[index - 1]

    return manufacturer, model, version, engine_power


def _has_next_page(soup: BeautifulSoup, current_page: int) -> bool:
    """Whether the page still links to a later page through the page parameter.

    The site paginates with ``?page=N`` links.  Any link addressing a page
    number greater than ``current_page`` (next arrow, numbered paginator,
    "last page" link) means there is more to crawl.  A document without any
    page-parameter link is a single-page listing, which counts as the normal
    last page.
    """
    pattern = re.compile(r'page=(\d+)')
    for link in soup.find_all('a', href=True):
        for match in pattern.finditer(link['href']):
            if int(match.group(1)) > current_page:
                return True
    return False


def _parse_row(row, context: Dict[str, Any]) -> Optional[List[Any]]:
    """Parse one table row.

    Returns ``None`` for non-fueling rows (including row headers and rows with
    an empty ``fuel_date``).  Numeric fields that cannot be parsed are written
    as ``None`` (an empty CSV cell) instead of raising.
    """
    fuel_date = _cell_text(_cell(row, 'fueldate'))
    if not fuel_date:
        # Non-fueling record: skip the whole row.
        return None

    odometer = _parse_odometer(_cell_text(_cell(row, 'fuelkmpos')))
    distance = _parse_float(_cell_text(_cell(row, 'trip')))
    quantity = _parse_float(_cell_text(_cell(row, 'quantity')))

    fuel_type = _cell_label(_cell(row, 'fuelsort'))

    tire_cell = _cell(row, 'tire')
    tire_type = None
    if tire_cell is not None:
        tire_type = _cell_label(tire_cell)

    street_cell = _cell(row, 'street')
    if street_cell is None:
        city = motor_way = country_roads = None
    else:
        city = country_roads = motor_way = 0
        for street_img in street_cell.find_all('img'):
            label = _mouseover_label(street_img)
            if label == 'City':
                city = 1
            if label == 'Motor-way':
                motor_way = 1
            if label == 'Country roads':
                country_roads = 1

    style_cell = _cell(row, 'style')
    driving_style = _cell_label(style_cell) if style_cell is not None else None

    consumption_cell = _cell(row, 'consumption')
    consumption = None
    consumption_label = _cell_label(consumption_cell)
    if consumption_label:
        consumption = _parse_float(consumption_label.split(' ')[0])

    avg_speed = None
    ac = park_heating = 0
    fuel_note = None
    fuel_note_cell = _cell(row, 'fuelnote')
    if fuel_note_cell is not None:
        for fuel_note_img in fuel_note_cell.find_all('img'):
            alt = fuel_note_img.get('alt')
            if alt == 'Bordcomputer':
                bordcomputer = _mouseover_label(fuel_note_img)
                words = bordcomputer.split() if bordcomputer else []
                for index, word in enumerate(words):
                    following = words[index + 1] if index + 1 < len(words) else None
                    if 'Consumption' in word:
                        consumption = _parse_float(following)
                    elif 'Quantity' in word:
                        quantity = _parse_float(following)
                    elif 'speed' in word:
                        avg_speed = _parse_float(following)
            elif alt == 'A/C':
                ac = 1
            elif alt == 'Park heating':
                park_heating = 1
            else:
                fuel_note = _mouseover_label(fuel_note_img)

    manufacturer_ecr = context['manufacturer_ecr']
    if consumption is not None and manufacturer_ecr is not None:
        try:
            ecr_deviation = consumption - manufacturer_ecr
        except (TypeError, ValueError, OverflowError):
            ecr_deviation = None
    else:
        ecr_deviation = None

    return [context['manufacturer'], context['model'], context['version'], context['engine_power'],
            fuel_date, odometer, distance, quantity, fuel_type, tire_type, city, motor_way, country_roads,
            driving_style, consumption, ac, park_heating, avg_speed, ecr_deviation, fuel_note]


# ---------------------------------------------------------------------------
# Main crawl
# ---------------------------------------------------------------------------

def crawl_vehicle(csv_path, url: str, manufacturer_ecr: Optional[float], page_reader: Optional[PageReader] = None,
                  *, max_pages: Optional[int] = None, driver: Any = None,
                  wait_seconds: float = DEFAULT_WAIT_SECONDS, encoding: str = DEFAULT_ENCODING) -> CrawlResult:
    """Crawl the fueling records of one vehicle and append them to ``csv_path``.

    Parameters
    ----------
    csv_path:
        Target CSV file.  The header is written only when the file is empty, so
        calling this repeatedly (e.g. for several vehicles) is safe.
    url:
        Vehicle detail URL, e.g. ``https://www.spritmonitor.de/en/detail/786327.html``.
    manufacturer_ecr:
        Factory energy consumption used to compute ``ecr_deviation``.  ``None``
        (or an unparsable value) leaves the deviation empty.
    page_reader:
        Optional callable ``reader(url) -> html``.  When omitted a Selenium/Chrome
        reader is created (and closed) internally.  Pass a fake reader in tests.
    max_pages:
        Optional safety limit; reaching it stops with ``REASON_PAGE_LIMIT``.
    driver:
        Optional externally owned Selenium driver used by the default reader.
        Such a driver is never quit by this function.
    wait_seconds:
        Delay applied after each request by the default Selenium reader.
    encoding:
        Encoding used to open the CSV file.

    Returns
    -------
    CrawlResult
        Contains one ``PageResult`` per processed page (including the page where
        crawling stopped), plus running totals.  Every written row is flushed, so
        rows written before an abnormal stop stay in the CSV.
    """
    if not url:
        raise ValueError('url must be a non-empty string')
    if csv_path is None or str(csv_path) == '':
        raise ValueError('csv_path must be a non-empty path')
    if max_pages is not None and max_pages < 1:
        raise ValueError('max_pages must be >= 1 when provided')

    csv_path_str = os.fspath(csv_path)
    manufacturer_ecr = _parse_float(manufacturer_ecr) if not isinstance(manufacturer_ecr, (int, float)) else float(
        manufacturer_ecr)

    owns_reader = page_reader is None
    reader = SeleniumPageReader(driver=driver, wait_seconds=wait_seconds) if owns_reader else page_reader

    pages: List[PageResult] = []
    manufacturer = model = version = engine_power = None
    details_loaded = False
    csv_file = None

    try:
        writer, csv_file = initialize_csv_reader(csv_path_str, encoding=encoding)
        context: Dict[str, Any] = {
            'manufacturer': None,
            'model': None,
            'version': None,
            'engine_power': None,
            'manufacturer_ecr': manufacturer_ecr,
        }

        page_number = 1
        while True:
            page_url = _page_url(url, page_number)
            stats = {'written': 0, 'skipped': 0}
            try:
                html = reader(page_url)
                soup = BeautifulSoup(html, 'html.parser')

                if not details_loaded:
                    manufacturer, model, version, engine_power = _parse_vehicle_details(soup)
                    # Keep retrying on later pages until the header is actually found.
                    details_loaded = manufacturer is not None
                context['manufacturer'] = manufacturer
                context['model'] = model
                context['version'] = version
                context['engine_power'] = engine_power

                table = _find_item_table(soup)
                if table is None:
                    # The requested page has no fueling table: stop here and do
                    # not touch any later page.
                    pages.append(PageResult(page_number, stats['written'], stats['skipped'],
                                            termination_reason=REASON_TABLE_MISSING))
                    break

                for row in _table_rows(table):
                    if not row.find_all('td'):
                        # Structural rows (e.g. a <th> header row) carry no record
                        # and are not counted as skipped fueling rows.
                        continue
                    record = _parse_row(row, context)
                    if record is None:
                        stats['skipped'] += 1
                    else:
                        append_to_csv(record=record, csv_file=csv_file, writer=writer)
                        stats['written'] += 1

                has_next_page = _has_next_page(soup, page_number)
                if not has_next_page:
                    pages.append(PageResult(page_number, stats['written'], stats['skipped'],
                                            termination_reason=REASON_LAST_PAGE))
                    break
                if max_pages is not None and page_number >= max_pages:
                    pages.append(PageResult(page_number, stats['written'], stats['skipped'],
                                            termination_reason=REASON_PAGE_LIMIT))
                    break

                pages.append(PageResult(page_number, stats['written'], stats['skipped']))
                page_number += 1
            except Exception as exc:  # noqa: BLE001 - the crawl must report, not crash
                # Anything else (network error, unparsable page, ...) is reported
                # as an abnormal stop.  Flushed rows are already on disk.
                pages.append(PageResult(page_number, stats['written'], stats['skipped'],
                                        termination_reason=REASON_OTHER_ERROR,
                                        error_summary=_exception_summary(exc)))
                break

        return CrawlResult(url=url, csv_path=csv_path_str, manufacturer=manufacturer, model=model,
                           version=version, pages=pages)
    finally:
        if csv_file is not None:
            try:
                csv_file.flush()
            finally:
                csv_file.close()
        if owns_reader:
            reader.close()


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Crawl spritmonitor.de fueling records into a CSV file.')
    parser.add_argument('csv_path', help='target CSV file (created with a header when empty)')
    parser.add_argument('url', help='vehicle detail URL, e.g. https://www.spritmonitor.de/en/detail/123456.html')
    parser.add_argument('manufacturer_ecr', type=float, help="factory's defined consumption in kWh/100km")
    parser.add_argument('--max-pages', type=int, default=None, help='optional page limit')
    parser.add_argument('--wait-seconds', type=float, default=DEFAULT_WAIT_SECONDS,
                        help='delay after each browser request')
    parser.add_argument('--encoding', default=DEFAULT_ENCODING, help='CSV encoding (default: utf-8)')
    return parser


def main(argv=None) -> int:
    args = _build_arg_parser().parse_args(argv)
    result = crawl_vehicle(args.csv_path, args.url, args.manufacturer_ecr,
                           max_pages=args.max_pages, wait_seconds=args.wait_seconds, encoding=args.encoding)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.termination_reason == REASON_LAST_PAGE else 1


if __name__ == '__main__':
    sys.exit(main())
