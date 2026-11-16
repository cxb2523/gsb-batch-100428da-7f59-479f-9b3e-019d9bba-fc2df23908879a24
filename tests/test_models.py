import pytest
from datetime import datetime, time
from unittest.mock import patch
from errors import InvalidDWRToken, InvalidTrainRideFilter
from models import (
    ScrapeStage,
    ScrapeStatus,
    TrainRideRecord,
    TrainRideFilter,
    TrainRideResult,
    StationRecord,
)


@pytest.fixture
def sample_rides():
    return [
        TrainRideRecord(
            origin="Madrid",
            destination="Barcelona",
            departure_time=datetime(2025, 1, 30, 8, 30),
            arrival_time=datetime(2025, 1, 30, 11, 30),
            duration=180,
            price=50.0,
            available=True,
            train_type="AVE"
        ),
        TrainRideRecord(
            origin="Madrid",
            destination="Barcelona",
            departure_time=datetime(2025, 1, 30, 9, 30),
            arrival_time=datetime(2025, 1, 30, 12, 30),
            duration=180,
            price=55.0,
            available=False,
            train_type="ALVIA"
        ),
        TrainRideRecord(
            origin="Madrid",
            destination="Barcelona",
            departure_time=datetime(2025, 1, 30, 10, 30),
            arrival_time=datetime(2025, 1, 30, 13, 30),
            duration=180,
            price=60.0,
            available=True,
            train_type="ALVIA"
        )
    ]


def test_filter_rides_origin_and_destination(sample_rides):
    filter = TrainRideFilter(
        origin="Madrid",
        destination="Barcelona",
        departure_date=datetime(2025, 1, 30),
        max_duration_minutes=None,
        max_price=None
    )

    result = filter.filter_rides(sample_rides)

    assert len(result) == 2  # Only 2 available rides


def test_filter_rides_by_min_departure_hour(sample_rides):
    filter = TrainRideFilter(
        origin="Madrid",
        destination="Barcelona",
        departure_date=datetime(2025, 1, 30, 9, 0),
        max_duration_minutes=None,
        max_price=None
    )

    result = filter.filter_rides(sample_rides)

    assert len(result) == 1  # Only the third ride should pass


def test_filter_rides_by_max_duration(sample_rides):
    filter = TrainRideFilter(
        origin="Madrid",
        destination="Barcelona",
        departure_date=datetime(2025, 1, 30),
        max_duration_minutes=180,
        max_price=None
    )

    result = filter.filter_rides(sample_rides)

    assert len(result) == 2  # All rides are within the duration limit


def test_filter_rides_by_max_price(sample_rides):
    filter = TrainRideFilter(
        origin="Madrid",
        destination="Barcelona",
        departure_date=datetime(2025, 1, 30),
        max_duration_minutes=None,
        max_price=55.0
    )

    result = filter.filter_rides(sample_rides)

    assert len(result) == 1  # Only the first ride should pass due to price


def test_filter_rides_no_results(sample_rides):
    filter = TrainRideFilter(
        origin="Madrid",
        destination="Barcelona",
        departure_date=datetime(2025, 1, 31),  # Different date
        max_duration_minutes=1,
        max_price=None
    )

    with pytest.raises(InvalidTrainRideFilter):
        filter.filter_rides(sample_rides)


def test_train_ride_str(sample_rides):
    ride = sample_rides[0]
    expected_str = "🚆 Tren AVE: 🕒 08:30 - 11:30 🕙 - 50.00 €\n"
    assert str(ride) == expected_str

def test_train_ride_is_direct_by_default(sample_rides):
    assert sample_rides[0].is_direct
    assert sample_rides[0].transfers == 0

def test_train_ride_with_transfer(sample_rides):
    ride = sample_rides[0].model_copy(update={"transfer_stations": ["ZARAGOZA"]})
    assert not ride.is_direct
    assert ride.transfers == 1


def test_train_ride_result_success_properties(sample_rides):
    result = TrainRideResult(
        status=ScrapeStatus.SUCCESS, stage=ScrapeStage.TRAIN_LIST, rides=sample_rides
    )
    assert result.success
    assert not result.empty
    assert not result.session_failed
    assert not result.train_list_failed


def test_train_ride_result_empty_properties():
    result = TrainRideResult(status=ScrapeStatus.EMPTY, stage=ScrapeStage.TRAIN_LIST)
    assert result.empty
    assert result.rides == []
    assert result.stage == ScrapeStage.TRAIN_LIST
    assert result.error is None


def test_train_ride_result_session_failure_keeps_stage_and_error():
    error = InvalidDWRToken()
    result = TrainRideResult(
        status=ScrapeStatus.SESSION_FAILED, stage=ScrapeStage.DWR_TOKEN, error=error
    )
    assert result.session_failed
    assert result.stage == ScrapeStage.DWR_TOKEN
    assert result.error is error
    assert result.rides == []


def test_train_ride_result_train_list_failure_keeps_partial_rides(sample_rides):
    error = ValueError("payload cut off")
    result = TrainRideResult(
        status=ScrapeStatus.TRAIN_LIST_FAILED,
        stage=ScrapeStage.TRAIN_LIST,
        error=error,
        rides=sample_rides[:1],
    )
    assert result.train_list_failed
    assert len(result.rides) == 1
    assert result.rides[0] == sample_rides[0]
    assert result.error is error
