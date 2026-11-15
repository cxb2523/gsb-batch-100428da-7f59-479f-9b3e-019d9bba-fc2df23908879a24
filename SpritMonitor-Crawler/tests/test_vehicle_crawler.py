"""Offline tests for the Spritmonitor crawler.

No network access and no browser is used: pages are local HTML fragments served
through a fake page reader (``reader(url) -> html``).
"""

import csv
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vehicle_crawler as vc  # noqa: E402


# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------

DETAILS = """
<div id="vehicledetails">
  <h1>Mitsubishi - i-MiEV - i-MiEV</h1>
  <span>49 kW</span>
</div>
"""


def _img(alt, label=None):
    if label is None:
        return '<img alt="%s" />' % alt
    return '<img alt="%s" onmouseover="return overlib(\'%s\');" />' % (alt, label)


def _street_imgs(streets):
    return ''.join(_img(street, street) for street in streets)


def make_row(fuel_date='08.02.2019', odometer='12.345', trip='50,7', quantity='6,5',
             fuel_type='Electricity', tire='Winter tires', streets=('City', 'Country roads'),
             style='Normal', consumption='17,8 kWh/100km', fuel_notes=None,
             fuel_date_class='fueldate'):
    """Build one ``itemtable`` row following the site's cell classes."""
    fuel_date_cell = '<td class="%s">%s</td>' % (fuel_date_class, fuel_date) if fuel_date_class \
        else '<td>%s</td>' % fuel_date
    note_cell = '<td class="fuelnote">%s</td>' % (fuel_notes or '')
    return (
        '<tr>'
        + fuel_date_cell
        + '<td class="fuelkmpos">%s</td>' % odometer
        + '<td class="trip">%s</td>' % trip
        + '<td class="quantity">%s</td>' % quantity
        + '<td class="fuelsort" onmouseover="return overlib(\'%s\');">%s</td>' % (fuel_type, fuel_type)
        + '<td class="tire">%s</td>' % _img(tire, tire)
        + '<td class="street">%s</td>' % _street_imgs(streets)
        + '<td class="style">%s</td>' % _img(style, style)
        + '<td class="fuel">&nbsp;</td>'
        + '<td class="consumption">%s</td>' % (_img('consumption', consumption) if consumption else '')
        + note_cell
        + '</tr>'
    )


def make_page(rows, next_page=None, prev_page=None, with_details=True, include_table=True):
    """Build a page.  ``next_page`` adds a pagination link, as the site does."""
    links = []
    if prev_page is not None:
        links.append('<a href="/en/detail/123456.html?page=%d">prev</a>' % prev_page)
    if next_page is not None:
        links.append('<a href="/en/detail/123456.html?page=%d">next</a>' % next_page)
    table = ''
    if include_table:
        table = '<table class="itemtable"><tbody>' + ''.join(rows) + '</tbody></table>'
    return (
        '<html><body>'
        + (DETAILS if with_details else '')
        + table
        + '<div class="paginator">' + ''.join(links) + '</div>'
        + '</body></html>'
    )


class FakeReader:
    """Serve pages by page number extracted from the ``page`` query parameter."""

    def __init__(self, pages):
        self.pages = pages
        self.requested_pages = []

    def __call__(self, url):
        page_number = int(url.rsplit('page=', 1)[1])
        self.requested_pages.append(page_number)
        if page_number not in self.pages:
            raise IOError('page %d does not exist' % page_number)
        return self.pages[page_number]


def read_csv(path):
    with open(path, newline='', encoding='utf-8') as csv_file:
        return list(csv.reader(csv_file))


VEHICLE_URL = 'https://www.spritmonitor.de/en/detail/123456.html'


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_normal_last_page(tmp_path):
    csv_path = str(tmp_path / 'vehicle.csv')
    pages = {
        1: make_page([make_row(fuel_date='08.02.2019'), make_row(fuel_date='07.02.2019')], next_page=2),
        2: make_page([make_row(fuel_date='06.02.2019')]),  # no next link => last page
    }
    reader = FakeReader(pages)

    result = vc.crawl_vehicle(csv_path, VEHICLE_URL, 12.7, page_reader=reader)

    assert [page.page_number for page in result.pages] == [1, 2]
    assert result.termination_reason == vc.REASON_LAST_PAGE
    assert result.pages[-1].termination_reason == vc.REASON_LAST_PAGE
    assert result.written_rows == 3
    assert result.skipped_rows == 0
    assert result.pages[0].written_rows == 2
    assert result.pages[1].written_rows == 1
    assert result.manufacturer == 'Mitsubishi'
    assert result.model == 'i-MiEV'
    assert result.version == 'i-MiEV'
    assert reader.requested_pages == [1, 2]

    rows = read_csv(csv_path)
    assert rows[0] == vc.CSV_HEADER
    assert len(rows) == 1 + 3
    assert rows[1][4] == '08.02.2019'
    assert rows[1][5] == '12345'  # 12.345 interpreted as thousands separator
    assert rows[1][6] == '50.7'
    assert rows[1][7] == '6.5'
    assert rows[1][8] == 'Electricity'
    assert rows[1][9] == 'Winter tires'
    assert rows[1][10:13] == ['1', '0', '1']  # city, motor_way, country_roads
    assert rows[1][13] == 'Normal'
    assert float(rows[1][14]) == 17.8
    assert float(rows[1][18]) == pytest.approx(17.8 - 12.7)


def test_missing_table_stops_and_reports(tmp_path):
    csv_path = str(tmp_path / 'vehicle.csv')
    pages = {
        1: make_page([make_row(), make_row()], next_page=2),
        2: make_page([], with_details=False, include_table=False, next_page=3),
        3: make_page([make_row()]),  # must never be visited
    }
    reader = FakeReader(pages)

    result = vc.crawl_vehicle(csv_path, VEHICLE_URL, 12.7, page_reader=reader)

    assert [page.page_number for page in result.pages] == [1, 2]
    assert result.termination_reason == vc.REASON_TABLE_MISSING
    assert result.stopped_page == 2
    assert result.pages[1].written_rows == 0
    assert reader.requested_pages == [1, 2], 'pages after the missing table must not be requested'

    rows = read_csv(csv_path)
    assert len(rows) == 1 + 2
    assert result.written_rows == len(rows) - 1


def test_non_fueling_rows_are_skipped_and_counted(tmp_path):
    csv_path = str(tmp_path / 'vehicle.csv')
    non_fueling_empty = make_row(fuel_date='')
    non_fueling_no_class = make_row(fuel_date='x', fuel_date_class=None)
    pages = {
        1: make_page([
            non_fueling_empty,
            make_row(fuel_date='08.02.2019'),
            non_fueling_no_class,
            non_fueling_empty,
        ], next_page=2),
        2: make_page([make_row(fuel_date='07.02.2019')]),
    }
    reader = FakeReader(pages)

    result = vc.crawl_vehicle(csv_path, VEHICLE_URL, 12.7, page_reader=reader)

    assert result.termination_reason == vc.REASON_LAST_PAGE
    assert result.pages[0].written_rows == 1
    assert result.pages[0].skipped_rows == 3
    assert result.pages[1].written_rows == 1
    assert result.pages[1].skipped_rows == 0
    assert result.written_rows == 2
    assert result.skipped_rows == 3

    rows = read_csv(csv_path)
    assert len(rows) - 1 == result.written_rows
    assert all(row[4] for row in rows[1:]), 'only rows with a fuel_date are written'


def test_numeric_parse_failures_write_empty_cells(tmp_path):
    csv_path = str(tmp_path / 'vehicle.csv')
    bad_row = make_row(
        fuel_date='09.02.2019',
        odometer='not-a-number',
        trip='---',
        quantity='',
        consumption='n/a kWh/100km',
    )
    pages = {1: make_page([bad_row])}
    reader = FakeReader(pages)

    result = vc.crawl_vehicle(csv_path, VEHICLE_URL, 12.7, page_reader=reader)

    assert result.termination_reason == vc.REASON_LAST_PAGE
    assert result.written_rows == 1, 'a row with a fuel_date is always written'
    assert result.skipped_rows == 0

    rows = read_csv(csv_path)
    assert len(rows) == 1 + 1
    written = rows[1]
    assert written[4] == '09.02.2019'
    assert written[5] == ''  # odometer
    assert written[6] == ''  # trip distance
    assert written[7] == ''  # quantity
    assert written[14] == ''  # consumption
    assert written[18] == ''  # ecr_deviation needs consumption


def test_ecr_deviation_needs_consumption_and_manufacturer_ecr(tmp_path):
    csv_path = str(tmp_path / 'vehicle.csv')
    pages = {1: make_page([make_row(consumption='17,8 kWh/100km')])}
    reader = FakeReader(pages)

    # No factory consumption: consumption is still recorded, deviation stays empty.
    result = vc.crawl_vehicle(csv_path, VEHICLE_URL, None, page_reader=reader)

    rows = read_csv(csv_path)
    assert result.written_rows == 1
    assert rows[1][14] == '17.8'
    assert rows[1][18] == ''


def test_bordcomputer_notes_refine_values(tmp_path):
    csv_path = str(tmp_path / 'vehicle.csv')
    notes = (_img('Bordcomputer', 'Consumption 16,4 Quantity 7,25 speed 52')
             + _img('A/C') + _img('Park heating') + _img('note', 'free text note'))
    pages = {1: make_page([make_row(fuel_date='09.02.2019', quantity='', consumption='', fuel_notes=notes)])}
    reader = FakeReader(pages)

    result = vc.crawl_vehicle(csv_path, VEHICLE_URL, 12.7, page_reader=reader)

    assert result.written_rows == 1
    rows = read_csv(csv_path)
    assert float(rows[1][7]) == 7.25        # quantity from the on-board computer
    assert float(rows[1][14]) == 16.4       # consumption from the on-board computer
    assert rows[1][15] == '1'               # A/C
    assert rows[1][16] == '1'               # park heating
    assert float(rows[1][17]) == 52.0       # average speed
    assert float(rows[1][18]) == pytest.approx(16.4 - 12.7)
    assert rows[1][19] == 'free text note'  # free-text note wins over the last icon


def test_header_is_written_once_across_calls(tmp_path):
    csv_path = str(tmp_path / 'shared.csv')

    first = vc.crawl_vehicle(csv_path, VEHICLE_URL, 12.7, page_reader=FakeReader({
        1: make_page([make_row(fuel_date='08.02.2019')]),
    }))
    second = vc.crawl_vehicle(csv_path, VEHICLE_URL.replace('123456', '654321'), 12.7, page_reader=FakeReader({
        1: make_page([make_row(fuel_date='07.02.2019')]),
    }))

    rows = read_csv(csv_path)
    assert rows[0] == vc.CSV_HEADER
    assert sum(1 for row in rows if row == vc.CSV_HEADER) == 1
    assert len(rows) - 1 == first.written_rows + second.written_rows == 2


def test_other_errors_are_reported_and_keep_written_rows(tmp_path):
    csv_path = str(tmp_path / 'vehicle.csv')

    class ExplodingReader(FakeReader):
        def __call__(self, url):
            page_number = int(url.rsplit('page=', 1)[1])
            self.requested_pages.append(page_number)
            if page_number == 2:
                raise RuntimeError('boom')
            return self.pages[page_number]

    reader = ExplodingReader({1: make_page([make_row(), make_row()], next_page=2)})

    result = vc.crawl_vehicle(csv_path, VEHICLE_URL, 12.7, page_reader=reader)

    assert [page.page_number for page in result.pages] == [1, 2]
    assert result.termination_reason == vc.REASON_OTHER_ERROR
    assert result.pages[-1].error_summary
    assert 'boom' in result.pages[-1].error_summary
    assert result.pages[0].written_rows == 2
    assert result.pages[1].written_rows == 0

    rows = read_csv(csv_path)
    assert len(rows) == 1 + 2, 'rows flushed before the failure must remain on disk'
    assert result.written_rows == len(rows) - 1


def test_page_numbers_start_at_one_and_increment(tmp_path):
    csv_path = str(tmp_path / 'vehicle.csv')
    pages = {
        1: make_page([make_row(fuel_date='08.02.2019')], next_page=2),
        2: make_page([make_row(fuel_date='07.02.2019')], next_page=3, prev_page=1),
        3: make_page([make_row(fuel_date='06.02.2019')], prev_page=2),
    }
    reader = FakeReader(pages)

    result = vc.crawl_vehicle(csv_path, VEHICLE_URL, 12.7, page_reader=reader)

    assert reader.requested_pages == [1, 2, 3]
    assert [page.page_number for page in result.pages] == [1, 2, 3]
    assert result.termination_reason == vc.REASON_LAST_PAGE
    assert result.written_rows == 3
    assert len(read_csv(csv_path)) - 1 == result.written_rows


def test_header_row_inside_table_is_not_counted_as_skipped(tmp_path):
    csv_path = str(tmp_path / 'vehicle.csv')
    header_row = '<tr><th>Date</th><th>Odometer</th><th>Trip</th></tr>'
    pages = {1: make_page([header_row, make_row(fuel_date='08.02.2019')])}
    reader = FakeReader(pages)

    result = vc.crawl_vehicle(csv_path, VEHICLE_URL, 12.7, page_reader=reader)

    assert result.pages[0].written_rows == 1
    assert result.pages[0].skipped_rows == 0
    assert len(read_csv(csv_path)) - 1 == result.written_rows


def test_paginator_links_beyond_next_page_keep_crawling(tmp_path):
    """A "last page" link (e.g. page=3 on page 1) must not end the crawl early."""
    csv_path = str(tmp_path / 'vehicle.csv')
    last_page_link = '<a href="/en/detail/123456.html?page=3">last</a>'
    pages = {
        1: make_page([make_row(fuel_date='08.02.2019')], next_page=2).replace(
            '</body>', last_page_link + '</body>'),
        2: make_page([make_row(fuel_date='07.02.2019')], next_page=3, prev_page=1),
        3: make_page([make_row(fuel_date='06.02.2019')], prev_page=2),
    }
    reader = FakeReader(pages)

    result = vc.crawl_vehicle(csv_path, VEHICLE_URL, 12.7, page_reader=reader)

    assert reader.requested_pages == [1, 2, 3]
    assert result.termination_reason == vc.REASON_LAST_PAGE
    assert result.written_rows == 3
    assert len(read_csv(csv_path)) - 1 == result.written_rows


def test_max_pages_limit(tmp_path):
    csv_path = str(tmp_path / 'vehicle.csv')
    pages = {
        1: make_page([make_row()], next_page=2),
        2: make_page([make_row()], next_page=3),
    }
    reader = FakeReader(pages)

    result = vc.crawl_vehicle(csv_path, VEHICLE_URL, 12.7, page_reader=reader, max_pages=1)

    assert reader.requested_pages == [1]
    assert result.termination_reason == vc.REASON_PAGE_LIMIT
    assert len(read_csv(csv_path)) == 1 + 1


def test_result_serializes_to_dict(tmp_path):
    csv_path = str(tmp_path / 'vehicle.csv')
    result = vc.crawl_vehicle(csv_path, VEHICLE_URL, 12.7, page_reader=FakeReader({
        1: make_page([make_row(), make_row(fuel_date='')]),
    }))

    serialized = result.to_dict()
    assert serialized['termination_reason'] == vc.REASON_LAST_PAGE
    assert serialized['written_rows'] == 1
    assert serialized['skipped_rows'] == 1
    assert serialized['pages'][0]['page_number'] == 1
    assert set(serialized['pages'][0]) == {'page_number', 'written_rows', 'skipped_rows',
                                           'termination_reason', 'error_summary'}
