# -*- encoding: utf-8 -*-
"""Offline tests for the per-source collection outcome.

Each run publishes, in a new top-level ``status`` field of dolar.json, how
every source's collection went: ``ok``, ``connection_error`` (the request
could not be made or was rejected), ``structure_error`` (the fetched page no
longer has the expected shape) or ``invalid_value`` (a price was parsed but
is outside the plausible range, or the sell price is below the buy price).

These tests use forged responses and stubs, never the network. They assert
the two things the field exists for: a failure is labelled with its cause,
and it never takes the other sources down with it.
"""
import json
from decimal import Decimal

import pytest
import requests

import coti

ZERO = Decimal(0)

VALID_COMPRA = Decimal("7200")
VALID_VENTA = Decimal("7300")

# Just enough markup to parse: the point is the outcome, not the selector.
MAXI_PAGE = (
    '<div class="fixed-plugin"><table><tbody><tr>'
    "<td>Dólar</td><td>7200</td><td>7300</td>"
    "</tr></tbody></table></div>"
)

BCP_REF_PAGE = (
    '<table id="cotizacion-interbancaria"><tbody><tr>'
    "<td>USD</td><td>x</td><td>y</td><td>7.250,00</td>"
    "</tr></tbody></table>"
)


def bcp_fluct_page(compra, venta):
    return (
        '<table class="table table-striped table-bordered table-condensed">'
        "<tbody><tr><td>USD</td><td>x</td><td>y</td>"
        "<td>%s</td><td>%s</td></tr></tbody></table>" % (compra, venta)
    )


class RecordingCall(object):
    """Callable that records its calls and returns a fixed response."""

    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response


@pytest.fixture
def patch_get(monkeypatch, fake_response):
    def _patch(text="", payload=None, status_code=200):
        recorder = RecordingCall(
            fake_response(text=text, payload=payload, status_code=status_code)
        )
        monkeypatch.setattr(coti.requests, "get", recorder)
        return recorder

    return _patch


@pytest.fixture
def patch_scraper(monkeypatch, fake_response):
    def _patch(*pages_text, **kwargs):
        status_code = kwargs.pop("status_code", 200)
        pages_text = pages_text or ("",)
        responses = [
            fake_response(text=text, status_code=status_code) for text in pages_text
        ]

        class FakeScraper(object):
            def __init__(self):
                self.calls = []

            def get(self, url, **kwargs):
                index = min(len(self.calls), len(responses) - 1)
                self.calls.append(url)
                return responses[index]

        scraper = FakeScraper()
        monkeypatch.setattr(coti.cloudscraper, "create_scraper", lambda *a, **k: scraper)
        return scraper

    return _patch


# --- success ------------------------------------------------------------------


def test_a_normal_json_scrape_is_ok_and_keeps_its_values(patch_get):
    patch_get(payload={"items": [{"purchasePrice": "7200", "salePrice": "7300"}]})

    result = coti.chaco()

    assert result.status == coti.STATUS_OK
    assert tuple(result) == (VALID_COMPRA, VALID_VENTA)
    assert result.detail


def test_a_normal_html_scrape_is_ok_and_keeps_its_values(patch_get):
    patch_get(text=MAXI_PAGE)

    result = coti.maxi()

    assert result.status == coti.STATUS_OK
    assert tuple(result) == (VALID_COMPRA, VALID_VENTA)


def test_bcp_reports_ok_with_its_three_values(patch_scraper):
    patch_scraper(BCP_REF_PAGE, bcp_fluct_page("7.200,00", "7.300,00"))

    result = coti.bcp()

    assert result.status == coti.STATUS_OK
    assert tuple(result) == (
        Decimal("7200.00"),
        Decimal("7300.00"),
        Decimal("7250.00"),
    )


# --- connection or request failures -------------------------------------------


def test_a_connection_error_is_recorded(monkeypatch):
    def boom(*args, **kwargs):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(coti.requests, "get", boom)

    result = coti.chaco()

    assert result.status == coti.STATUS_CONNECTION_ERROR
    assert tuple(result) == (ZERO, ZERO)
    assert "ConnectionError" in result.detail


def test_an_http_error_status_is_a_request_failure(patch_get):
    """A 403 block page is a failed request, not a redesigned page."""
    patch_get(text="<html>blocked</html>", status_code=403)

    result = coti.chaco()

    assert result.status == coti.STATUS_CONNECTION_ERROR
    assert tuple(result) == (ZERO, ZERO)
    assert "403" in result.detail


def test_a_timeout_from_cloudscraper_is_a_request_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise requests.Timeout("timed out")

    monkeypatch.setattr(coti.cloudscraper, "create_scraper", boom)

    result = coti.bcp()

    assert result.status == coti.STATUS_CONNECTION_ERROR
    assert tuple(result) == (ZERO, ZERO, ZERO)


# --- page structure changed ---------------------------------------------------


def test_a_redesigned_page_is_a_structure_error(patch_get):
    patch_get(text="<html><body><h1>Nothing here</h1></body></html>")

    result = coti.maxi()

    assert result.status == coti.STATUS_STRUCTURE_ERROR
    assert tuple(result) == (ZERO, ZERO)


def test_an_html_page_where_json_was_expected_is_a_structure_error(patch_get):
    patch_get(text="<html><body>error</body></html>")

    result = coti.chaco()

    assert result.status == coti.STATUS_STRUCTURE_ERROR
    assert tuple(result) == (ZERO, ZERO)


def test_a_missing_row_is_a_structure_error(patch_get):
    """The table parses, but the dollar row it should contain is gone."""
    patch_get(text='<table class="table"><tbody><tr><td>Euro</td><td>1</td><td>2</td></tr></tbody></table>')

    result = coti.alberdi()

    assert result.status == coti.STATUS_STRUCTURE_ERROR
    assert tuple(result) == (ZERO, ZERO)


def test_an_unparseable_number_is_a_structure_error(patch_get):
    patch_get(payload={"items": [{"purchasePrice": "N/A", "salePrice": "7300"}]})

    result = coti.chaco()

    assert result.status == coti.STATUS_STRUCTURE_ERROR
    assert tuple(result) == (ZERO, ZERO)


def test_a_redesigned_bcp_page_is_a_structure_error(patch_scraper):
    patch_scraper("", "")

    result = coti.bcp()

    assert result.status == coti.STATUS_STRUCTURE_ERROR
    assert tuple(result) == (ZERO, ZERO, ZERO)


# --- values that cannot be trusted --------------------------------------------


@pytest.mark.parametrize(
    "compra, venta",
    [
        ("2999", "7300"),  # below the plausible floor
        ("7200", "20001"),  # above the plausible ceiling
        ("0", "0"),  # nothing parsed at all
        ("-7200", "7300"),  # negative
    ],
)
def test_an_out_of_range_price_is_an_invalid_value(patch_get, compra, venta):
    patch_get(payload={"items": [{"purchasePrice": compra, "salePrice": venta}]})

    result = coti.chaco()

    assert result.status == coti.STATUS_INVALID_VALUE
    assert tuple(result) == (ZERO, ZERO)
    assert "outside" in result.detail


def test_a_sell_price_below_the_buy_price_is_an_invalid_value(patch_get):
    patch_get(payload={"items": [{"purchasePrice": "7300", "salePrice": "7200"}]})

    result = coti.chaco()

    assert result.status == coti.STATUS_INVALID_VALUE
    assert tuple(result) == (ZERO, ZERO)
    assert "below" in result.detail


def test_every_source_except_bcp_checks_the_spread():
    assert "bcp" in coti.NO_SPREAD_GUARANTEE
    for key, function_name, _ in coti.SOURCES:
        if key == "bcp":
            continue
        assert key not in coti.NO_SPREAD_GUARANTEE


def test_bcp_hourly_averages_may_cross_without_failing(patch_scraper):
    """BCP's compra/venta are hourly averages, not a counter quote."""
    patch_scraper(BCP_REF_PAGE, bcp_fluct_page("5.774,54", "5.766,62"))

    result = coti.bcp()

    assert result.status == coti.STATUS_OK
    assert tuple(result)[0] > tuple(result)[1]
    assert tuple(result) == (
        Decimal("5774.54"),
        Decimal("5766.62"),
        Decimal("7250"),
    )


def test_bcp_is_still_range_checked(patch_scraper):
    """Skipping the spread check must not skip the sanity check."""
    # The reference rate is the invalid one here.
    patch_scraper(
        BCP_REF_PAGE.replace("7.250,00", "0"),
        bcp_fluct_page("5.774,54", "5.766,62"),
    )

    result = coti.bcp()

    assert result.status == coti.STATUS_INVALID_VALUE
    assert tuple(result) == (ZERO, ZERO, ZERO)


# --- the published document ---------------------------------------------------


def _stub_all(monkeypatch, **overrides):
    """Replace every scraper with a deterministic one.

    With no overrides every source looks healthy: two valid prices, and for
    BCP a third valid value for the daily reference rate.
    """
    for key, function_name, arity in coti.SOURCES:
        if arity == 2:
            value = (VALID_COMPRA, VALID_VENTA)
        else:
            value = (VALID_COMPRA, VALID_VENTA, Decimal("7250"))
        monkeypatch.setattr(coti, function_name, (lambda v: (lambda: v))(value))

    for function_name, scraper in overrides.items():
        monkeypatch.setattr(coti, function_name, scraper)


def _sources():
    return [key for key, _, _ in coti.SOURCES]


def test_status_is_a_new_top_level_field_beside_dolarpy_and_updated(monkeypatch):
    _stub_all(monkeypatch)

    payload = json.loads(coti.create_json())

    assert sorted(payload.keys()) == ["dolarpy", "status", "updated"]


def test_status_has_an_entry_for_every_published_source(monkeypatch):
    _stub_all(monkeypatch)

    payload = json.loads(coti.create_json())

    assert sorted(payload["status"].keys()) == sorted(payload["dolarpy"].keys())
    assert sorted(payload["status"].keys()) == sorted(_sources())
    for entry in payload["status"].values():
        assert sorted(entry.keys()) == ["detail", "status"]
        assert entry["status"] in coti.ALL_STATUSES
        assert isinstance(entry["detail"], str)


def test_a_healthy_run_reports_every_source_as_ok(monkeypatch):
    _stub_all(monkeypatch)

    payload = json.loads(coti.create_json())

    assert {entry["status"] for entry in payload["status"].values()} == {
        coti.STATUS_OK
    }
    # And the rate structure is exactly what it was before the new field.
    assert payload["dolarpy"]["cambioschaco"] == {"compra": 7200, "venta": 7300}
    assert payload["dolarpy"]["maxicambios"] == {"compra": 7200, "venta": 7300}
    assert payload["dolarpy"]["bcp"] == {
        "compra": 7200,
        "venta": 7300,
        "referencial_diario": 7250,
    }


def test_a_failing_source_stays_under_its_key_with_zero_prices(monkeypatch):
    _stub_all(monkeypatch, maxi=lambda: (ZERO, ZERO))

    payload = json.loads(coti.create_json())

    assert payload["dolarpy"]["maxicambios"] == {"compra": 0, "venta": 0}
    assert payload["status"]["maxicambios"]["status"] == coti.STATUS_INVALID_VALUE


def test_one_failing_source_does_not_block_the_others(monkeypatch):
    def boom():
        raise RuntimeError("unexpected")

    _stub_all(monkeypatch, maxi=boom)

    payload = json.loads(coti.create_json())

    assert payload["status"]["maxicambios"]["status"] == coti.STATUS_STRUCTURE_ERROR
    assert "RuntimeError" in payload["status"]["maxicambios"]["detail"]

    for key in _sources():
        if key == "maxicambios":
            continue
        assert payload["status"][key]["status"] == coti.STATUS_OK
        assert payload["dolarpy"][key]["compra"] == 7200
        assert payload["dolarpy"][key]["venta"] == 7300


def test_a_source_raising_a_request_error_is_a_connection_error(monkeypatch):
    def boom():
        raise requests.ConnectionError("offline")

    _stub_all(monkeypatch, chaco=boom)

    payload = json.loads(coti.create_json())

    assert payload["status"]["cambioschaco"]["status"] == coti.STATUS_CONNECTION_ERROR
    assert payload["dolarpy"]["cambioschaco"] == {"compra": 0, "venta": 0}
    assert payload["status"]["maxicambios"]["status"] == coti.STATUS_OK


def test_a_source_with_the_wrong_number_of_values_is_a_structure_error(monkeypatch):
    _stub_all(monkeypatch, bcp=lambda: (VALID_COMPRA, VALID_VENTA))

    payload = json.loads(coti.create_json())

    assert payload["status"]["bcp"]["status"] == coti.STATUS_STRUCTURE_ERROR
    assert payload["dolarpy"]["bcp"] == {
        "compra": 0,
        "venta": 0,
        "referencial_diario": 0,
    }
    assert payload["status"]["maxicambios"]["status"] == coti.STATUS_OK


def test_an_inverted_spread_from_a_stub_is_flagged(monkeypatch):
    _stub_all(
        monkeypatch,
        maxi=lambda: (Decimal("7300"), Decimal("7200")),
    )

    payload = json.loads(coti.create_json())

    assert payload["status"]["maxicambios"]["status"] == coti.STATUS_INVALID_VALUE
    assert payload["dolarpy"]["maxicambios"] == {"compra": 0, "venta": 0}


def test_bcp_stub_with_crossed_prices_is_not_flagged(monkeypatch):
    _stub_all(
        monkeypatch,
        bcp=lambda: (
            Decimal("5774.54"),
            Decimal("5766.62"),
            Decimal("7250"),
        ),
    )

    payload = json.loads(coti.create_json())

    assert payload["status"]["bcp"]["status"] == coti.STATUS_OK
    assert payload["dolarpy"]["bcp"]["compra"] == 5774.54


def test_write_output_publishes_the_status_field(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _stub_all(monkeypatch, maxi=lambda: (ZERO, ZERO))

    coti.write_output()
    payload = json.loads((tmp_path / "dolar.json").read_text())

    assert payload["status"]["maxicambios"]["status"] == coti.STATUS_INVALID_VALUE
    assert payload["status"]["cambioschaco"]["status"] == coti.STATUS_OK
    assert payload["updated"]
