import csv
import json
import os
import sys

import pytest
import requests
from scrapy.http import HtmlResponse, Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from is24crawler.collect import collect_page, retry_pending  # noqa: E402
from is24crawler.csvstore import CSV_FIELDS, CSV_DELIMITER  # noqa: E402
from is24crawler.geocoding import (GeocodeError, GoogleMapsGeocodingClient, REASON_AMBIGUOUS_RESULT,  # noqa: E402
                                   REASON_INCOMPLETE_ADDRESS, REASON_REQUEST_ERROR, geocode_address)
from is24crawler.pending import PendingStore, new_pending_record  # noqa: E402
from is24crawler.pipelines import CsvWriterPipeline  # noqa: E402
from is24crawler.spiders.immoscout24bot import Immoscout24Bot  # noqa: E402

MARKER_VALID = "Soldiner"
MARKER_OTHER_VALID = "Naugarder"
MARKER_AMBIGUOUS = "Hauptstrasse"
MARKER_FAILING = "Teststrasse"

DATE = "2017-06-24"
VALID_ADDRESS = "Soldiner Strasse 20, Wedding (Wedding), Berlin"
OTHER_VALID_ADDRESS = "Naugarder Strasse 14, Prenzlauer Berg (Prenzlauer Berg), Berlin"
INCOMPLETE_ADDRESS = "Soldiner Strasse 20, Berlin"
AMBIGUOUS_ADDRESS = "Hauptstrasse 1, Mitte (Mitte), Berlin"
FAILING_ADDRESS = "Teststrasse 3, Kreuzberg (Kreuzberg), Berlin"

LOCATION = {"lat": 52.55863, "lng": 13.38832}


def result(payload_location = None, types = ("street_address",)):
    return {"types": list(types), "geometry": {"location": payload_location or LOCATION}}


def ok_response(*results):
    return {"status": "OK", "results": list(results) or [result()]}


class FakeGeocoder(object):
    """Maps requests to canned responses/errors so no real Google request is made."""

    def __init__(self, responses):
        self.responses = responses
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        for marker, response in self.responses.items():
            if marker in url:
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError("unexpected geocoding request: {}".format(url))


def page_criteria(count):
    return [value for _ in range(count) for value in ("1000 EUR", "50,0 m²", "2")]


def write_csv(path, rows, fields = CSV_FIELDS):
    with open(path, "w", encoding = "utf-8", newline = "") as handle:
        writer = csv.writer(handle, delimiter = CSV_DELIMITER)
        for row in rows:
            writer.writerow([row[field] for field in fields])


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding = "utf-8", newline = "") as handle:
        return [dict(zip(CSV_FIELDS, row)) for row in csv.reader(handle, delimiter = CSV_DELIMITER)]


def read_pending(path):
    with open(path, "r", encoding = "utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


@pytest.fixture
def paths(tmp_path):
    return {"csv": str(tmp_path / "apartments.csv"), "pending": str(tmp_path / "apartments-pending.jsonl")}


# -- collect_page ---------------------------------------------------------


def test_collect_page_keeps_failures_with_reasons_and_continues_after_request_error():
    addresses = [VALID_ADDRESS, INCOMPLETE_ADDRESS, AMBIGUOUS_ADDRESS, FAILING_ADDRESS, OTHER_VALID_ADDRESS]
    geocoder = FakeGeocoder({
        MARKER_OTHER_VALID: ok_response(result({"lat": 52.5442976, "lng": 13.4382068}, ("premise",))),
        MARKER_VALID: ok_response(),
        MARKER_AMBIGUOUS: ok_response(result(types = ("locality",)), result(types = ("route",))),
        MARKER_FAILING: requests.ConnectionError("connection refused"),
    })

    apartments, failures = collect_page(page_criteria(len(addresses)), addresses, DATE, geocoder, "2017-06-24T10:00:00")

    # the request error for FAILING_ADDRESS must not abort the page: the last address is still processed
    assert len(apartments) == 2
    assert [failure["address"] for failure in failures] == [INCOMPLETE_ADDRESS, AMBIGUOUS_ADDRESS, FAILING_ADDRESS]
    reasons = dict((failure["address"], failure["reason"]) for failure in failures)
    assert reasons[INCOMPLETE_ADDRESS] == REASON_INCOMPLETE_ADDRESS
    assert reasons[AMBIGUOUS_ADDRESS] == REASON_AMBIGUOUS_RESULT
    assert reasons[FAILING_ADDRESS] == REASON_REQUEST_ERROR
    assert all(failure["attempts"] == 1 for failure in failures)
    assert all(failure["price"] == 1000.0 and failure["size"] == 50.0 and failure["rooms"] == 2.0 for failure in failures)
    # all four addresses that could be parsed were requested exactly once
    assert len(geocoder.urls) == 4


def test_collect_page_marks_non_json_response_as_request_error():
    geocoder = FakeGeocoder({
        MARKER_VALID: GeocodeError(REASON_REQUEST_ERROR, "invalid json"),
    })
    apartments, failures = collect_page(page_criteria(2), [VALID_ADDRESS, INCOMPLETE_ADDRESS], DATE, geocoder)
    assert apartments == []
    assert [failure["reason"] for failure in failures] == [REASON_REQUEST_ERROR, REASON_INCOMPLETE_ADDRESS]


def test_parse_criteria_failures_are_not_queued_as_geocode_failures():
    apartments, failures = collect_page(["1000 EUR", "50,0 m²", "2", "n.V.", "-", "2"], [VALID_ADDRESS, VALID_ADDRESS],
                                        DATE, FakeGeocoder({MARKER_VALID: ok_response()}))
    assert [apartment["address"] for apartment in apartments] == [VALID_ADDRESS]
    assert failures == []


# -- request layer --------------------------------------------------------


class FakeGet(object):
    def __init__(self, response = None, error = None):
        self.response = response
        self.error = error

    def __call__(self, url, **kwargs):
        if self.error is not None:
            raise self.error
        return self.response


class FakeResponse(object):
    def __init__(self, payload = None, status_code = 200, text = "not json"):
        self.payload = payload
        self.status_code = status_code
        self.ok = status_code < 400
        self.text = text

    def json(self):
        if self.payload is None:
            raise ValueError("No JSON object could be decoded")
        return self.payload


def test_google_client_normalises_connection_errors():
    client = GoogleMapsGeocodingClient(get = FakeGet(error = requests.ConnectionError("boom")), min_delay = 0,
                                       max_delay = 0, sleep = lambda seconds: None)
    with pytest.raises(GeocodeError) as caught:
        client("http://example.test/geocode")
    assert caught.value.reason == REASON_REQUEST_ERROR


def test_google_client_normalises_non_json_answers():
    client = GoogleMapsGeocodingClient(get = FakeGet(response = FakeResponse()), min_delay = 0, max_delay = 0,
                                       sleep = lambda seconds: None)
    with pytest.raises(GeocodeError) as caught:
        client("http://example.test/geocode")
    assert caught.value.reason == REASON_REQUEST_ERROR


def test_google_client_normalises_http_errors():
    client = GoogleMapsGeocodingClient(get = FakeGet(response = FakeResponse(payload = {"results": []}, status_code = 500)),
                                       min_delay = 0, max_delay = 0, sleep = lambda seconds: None)
    with pytest.raises(GeocodeError) as caught:
        client("http://example.test/geocode")
    assert caught.value.reason == REASON_REQUEST_ERROR


def test_google_client_throttles_between_calls_but_not_before_the_first():
    sleeps = []
    client = GoogleMapsGeocodingClient(get = FakeGet(response = FakeResponse(payload = ok_response())), min_delay = 2,
                                       max_delay = 3, sleep = sleeps.append)
    assert geocode_address(VALID_ADDRESS, client) == (52.55863, 13.38832)
    assert sleeps == []
    geocode_address(VALID_ADDRESS, client)
    assert len(sleeps) == 1 and 2 <= sleeps[0] <= 3


# -- pipeline -------------------------------------------------------------


def test_pipeline_writes_successes_to_csv_and_failures_to_pending_file(paths):
    pipeline = CsvWriterPipeline(csv_path = paths["csv"], pending_path = paths["pending"])
    pipeline.open_spider(spider = None)

    error = GeocodeError(REASON_AMBIGUOUS_RESULT, "two exact matches")
    failure = new_pending_record(1000.0, 50.0, 2.0, AMBIGUOUS_ADDRESS, DATE, error, "2017-06-24T10:00:00")
    good = {"price": 1000.0, "size": 50.0, "rooms": 2.0, "address": VALID_ADDRESS, "lat": 52.55863, "lng": 13.38832,
            "zone": 33, "band": "U", "east": 390743.5, "north": 5824393.7, "date": DATE}
    pipeline.process_item({"apartments": [good], "failures": [failure], "page": "1"}, spider = None)
    pipeline.close_spider(spider = None)

    rows = read_csv(paths["csv"])
    assert len(rows) == 1 and rows[0]["address"] == VALID_ADDRESS
    pending = read_pending(paths["pending"])
    assert len(pending) == 1
    assert pending[0]["address"] == AMBIGUOUS_ADDRESS
    assert pending[0]["reason"] == REASON_AMBIGUOUS_RESULT
    assert pending[0]["detail"] == "two exact matches"
    assert pending[0]["price"] == 1000.0


def test_pipeline_does_not_duplicate_rows_when_page_is_processed_again(paths):
    good = {"price": 1000.0, "size": 50.0, "rooms": 2.0, "address": VALID_ADDRESS, "lat": 52.55863, "lng": 13.38832,
            "zone": 33, "band": "U", "east": 1.0, "north": 2.0, "date": DATE}
    error = GeocodeError(REASON_INCOMPLETE_ADDRESS, "missing district")
    failure = new_pending_record(1000.0, 50.0, 2.0, INCOMPLETE_ADDRESS, DATE, error, "2017-06-24T10:00:00")

    for _ in range(2):
        pipeline = CsvWriterPipeline(csv_path = paths["csv"], pending_path = paths["pending"])
        pipeline.open_spider(spider = None)
        pipeline.process_item({"apartments": [good, good], "failures": [failure, failure], "page": "1"}, spider = None)
        pipeline.close_spider(spider = None)

    assert len(read_csv(paths["csv"])) == 1
    assert len(read_pending(paths["pending"])) == 1


def test_pipeline_keeps_existing_rows_untouched(paths):
    write_csv(paths["csv"], [{"price": 900.0, "size": 40.0, "rooms": 1.0, "address": "Old Row, Mitte (Mitte), Berlin",
                              "lat": 52.5, "lng": 13.3, "zone": 33, "band": "U", "east": 1.0, "north": 2.0,
                              "date": "2017-06-01"}])
    pipeline = CsvWriterPipeline(csv_path = paths["csv"], pending_path = paths["pending"])
    pipeline.open_spider(spider = None)
    pipeline.process_item({"apartments": [], "failures": [], "page": "1"}, spider = None)
    pipeline.close_spider(spider = None)
    assert len(read_csv(paths["csv"])) == 1


# -- retry ----------------------------------------------------------------


def test_retry_recovers_append_to_csv_and_removes_from_pending(paths):
    error = GeocodeError(REASON_REQUEST_ERROR, "connection refused")
    recovered = new_pending_record(1000.0, 50.0, 2.0, VALID_ADDRESS, DATE, error, "2017-06-24T10:00:00")
    still_failing = new_pending_record(1000.0, 50.0, 2.0, INCOMPLETE_ADDRESS, DATE, error, "2017-06-24T10:00:00")
    pending = PendingStore(paths["pending"])
    pending.add(recovered).add(still_failing).save()
    write_csv(paths["csv"], [])

    geocoder = FakeGeocoder({MARKER_VALID: ok_response(), INCOMPLETE_ADDRESS: ok_response()})
    # second address is still returned by geocoder, but the crawl-side splitter rejects it
    geocoder.responses[INCOMPLETE_ADDRESS] = GeocodeError(REASON_INCOMPLETE_ADDRESS, "still incomplete")

    summary = retry_pending(geocoder, paths["csv"], paths["pending"], "2017-06-25T09:00:00")

    assert summary == {"retried": 2, "recovered": 1, "skipped": 0, "failed": 1}
    rows = read_csv(paths["csv"])
    assert len(rows) == 1
    assert rows[0]["address"] == VALID_ADDRESS
    assert float(rows[0]["lat"]) == pytest.approx(52.55863)
    assert rows[0]["date"] == DATE
    remaining = read_pending(paths["pending"])
    assert len(remaining) == 1
    assert remaining[0]["address"] == INCOMPLETE_ADDRESS
    assert remaining[0]["reason"] == REASON_INCOMPLETE_ADDRESS
    assert "2 part(s)" in remaining[0]["detail"]  # the address is rejected before it reaches the API
    assert remaining[0]["attempts"] == 2
    assert remaining[0]["last_failed_at"] == "2017-06-25T09:00:00"
    assert remaining[0]["first_failed_at"] == "2017-06-24T10:00:00"


def test_retry_is_repeatable_and_adds_no_duplicate_rows(paths):
    error = GeocodeError(REASON_REQUEST_ERROR, "timeout")
    pending = PendingStore(paths["pending"])
    pending.add(new_pending_record(1000.0, 50.0, 2.0, VALID_ADDRESS, DATE, error, "2017-06-24T10:00:00")).save()
    write_csv(paths["csv"], [])

    geocoder = FakeGeocoder({MARKER_VALID: ok_response()})
    first = retry_pending(geocoder, paths["csv"], paths["pending"], "2017-06-25T09:00:00")
    second = retry_pending(geocoder, paths["csv"], paths["pending"], "2017-06-26T09:00:00")

    assert first == {"retried": 1, "recovered": 1, "skipped": 0, "failed": 0}
    assert second == {"retried": 0, "recovered": 0, "skipped": 0, "failed": 0}
    assert len(read_csv(paths["csv"])) == 1
    assert not os.path.exists(paths["pending"])


def test_retry_does_not_copy_an_apartment_that_was_already_written(paths):
    # e.g. the page was processed again and the apartment made it into the CSV meanwhile
    error = GeocodeError(REASON_REQUEST_ERROR, "timeout")
    pending = PendingStore(paths["pending"])
    pending.add(new_pending_record(1000.0, 50.0, 2.0, VALID_ADDRESS, DATE, error, "2017-06-24T10:00:00")).save()
    write_csv(paths["csv"], [{"price": 1000.0, "size": 50.0, "rooms": 2.0, "address": VALID_ADDRESS, "lat": 52.55863,
                              "lng": 13.38832, "zone": 33, "band": "U", "east": 1.0, "north": 2.0, "date": DATE}])

    summary = retry_pending(FakeGeocoder({MARKER_VALID: ok_response()}), paths["csv"], paths["pending"])
    assert summary == {"retried": 1, "recovered": 0, "skipped": 1, "failed": 0}
    assert len(read_csv(paths["csv"])) == 1
    assert not os.path.exists(paths["pending"])


def test_retry_keeps_pending_file_when_it_is_empty(paths):
    assert retry_pending(FakeGeocoder({}), paths["csv"], paths["pending"]) == \
           {"retried": 0, "recovered": 0, "skipped": 0, "failed": 0}
    assert not os.path.exists(paths["pending"])


def test_pending_store_ignores_corrupt_lines_and_duplicates(tmp_path):
    path = str(tmp_path / "pending.jsonl")
    error = GeocodeError(REASON_INCOMPLETE_ADDRESS, "missing")
    record = new_pending_record(1000.0, 50.0, 2.0, INCOMPLETE_ADDRESS, DATE, error, "2017-06-24T10:00:00")
    with open(path, "w", encoding = "utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
        handle.write("{broken json\n")
        handle.write(json.dumps(dict(record, detail = "second attempt")) + "\n")
    store = PendingStore(path).load()
    assert len(store) == 1
    assert store.records[0]["detail"] == "second attempt"


# -- end to end: spider -> pipeline -> retry ------------------------------


def page_html(entries):
    criteria = "".join(
        '<div class="grid-item result-list-entry__primary-criterion"><dd>{} EUR</dd><dd>{}</dd><dd>{}</dd></div>'
        .format(price, size, rooms) for price, size, rooms, _ in entries)
    addresses = "".join('<a class="result-list-entry__map-link">{}</a>'.format(address)
                        for _, _, _, address in entries)
    return ('<html><body><div class="grid-item result-list-entry__data-container">{}{}</div></body></html>'
            .format(criteria, addresses))


def page_response(entries, page = 1):
    url = "https://www.immobilienscout24.de/Suche/S-T/P-{}/Wohnung-Miete/Berlin/Berlin".format(page)
    return HtmlResponse(url = url, body = page_html(entries).encode("utf-8"), encoding = "utf-8",
                        request = Request(url))


def test_spider_result_routes_through_pipeline_and_can_be_retried(paths):
    entries = [(1000, "50,0 m²", 2, VALID_ADDRESS),
               (1200, "60,0 m²", 3, INCOMPLETE_ADDRESS),
               (900, "40,0 m²", 1, FAILING_ADDRESS),
               (1100, "55,0 m²", 2, OTHER_VALID_ADDRESS)]
    geocoder = FakeGeocoder({
        MARKER_VALID: ok_response(),
        MARKER_OTHER_VALID: ok_response(result({"lat": 52.5442976, "lng": 13.4382068}, ("premise",))),
        MARKER_FAILING: requests.ConnectionError("name resolution failed"),
    })
    bot = Immoscout24Bot(geocoder = geocoder)

    pipeline = CsvWriterPipeline(csv_path = paths["csv"], pending_path = paths["pending"])
    pipeline.open_spider(spider = bot)
    for item in bot.parse(page_response(entries)):
        pipeline.process_item(item, spider = bot)
    pipeline.close_spider(spider = bot)

    rows = read_csv(paths["csv"])
    assert sorted(row["address"] for row in rows) == sorted([VALID_ADDRESS, OTHER_VALID_ADDRESS])
    pending = read_pending(paths["pending"])
    assert sorted(record["address"] for record in pending) == sorted([INCOMPLETE_ADDRESS, FAILING_ADDRESS])
    assert {record["address"]: record["reason"] for record in pending} == {
        INCOMPLETE_ADDRESS: REASON_INCOMPLETE_ADDRESS,
        FAILING_ADDRESS: REASON_REQUEST_ERROR,
    }

    # the request error can be recovered by a later retry; the incomplete address still cannot
    retry_geocoder = FakeGeocoder({MARKER_FAILING: ok_response(result({"lat": 52.4997, "lng": 13.4031}))})
    summary = retry_pending(retry_geocoder, paths["csv"], paths["pending"], "2017-06-25T09:00:00")

    assert summary == {"retried": 2, "recovered": 1, "skipped": 0, "failed": 1}
    rows = read_csv(paths["csv"])
    assert sorted(row["address"] for row in rows) == sorted([VALID_ADDRESS, OTHER_VALID_ADDRESS, FAILING_ADDRESS])
    remaining = read_pending(paths["pending"])
    assert len(remaining) == 1 and remaining[0]["address"] == INCOMPLETE_ADDRESS
    assert remaining[0]["reason"] == REASON_INCOMPLETE_ADDRESS
    assert remaining[0]["attempts"] == 2


def test_pipeline_removes_a_queued_failure_once_it_geocodes_successfully_again(paths):
    error = GeocodeError(REASON_REQUEST_ERROR, "timeout")
    stale = new_pending_record(1000.0, 50.0, 2.0, VALID_ADDRESS, DATE, error, "2017-06-24T10:00:00")
    PendingStore(paths["pending"]).add(stale).save()

    good = {"price": 1000.0, "size": 50.0, "rooms": 2.0, "address": VALID_ADDRESS, "lat": 52.55863, "lng": 13.38832,
            "zone": 33, "band": "U", "east": 1.0, "north": 2.0, "date": DATE}
    pipeline = CsvWriterPipeline(csv_path = paths["csv"], pending_path = paths["pending"])
    pipeline.open_spider(spider = None)
    pipeline.process_item({"apartments": [good], "failures": [], "page": "1"}, spider = None)
    pipeline.close_spider(spider = None)

    assert len(read_csv(paths["csv"])) == 1
    assert not os.path.exists(paths["pending"])


def test_pending_store_keeps_records_with_unknown_reason(tmp_path):
    path = str(tmp_path / "pending.jsonl")
    error = GeocodeError(REASON_INCOMPLETE_ADDRESS, "missing")
    record = dict(new_pending_record(1000.0, 50.0, 2.0, INCOMPLETE_ADDRESS, DATE, error, "2017-06-24T10:00:00"),
                  reason = "some_newer_reason")
    with open(path, "w", encoding = "utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    assert len(PendingStore(path).load()) == 1


def test_collect_page_queues_out_of_range_coordinates_as_request_error():
    geocoder = FakeGeocoder({MARKER_VALID: ok_response(result({"lat": 999.0, "lng": 13.3}))})
    apartments, failures = collect_page(page_criteria(1), [VALID_ADDRESS], DATE, geocoder)
    assert apartments == []
    assert failures[0]["reason"] == REASON_REQUEST_ERROR
    assert "out of range" in failures[0]["detail"]
