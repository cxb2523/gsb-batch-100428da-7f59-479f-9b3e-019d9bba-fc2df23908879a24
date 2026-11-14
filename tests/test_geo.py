import httpx
import pytest

from gmaps_extractor import BoundaryError
from gmaps_extractor.geo import AreaBoundary, calculate_cell_size, generate_grid, get_area_boundary, is_in_boundary
from gmaps_extractor.geo import nominatim


def test_cell_size_by_area():
    assert calculate_cell_size(AreaBoundary("s", 30.05, 30.0, -97.0, -97.05)) == 1_000
    assert calculate_cell_size(AreaBoundary("m", 30.2, 30.0, -97.0, -97.2)) == 2_000
    assert calculate_cell_size(AreaBoundary("l", 30.5, 30.0, -97.0, -97.6)) == 5_000
    assert calculate_cell_size(AreaBoundary("xl", 33.0, 30.0, -95.0, -99.0)) == 50_000
    assert calculate_cell_size(AreaBoundary("xxl", 49.0, 25.0, -67.0, -125.0)) == 100_000


def test_grid_covers_box_and_starts_in_the_centre():
    box = AreaBoundary("a", north=30.30, south=30.24, east=-97.70, west=-97.78)
    cells = generate_grid(box, 1_000)
    assert len(cells) > 20
    assert all(is_in_boundary(c.center_lat, c.center_lng, box) for c in cells)
    centre_lat, centre_lng = box.center
    first, last = cells[0], cells[-1]
    d = lambda c: (c.center_lat - centre_lat) ** 2 + (c.center_lng - centre_lng) ** 2  # noqa: E731
    assert d(first) <= d(last)
    assert [c.cell_id for c in cells] == list(range(len(cells)))
    assert cells[0].radius_meters == 500


def test_tiny_area_gets_one_cell():
    cells = generate_grid(AreaBoundary("tiny", 30.0001, 30.0, -97.0, -97.0001), 1_000)
    assert len(cells) == 1


def test_grid_rejects_bad_size():
    with pytest.raises(ValueError):
        generate_grid(AreaBoundary("a", 1, 0, 1, 0), 0)


def _patch_nominatim(monkeypatch, handler):
    real = httpx.Client
    seen = {}

    def factory(**kwargs):
        seen.update(kwargs)
        kwargs.pop("proxy", None)
        return real(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(nominatim.httpx, "Client", factory)
    return seen


def test_area_boundary_lookup(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=[{"boundingbox": ["30.1", "30.5", "-97.9", "-97.5"]}])

    seen = _patch_nominatim(monkeypatch, handler)
    area, filt = get_area_boundary("Austin, Texas", buffer_km=11.1, proxy="http://user:pass@host:1")
    assert (area.south, area.north, area.west, area.east) == (30.1, 30.5, -97.9, -97.5)
    assert area.name == "Austin"
    assert filt.north == pytest.approx(30.6) and filt.west == pytest.approx(-98.0)
    assert seen["proxy"] == "http://user:pass@host:1"
    assert requests[0].url.params["q"] == "Austin, Texas"
    assert requests[0].headers["user-agent"].startswith("gmaps-extractor/")


@pytest.mark.parametrize(
    "response",
    [httpx.Response(200, json=[]), httpx.Response(500, text="x"), httpx.Response(200, text="not json"),
     httpx.Response(200, json=[{"no": "box"}])],
)
def test_area_boundary_errors(monkeypatch, response):
    _patch_nominatim(monkeypatch, lambda request: response)
    with pytest.raises(BoundaryError):
        get_area_boundary("Nowhere")


def test_empty_area_name():
    with pytest.raises(BoundaryError):
        get_area_boundary("  ")
