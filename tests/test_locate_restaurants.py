"""Tests for locate_restaurants using locally-built pages and a fake geocoder."""

import json

import pytest

import locate_restaurants as lr


def index_page(names):
    items = ''.join(
        '<li><div><a href="http://example.com/{0}">{0}</a></div></li>'.format(name)
        for name in names)
    return ('<html><body><h3>Current Favorites</h3><ul>{}</ul></body></html>'
            .format(items))


def restaurant_page(address):
    return ('<html><body><a href="https://www.google.com/maps/place/x">{}</a>'
            '</body></html>'.format(address))


def restaurant_page_without_address():
    return '<html><body><p>No map link here.</p><a href="/other">other</a></body></html>'


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeGeocoded:
    """Mimics a geocoder result: falsy on failure, with status/geometry/confidence."""

    def __init__(self, ok=True, status='OK', coordinates=None, confidence=9):
        self.ok = ok
        self.status = status
        self.geometry = {} if coordinates is None else {'coordinates': coordinates}
        self.confidence = confidence

    def __bool__(self):
        return self.ok


def make_fetch(pages):
    """pages: {url: html-string}; a string starting with '!' raises that error."""
    def fetch(url):
        page = pages[url]
        if page.startswith('!'):
            raise ConnectionError(page[1:])
        return FakeResponse(page)
    return fetch


def no_sleep(_seconds):
    return None


GOOD_GEOCODE = FakeGeocoded(coordinates=[1.0, 2.0], confidence=9)


def run(tmp_path, pages, geocode):
    fetch = make_fetch(pages)
    locations, failures = lr.collect_locations(
        fetch, geocode, sleep=no_sleep, attempts=2, wait=0)
    out = tmp_path / 'locations.json'
    lr.write_locations(locations, path=str(out))
    lr.log_summary(locations, failures)
    return locations, failures, json.loads(out.read_text())


def test_partial_failures_keep_successes_and_record_reasons(tmp_path, caplog):
    pages = {
        lr.BASE_URL: index_page(['Alpha', 'Beta', 'Gamma', 'Delta', 'Epsilon']),
        'http://example.com/Alpha': restaurant_page('1 Main St, Springfield, IL'),
        'http://example.com/Beta': '!connection reset',
        'http://example.com/Gamma': restaurant_page_without_address(),
        'http://example.com/Delta': restaurant_page('4 Oak Ave, Springfield, IL'),
        'http://example.com/Epsilon': restaurant_page('9 Pine Rd, Springfield, IL'),
    }

    def geocode(query):
        if query.startswith('4 Oak Ave'):
            return FakeGeocoded(ok=False, status='ZERO_RESULTS')  # retries exhausted
        if query.startswith('9 Pine Rd'):
            return FakeGeocoded(coordinates=[5.0, 6.0], confidence=3)  # low confidence
        return GOOD_GEOCODE

    locations, failures, written = run(tmp_path, pages, geocode)

    # The one fully successful restaurant is still written, sorted by name
    assert [loc['name'] for loc in written] == ['Alpha']
    assert written[0] == {
        'name': 'Alpha',
        'url': 'http://example.com/Alpha',
        'address': '1 Main St, Springfield, IL',
        'coordinates': [1.0, 2.0],
    }

    reasons = {f['name']: f['reason'] for f in failures}
    assert reasons == {
        'Beta': lr.FETCH_FAILED,
        'Gamma': lr.ADDRESS_NOT_FOUND,
        'Delta': lr.GEOCODING_FAILED,
        'Epsilon': lr.LOW_CONFIDENCE,
    }
    # Failures keep name and source URL for auditing
    for failure in failures:
        assert failure['url'] == 'http://example.com/' + failure['name']

    # The summary mentions the success count and every failed restaurant
    summary = '\n'.join(record.getMessage() for record in caplog.records)
    assert '1 restaurant(s) written, 4 failed' in summary
    for name in ('Beta', 'Gamma', 'Delta', 'Epsilon'):
        assert name in summary


def test_all_failures_write_empty_array(tmp_path):
    pages = {
        lr.BASE_URL: index_page(['Alpha', 'Beta']),
        'http://example.com/Alpha': '!timeout',
        'http://example.com/Beta': restaurant_page_without_address(),
    }
    locations, failures, written = run(
        tmp_path, pages, lambda query: GOOD_GEOCODE)

    assert locations == []
    assert len(failures) == 2
    assert written == []  # valid empty JSON array, not a crash


def test_request_denied_aborts_whole_run(tmp_path):
    pages = {
        lr.BASE_URL: index_page(['Alpha', 'Beta']),
        'http://example.com/Alpha': restaurant_page('1 Main St, Springfield, IL'),
        'http://example.com/Beta': restaurant_page('2 Elm St, Springfield, IL'),
    }
    fetch = make_fetch(pages)

    def geocode(query):
        return FakeGeocoded(ok=False, status='REQUEST_DENIED')

    with pytest.raises(lr.MissingApiKeyError):
        lr.collect_locations(fetch, geocode, sleep=no_sleep, attempts=2, wait=0)

    # The run aborted before writing anything
    assert not (tmp_path / 'locations.json').exists()


def test_geocoding_retries_until_success(tmp_path):
    pages = {
        lr.BASE_URL: index_page(['Alpha']),
        'http://example.com/Alpha': restaurant_page('1 Main St, Springfield, IL'),
    }
    calls = []

    def geocode(query):
        calls.append(query)
        if len(calls) < 2:
            return FakeGeocoded(ok=False, status='OVER_QUERY_LIMIT')
        return GOOD_GEOCODE

    locations, failures, written = run(tmp_path, pages, geocode)

    assert len(calls) == 2
    assert failures == []
    assert [loc['name'] for loc in written] == ['Alpha']


def test_output_sorted_by_name(tmp_path):
    pages = {
        lr.BASE_URL: index_page(['Zeta', 'Alpha', 'Mu']),
        'http://example.com/Zeta': restaurant_page('1 Main St, Springfield, IL'),
        'http://example.com/Alpha': restaurant_page('2 Elm St, Springfield, IL'),
        'http://example.com/Mu': restaurant_page('3 Oak Ave, Springfield, IL'),
    }
    locations, failures, written = run(tmp_path, pages, lambda query: GOOD_GEOCODE)

    assert failures == []
    assert [loc['name'] for loc in written] == ['Alpha', 'Mu', 'Zeta']
