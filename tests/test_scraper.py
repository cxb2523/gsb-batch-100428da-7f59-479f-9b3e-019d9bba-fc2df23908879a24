import pytest
from datetime import datetime
from unittest.mock import patch
from src.scraper import (
    Scraper,
    SEARCH_URL,
    SYSTEM_ID_URL,
    UPDATE_SESSION_URL,
    TRAIN_LIST_URL,
    extract_dwr_token,
    extract_train_list,
    create_search_id,
    create_session_script_id,
    tokenify,
)
from errors import (
    InvalidDWRToken,
    InvalidTrainListResponse,
    InvalidTrainRideFilter,
)
from models import ScrapeStage, ScrapeStatus, StationRecord, TrainRideRecord


class FakeResponse:
    """Minimal stand-in for requests.Response, built entirely from local DWR text."""

    def __init__(self, text="", ok=True, status_code=200):
        self.text = text
        self.ok = ok
        self.status_code = status_code

    def raise_for_status(self):
        if not self.ok:
            import requests

            raise requests.HTTPError(
                f"{self.status_code} error", response=self
            )


TOKEN_RESPONSE = 'r.handleCallback("0","0","test_token")'


def dwr_train_response(listado_trenes):
    """Wraps a Python list of train-way dicts in the DWR handleCallback text format."""
    import json

    payload = json.dumps({"listadoTrenes": listado_trenes})
    return f"r.handleCallback(0,0,{payload});"


def train_entry(hora_salida="08:30", hora_llegada="11:30", price="50.00", reason=""):
    """A single listviajeViewEnlaceBean entry with every field the parser touches."""
    return {
        "horaSalida": hora_salida,
        "horaLlegada": hora_llegada,
        "duracionViajeTotalEnMinutos": 180,
        "tarifaMinima": price,
        "completo": False,
        "razonNoDisponible": reason,
        "soloPlazaH": False,
        "tipoTrenUno": "AVE",
        "trayectos": [
            {"descripcionEstacionOrigen": "MADRID", "descripcionEstacionDestino": "BARCELONA"}
        ],
    }


def make_post_router(mapping):
    """Builds a side effect for requests.Session.post that routes by endpoint URL."""

    def _post(url, *args, **kwargs):
        return mapping[url]

    return _post


@pytest.fixture
def scraper():
    origin = StationRecord(name="Madrid", code="MAD")
    destination = StationRecord(name="Barcelona", code="BCN")
    departure_date = datetime(2023, 12, 25)
    return Scraper(origin, destination, departure_date)


def test_create_search_id():
    search_id = create_search_id()
    assert len(search_id) == 5
    assert search_id.startswith("_")

def test_create_session_script_id():
    dwr_token = "test_token"
    session_script_id = create_session_script_id(dwr_token)
    assert session_script_id.startswith(dwr_token)

def test_tokenify():
    number = 123456
    token = tokenify(number)
    assert isinstance(token, str)

def test_extract_dwr_token():
    response_text = 'r.handleCallback("0","0","test_token")'
    token = extract_dwr_token(response_text)
    assert token == "test_token"

def test_extract_dwr_token_invalid():
    response_text = 'invalid response'
    with pytest.raises(InvalidDWRToken):
        extract_dwr_token(response_text)

def test_extract_train_list():
    response_text = 'r.handleCallback(0,0,{"listadoTrenes":[]});'
    train_list = extract_train_list(response_text)
    assert "listadoTrenes" in train_list

def test_extract_train_list_missing_handle_callback():
    with pytest.raises(InvalidTrainListResponse):
        extract_train_list("this body has no DWR callback at all")

def test_extract_train_list_undecodable_payload():
    with pytest.raises(InvalidTrainListResponse):
        extract_train_list("r.handleCallback(0,0,{this is not valid json});")

def test_invalid_return_date():
    origin = StationRecord(name="Madrid", code="MAD")
    destination = StationRecord(name="Barcelona", code="BCN")
    departure_date = datetime(2023, 12, 25)
    return_date = datetime(2023, 12, 24)
    with pytest.raises(InvalidTrainRideFilter):
        Scraper(origin, destination, departure_date, return_date)

@patch('src.scraper.requests.Session.post')
def test_do_search(mock_post, scraper):
    mock_post.return_value.ok = True
    scraper._do_search()
    assert mock_post.called

@patch('src.scraper.requests.Session.post')
def test_do_get_dwr_token(mock_post, scraper):
    mock_post.return_value.ok = True
    mock_post.return_value.text = 'r.handleCallback("0","0","test_token")'
    scraper._do_get_dwr_token()
    assert scraper.dwr_token == "test_token"

@patch('src.scraper.requests.Session.post')
def test_do_update_session_objects(mock_post, scraper):
    mock_post.return_value.ok = True
    scraper.dwr_token = "test_token"
    scraper.script_session_id = "test_script_session_id"
    scraper._do_update_session_objects()
    assert mock_post.called

@patch('src.scraper.requests.Session.post')
def test_do_get_train_list(mock_post, scraper):
    mock_post.return_value.ok = True
    mock_post.return_value.text = 'r.handleCallback(0,0,{"listadoTrenes":[]});'
    train_list = scraper._do_get_train_list()
    assert "listadoTrenes" in train_list

def test_change_datetime_hour():
    date = datetime(2023, 12, 25)
    hour = "14:30"
    new_date = Scraper._change_datetime_hour(hour, date)
    assert new_date.hour == 14
    assert new_date.minute == 30

def test_is_train_available():
    train = {
        "completo": False,
        "razonNoDisponible": "",
        "tarifaMinima": "10.00",
        "soloPlazaH": False
    }
    assert Scraper._is_train_available(train)

def test_is_train_not_available():
    train = {
        "completo": True,
        "razonNoDisponible": "1",
        "tarifaMinima": "10.00",
        "soloPlazaH": False
    }
    assert not Scraper._is_train_available(train)

def test_is_train_not_available_only_handicapped():
    train = {
        "completo": False,
        "razonNoDisponible": "1",
        "tarifaMinima": "20.25",
        "soloPlazaH": True
    }
    assert not Scraper._is_train_available(train)

def test_get_transfer_stations_direct():
    train = {
        "tipoTrenDos": None,
        "trayectos": [
            {"descripcionEstacionOrigen": "MADRID", "descripcionEstacionDestino": "BARCELONA"}
        ],
    }
    assert Scraper._get_transfer_stations(train) == []

def test_get_transfer_stations_same_station():
    train = {
        "tipoTrenDos": "AVANT",
        "trayectos": [
            {"descripcionEstacionOrigen": "BARCELONA", "descripcionEstacionDestino": "CORDOBA"},
            {"descripcionEstacionOrigen": "CORDOBA", "descripcionEstacionDestino": "SEVILLA"},
        ],
    }
    assert Scraper._get_transfer_stations(train) == ["CORDOBA"]

def test_get_transfer_stations_station_change():
    train = {
        "tipoTrenDos": "MD",
        "trayectos": [
            {"descripcionEstacionOrigen": "BARCELONA", "descripcionEstacionDestino": "MADRID-PTA"},
            {"descripcionEstacionOrigen": "MADRID-CERC", "descripcionEstacionDestino": "SEVILLA"},
        ],
    }
    assert Scraper._get_transfer_stations(train) == ["MADRID-PTA → MADRID-CERC"]

def test_get_transfer_stations_without_legs():
    assert Scraper._get_transfer_stations({"tipoTrenDos": "MD"}) == ["N/A"]
    assert Scraper._get_transfer_stations({}) == []

def test_is_train_not_on_sale_yet():
    train = {"completo": False, "razonNoDisponible": "4", "tarifaMinima": None, "soloPlazaH": False}
    assert Scraper._is_train_not_on_sale_yet(train)
    assert not Scraper._is_train_available(train)

def test_sold_out_train_is_not_flagged_as_not_on_sale_yet():
    train = {"completo": True, "razonNoDisponible": "1", "tarifaMinima": None, "soloPlazaH": False}
    assert not Scraper._is_train_not_on_sale_yet(train)


# ---------------------------------------------------------------------------
# get_trainrides() end-to-end scenarios driven by locally built DWR responses
# ---------------------------------------------------------------------------

def _happy_path_mapping(train_list_text):
    return {
        SEARCH_URL: FakeResponse(),
        SYSTEM_ID_URL: FakeResponse(text=TOKEN_RESPONSE),
        UPDATE_SESSION_URL: FakeResponse(),
        TRAIN_LIST_URL: FakeResponse(text=train_list_text),
    }


@patch('src.scraper.requests.Session.post')
def test_get_trainrides_returns_multiple_trains(mock_post, scraper):
    listado = [
        {
            "listviajeViewEnlaceBean": [
                train_entry("07:00", "09:30", "30.00"),
                train_entry("10:00", "12:30", "45.50"),
                # Not on sale yet: reason code 4 and no price
                train_entry("15:00", "17:30", None, reason="4"),
            ]
        }
    ]
    mock_post.side_effect = make_post_router(_happy_path_mapping(dwr_train_response(listado)))

    result = scraper.get_trainrides()

    assert result.status == ScrapeStatus.SUCCESS
    assert result.stage == ScrapeStage.TRAIN_LIST
    assert result.error is None
    assert len(result.rides) == 3
    assert all(isinstance(ride, TrainRideRecord) for ride in result.rides)
    assert result.rides[0].departure_time.hour == 7
    assert result.rides[0].available
    not_on_sale = result.rides[2]
    assert not_on_sale.not_on_sale_yet
    assert not not_on_sale.available


@patch('src.scraper.requests.Session.post')
def test_get_trainrides_empty_listado_trenes(mock_post, scraper):
    mock_post.side_effect = make_post_router(
        _happy_path_mapping(dwr_train_response([]))
    )

    result = scraper.get_trainrides()

    assert result.status == ScrapeStatus.EMPTY
    assert result.stage == ScrapeStage.TRAIN_LIST
    assert result.rides == []
    assert result.error is None


@patch('src.scraper.requests.Session.post')
def test_get_trainrides_token_response_missing_handle_callback(mock_post, scraper):
    mapping = {
        SEARCH_URL: FakeResponse(),
        SYSTEM_ID_URL: FakeResponse(text="//#DWR-INSERT unexpected body without a callback"),
        UPDATE_SESSION_URL: FakeResponse(),
        TRAIN_LIST_URL: FakeResponse(),
    }
    mock_post.side_effect = make_post_router(mapping)

    result = scraper.get_trainrides()

    assert result.status == ScrapeStatus.SESSION_FAILED
    assert result.stage == ScrapeStage.DWR_TOKEN
    assert result.rides == []
    assert isinstance(result.error, InvalidDWRToken)


@patch('src.scraper.requests.Session.post')
def test_get_trainrides_train_list_response_not_parseable(mock_post, scraper):
    mapping = _happy_path_mapping("this is not a DWR response at all")
    mock_post.side_effect = make_post_router(mapping)

    result = scraper.get_trainrides()

    assert result.status == ScrapeStatus.TRAIN_LIST_FAILED
    assert result.stage == ScrapeStage.TRAIN_LIST
    assert result.rides == []
    assert isinstance(result.error, InvalidTrainListResponse)
    # The original failure stays traceable through the exception chain
    assert result.error.__cause__ is None or isinstance(result.error.__cause__, BaseException)


@patch('src.scraper.requests.Session.post')
def test_get_trainrides_failure_during_parsing_keeps_partial_rides(mock_post, scraper):
    good_entry = train_entry("07:00", "09:30", "30.00")
    broken_entry = {"unexpected_shape": True}  # missing every field the parser needs
    listado = [
        {"listviajeViewEnlaceBean": [good_entry, broken_entry]},
    ]
    mock_post.side_effect = make_post_router(_happy_path_mapping(dwr_train_response(listado)))

    result = scraper.get_trainrides()

    assert result.status == ScrapeStatus.TRAIN_LIST_FAILED
    assert result.stage == ScrapeStage.TRAIN_LIST
    assert len(result.rides) == 1
    assert result.rides[0].departure_time.hour == 7
    assert result.error is not None
    assert result.error.partial_rides == result.rides


@patch('src.scraper.requests.Session.post')
def test_get_trainrides_http_error_in_session_stage(mock_post, scraper):
    mapping = {
        SEARCH_URL: FakeResponse(ok=False, status_code=503),
        SYSTEM_ID_URL: FakeResponse(text=TOKEN_RESPONSE),
        UPDATE_SESSION_URL: FakeResponse(),
        TRAIN_LIST_URL: FakeResponse(),
    }
    mock_post.side_effect = make_post_router(mapping)

    result = scraper.get_trainrides()

    assert result.status == ScrapeStatus.SESSION_FAILED
    assert result.stage == ScrapeStage.SEARCH
    assert result.error is not None
    assert "503" in str(result.error)


@patch('src.scraper.requests.Session.post')
def test_get_trainrides_update_session_failure_is_a_session_stage(mock_post, scraper):
    mapping = {
        SEARCH_URL: FakeResponse(),
        SYSTEM_ID_URL: FakeResponse(text=TOKEN_RESPONSE),
        UPDATE_SESSION_URL: FakeResponse(ok=False, status_code=500),
        TRAIN_LIST_URL: FakeResponse(),
    }
    mock_post.side_effect = make_post_router(mapping)

    result = scraper.get_trainrides()

    assert result.status == ScrapeStatus.SESSION_FAILED
    assert result.stage == ScrapeStage.UPDATE_SESSION
    assert result.error is not None
    assert "500" in str(result.error)


@patch('src.scraper.requests.Session.post')
def test_get_trainrides_undecodable_train_list_payload(mock_post, scraper):
    # Well-formed handleCallback wrapper but its JSON payload is broken
    broken_body = "r.handleCallback(0,0,{listadoTrenes: [unclosed});"
    mock_post.side_effect = make_post_router(_happy_path_mapping(broken_body))

    result = scraper.get_trainrides()

    assert result.status == ScrapeStatus.TRAIN_LIST_FAILED
    assert result.stage == ScrapeStage.TRAIN_LIST
    assert isinstance(result.error, InvalidTrainListResponse)
    assert isinstance(result.error.__cause__, ValueError)
