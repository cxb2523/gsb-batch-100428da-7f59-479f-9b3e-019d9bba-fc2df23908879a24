import threading

import httpx
import pytest
from conftest import fixture_text

from gmaps_extractor import FIELDS, GMapsClient, RateLimitError, RequestError, collect
from gmaps_extractor.collector import to_row
from gmaps_extractor.geo import AreaBoundary

AREA = AreaBoundary("Austin", north=30.30, south=30.24, east=-97.70, west=-97.78)
FILTER = AreaBoundary("Austin", north=30.35, south=30.19, east=-97.65, west=-97.83)
BOUNDARY = (AREA, FILTER)


def place(n, lat=30.27, lng=-97.74, **extra):
    data = {
        "name": f"Example Place {n}",
        "address": f"{n} Example St",
        "phone": None,
        "website": None,
        "rating": 4.0,
        "reviews_count": n,
        "category": "Coffee shop",
        "categories": ["Coffee shop"],
        "latitude": lat,
        "longitude": lng,
        "place_id": f"ChIJ{n:04d}",
        "hex_id": f"0x{n}:0x{n}",
        "ftid": None,
    }
    data.update(extra)
    return data


class FakeClient:
    def __init__(self, pages, hours=None, fail_search=False, fail_hours=(),
                 search_error=None, hours_error=None):
        self.pages = pages  # list returned for every cell, page 0
        self.hours = hours or {"monday": "9 AM-5 PM"}
        # fail_search: True, or a callable(lat, lng) -> falsy / True / the exception to raise
        self.fail_search = fail_search
        self.fail_hours = set(fail_hours)
        self.search_error = search_error or RequestError("blocked")
        self.hours_error = hours_error or RequestError("blocked")
        self.search_calls = []
        self.hours_calls = []
        self.lock = threading.Lock()

    def search_page(self, query, lat, lng, offset=0, **kwargs):
        with self.lock:
            self.search_calls.append((query, lat, lng, offset))
        fail = self.fail_search(lat, lng) if callable(self.fail_search) else self.fail_search
        if fail:
            raise fail if isinstance(fail, Exception) else self.search_error
        return list(self.pages) if offset == 0 else []

    def place_hours(self, hex_id, name=None, lat=0.0, lng=0.0, ftid=None):
        with self.lock:
            self.hours_calls.append(hex_id)
        if hex_id in self.fail_hours:
            raise self.hours_error
        return dict(self.hours)


def test_rows_have_exactly_the_public_fields():
    fake = FakeClient([place(1), place(2)])
    rows = collect("Austin", "coffee shop", client=fake, boundary=BOUNDARY, cell_size_meters=10_000)
    assert len(rows) == 2
    for row in rows:
        assert tuple(row) == FIELDS
        assert row["hours"] == {"monday": "9 AM-5 PM"}
        assert row["google_maps_url"].endswith(f"query_place_id={row['place_id']}")
        assert "hex_id" not in row and "ftid" not in row


def test_deduplicates_across_cells_and_counts_stats():
    fake = FakeClient([place(1), place(2)])
    rows = collect("Austin", "coffee shop", client=fake, boundary=BOUNDARY, cell_size_meters=2_000, workers=3)
    assert len(rows) == 2
    assert rows.stats["cells_total"] == len(fake.search_calls) > 1
    assert rows.stats["places_found"] == 2
    assert rows.stats["hours_requested"] == 2 and rows.stats["hours_found"] == 2
    assert sorted(fake.hours_calls) == ["0x1:0x1", "0x2:0x2"]


def test_max_results_stops_early_and_limits_hours():
    fake = FakeClient([place(n) for n in range(1, 6)])
    rows = collect("Austin", "coffee", client=fake, boundary=BOUNDARY, cell_size_meters=1_000, max_results=3,
                   workers=2)
    assert len(rows) == 3
    assert len(fake.search_calls) == 2  # one batch of two cells was enough
    assert len(fake.hours_calls) == 3


def test_places_outside_the_area_are_dropped():
    fake = FakeClient([place(1), place(2, lat=40.7, lng=-74.0), place(3, lat=None, lng=None)])
    rows = collect("Austin", "coffee", client=fake, boundary=BOUNDARY, cell_size_meters=10_000)
    assert [r["place_id"] for r in rows] == ["ChIJ0001", "ChIJ0003"]
    assert rows.stats["places_outside_area"] == 1


def test_no_hours_mode_makes_no_place_requests():
    fake = FakeClient([place(1)])
    rows = collect("Austin", "coffee", client=fake, boundary=BOUNDARY, cell_size_meters=10_000, hours=False)
    assert rows[0]["hours"] is None
    assert fake.hours_calls == []


def test_failed_hours_leave_none_and_are_counted():
    fake = FakeClient([place(1), place(2)], fail_hours={"0x2:0x2"})
    rows = collect("Austin", "coffee", client=fake, boundary=BOUNDARY, cell_size_meters=10_000)
    by_id = {r["place_id"]: r for r in rows}
    assert by_id["ChIJ0001"]["hours"] and by_id["ChIJ0002"]["hours"] is None
    assert rows.stats["hours_failed"] == 1


def test_all_searches_failing_raises():
    fake = FakeClient([], fail_search=True)
    with pytest.raises(RequestError):
        collect("Austin", "coffee", client=fake, boundary=BOUNDARY, cell_size_meters=10_000)


def fail_first_calls(errors, default=False):
    """Return a fail_search predicate: raise the queued errors in call order, then ``default``."""
    todo = list(errors)
    lock = threading.Lock()

    def predicate(lat, lng):
        with lock:
            return todo.pop(0) if todo else default

    return predicate


def test_some_cells_rate_limited_returns_rows_and_counts():
    fake = FakeClient(
        [place(1)],
        fail_search=fail_first_calls([RateLimitError("HTTP 429"), RateLimitError("HTTP 429")]),
    )
    rows = collect("Austin", "coffee", client=fake, boundary=BOUNDARY, cell_size_meters=2_000, workers=2)
    assert [r["place_id"] for r in rows] == ["ChIJ0001"]
    stats = rows.stats
    assert stats["cells_rate_limited"] == 2
    assert stats["cells_failed"] == 0
    assert stats["cells_not_searched"] == 0
    assert stats["cells_searched"] == stats["cells_total"] > 2
    assert stats["search_requests_failed"] == 2


def test_some_cells_failing_mixed_counts_each_cause():
    fake = FakeClient(
        [place(1)],
        fail_search=fail_first_calls([RateLimitError("HTTP 429"), RequestError("empty answer")]),
    )
    rows = collect("Austin", "coffee", client=fake, boundary=BOUNDARY, cell_size_meters=2_000, workers=2)
    assert len(rows) == 1
    assert rows.stats["cells_rate_limited"] == 1
    assert rows.stats["cells_failed"] == 1


def test_all_cells_rate_limited_raises_rate_limit_error():
    fake = FakeClient([], fail_search=True, search_error=RateLimitError("HTTP 429"))
    with pytest.raises(RateLimitError):
        collect("Austin", "coffee", client=fake, boundary=BOUNDARY, cell_size_meters=2_000, workers=3)


def test_all_cells_failing_mixed_raises_plain_request_error():
    fake = FakeClient(
        [],
        fail_search=fail_first_calls([RateLimitError("HTTP 429")], default=RequestError("empty answer")),
    )
    with pytest.raises(RequestError) as excinfo:
        collect("Austin", "coffee", client=fake, boundary=BOUNDARY, cell_size_meters=2_000, workers=3)
    assert not isinstance(excinfo.value, RateLimitError)


def test_rate_limited_hours_leave_none_and_are_counted():
    fake = FakeClient([place(1), place(2)], fail_hours={"0x2:0x2"}, hours_error=RateLimitError("HTTP 429"))
    rows = collect("Austin", "coffee", client=fake, boundary=BOUNDARY, cell_size_meters=10_000)
    by_id = {r["place_id"]: r for r in rows}
    assert by_id["ChIJ0001"]["hours"] == {"monday": "9 AM-5 PM"}
    assert by_id["ChIJ0002"]["hours"] is None
    assert rows.stats["hours_failed"] == 1
    assert rows.stats["hours_rate_limited"] == 1
    assert rows.stats["hours_found"] == 1


def test_max_results_leaves_cells_not_searched():
    fake = FakeClient([place(n) for n in range(1, 6)])
    rows = collect("Austin", "coffee", client=fake, boundary=BOUNDARY, cell_size_meters=1_000, max_results=3,
                   workers=2)
    stats = rows.stats
    assert stats["cells_not_searched"] == stats["cells_total"] - stats["cells_searched"] > 0


def test_empty_area_is_not_an_error():
    rows = collect("Austin", "coffee", client=FakeClient([]), boundary=BOUNDARY, cell_size_meters=10_000)
    assert rows == [] and rows.stats["places_found"] == 0


@pytest.mark.parametrize("kwargs", [{"max_results": 0}, {"query": " "}])
def test_bad_arguments(kwargs):
    args = {"query": "coffee", **kwargs}
    with pytest.raises(Exception):
        collect("Austin", client=FakeClient([]), boundary=BOUNDARY, **args)


def test_end_to_end_with_recorded_answers():
    search, place_answer = fixture_text("search_page.txt"), fixture_text("place_hours.txt")

    def handler(request):
        if request.url.path == "/maps":
            return httpx.Response(200, text="<html>maps</html>")
        return httpx.Response(200, text=search if request.url.path == "/search" else place_answer)

    with GMapsClient(transport=httpx.MockTransport(handler), min_interval=0) as client:
        rows = collect("Austin", "coffee shop", client=client, boundary=BOUNDARY, cell_size_meters=10_000)
    assert [r["name"] for r in rows] == ["Example Coffee Roasters", "Sample Espresso Bar", "Café Ünïcode & Co!"]
    assert all(r["hours"]["sunday"] == "Closed" for r in rows)
    text = repr(list(rows))
    assert "UNPARSED" not in text and "Synthetic" not in text


def test_to_row_copies_categories():
    src = place(1)
    row = to_row(src, None)
    row["categories"].append("x")
    assert src["categories"] == ["Coffee shop"]
