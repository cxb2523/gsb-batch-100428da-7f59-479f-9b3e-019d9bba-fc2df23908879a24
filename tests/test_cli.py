from datetime import datetime
from unittest.mock import patch

import pytest

from cli import main, remove_duplicates
from models import StationRecord, TrainRideRecord
from validators import DateValidationResult, StationValidationResult


@pytest.fixture(autouse=True)
def mock_validators():
    """Avoid loading the real stations file, as StationsStorage caches it between tests"""
    station = StationValidationResult(True, StationRecord(name="X", code="1"))
    date = DateValidationResult(True, datetime(2026, 12, 20))
    with patch("cli.validate_station", return_value=station), \
            patch("cli.validate_date", return_value=date):
        yield


def make_ride(hour, transfer_stations, available=True, price=50.0):
    return TrainRideRecord(
        origin="BARCELONA",
        destination="CORDOBA",
        departure_time=datetime(2026, 12, 20, hour),
        arrival_time=datetime(2026, 12, 20, hour + 6),
        duration=360,
        price=price,
        available=available,
        train_type="AVE",
        transfer_stations=transfer_stations,
    )


RIDES = [make_ride(7, []), make_ride(9, ["MADRID"])]


@patch("cli.Scraper")
def test_main_shows_all_trains(mock_scraper, capsys):
    mock_scraper.return_value.get_trainrides.return_value = RIDES
    main("Madrid", "Barcelona", "20/12/2026")
    out = capsys.readouterr().out
    assert "07:00" in out and "09:00" in out


@patch("cli.Scraper")
def test_main_direct_only(mock_scraper, capsys):
    mock_scraper.return_value.get_trainrides.return_value = RIDES
    main("Madrid", "Barcelona", "20/12/2026", direct_only=True)
    out = capsys.readouterr().out
    assert "07:00" in out and "09:00" not in out


@patch("cli.Scraper")
def test_main_direct_only_without_direct_trains(mock_scraper, capsys):
    mock_scraper.return_value.get_trainrides.return_value = RIDES[1:]
    main("Madrid", "Barcelona", "20/12/2026", direct_only=True)
    assert "No trains found" in capsys.readouterr().out


@patch("cli.Scraper")
def test_main_all_sold_out(mock_scraper, capsys):
    mock_scraper.return_value.get_trainrides.return_value = [
        make_ride(7, [], available=False, price=float("nan")),
        make_ride(9, [], available=False, price=float("nan")),
    ]
    main("Madrid", "Barcelona", "20/12/2026")
    out = capsys.readouterr().out
    assert "All 2 trains for the given filter are sold out" in out
    assert "Train type" not in out


@patch("cli.Scraper")
def test_main_direct_only_sold_out(mock_scraper, capsys):
    mock_scraper.return_value.get_trainrides.return_value = [
        make_ride(7, [], available=False, price=float("nan")),
        make_ride(9, ["MADRID"]),
    ]
    main("Madrid", "Barcelona", "20/12/2026", direct_only=True)
    assert "The only train for the given filter is sold out" in capsys.readouterr().out


@patch("cli.format_transfers", return_value="")
@patch("cli.Scraper")
def test_main_highlights_cheap_trains_despite_sold_out(mock_scraper, _, capsys):
    mock_scraper.return_value.get_trainrides.return_value = [
        make_ride(7, [], price=30.0),
        make_ride(9, [], price=90.0),
        make_ride(11, [], available=False, price=float("nan")),
    ]
    with patch("cli.Table.add_row") as add_row:
        main("Madrid", "Barcelona", "20/12/2026")
    prices = [call.args[-1] for call in add_row.call_args_list]
    assert prices == ["[bold green]30.00 €[/]", "90.00 €"]


@patch("cli.Scraper")
def test_main_shows_trains_not_on_sale_yet(mock_scraper, capsys):
    not_on_sale = make_ride(8, [], available=False, price=float("nan"))
    not_on_sale.not_on_sale_yet = True
    mock_scraper.return_value.get_trainrides.return_value = [
        not_on_sale,
        make_ride(10, [], available=False, price=float("nan")),  # sold out
    ]
    main("Madrid", "Barcelona", "20/12/2026")
    out = capsys.readouterr().out
    assert "08:00" in out and "Not on sale" in out
    assert "10:00" not in out


def test_remove_duplicates_keeps_the_most_useful_train():
    sold_out = make_ride(8, [], available=False, price=float("nan"))
    not_on_sale = make_ride(8, [], available=False, price=float("nan"))
    not_on_sale.not_on_sale_yet = True
    expensive = make_ride(8, [], price=90.0)
    cheap = make_ride(8, [], price=30.0)
    other = make_ride(9, [])

    assert remove_duplicates([sold_out, not_on_sale]) == [not_on_sale]
    assert remove_duplicates([not_on_sale, expensive, cheap, other]) == [cheap, other]


def test_remove_duplicates_keeps_different_trains():
    direct = make_ride(8, [])
    with_transfer = make_ride(8, ["MADRID"])
    assert remove_duplicates([direct, with_transfer]) == [direct, with_transfer]


@patch("cli.Scraper")
def test_main_shows_duplicated_trains_once(mock_scraper, capsys):
    mock_scraper.return_value.get_trainrides.return_value = [make_ride(8, []), make_ride(8, [])]
    main("Madrid", "Barcelona", "20/12/2026")
    assert capsys.readouterr().out.count("08:00") == 1
