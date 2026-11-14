import httpx
import pytest
from conftest import Recorder, fixture_text, make_client

from gmaps_extractor import ConfigurationError, RateLimitError, RequestError, resolve_proxy
from gmaps_extractor.client import GMapsClient, generate_consent_cookie

SEARCH = fixture_text("search_page.txt")
PLACE = fixture_text("place_hours.txt")


def test_search_page_returns_places():
    rec = Recorder([(200, SEARCH)])
    with make_client(rec) as client:
        places = client.search_page("coffee shop", 30.2672, -97.7431)
    assert len(places) == 3
    (request,) = rec.api_requests()
    assert request.url.path == "/search"
    assert request.headers["referer"] == "https://www.google.com/"
    assert "SOCS=" in request.headers.get("cookie", "")


def test_place_hours_request_and_parse():
    rec = Recorder([(200, PLACE)])
    with make_client(rec) as client:
        hours = client.place_hours("0x1:0x2", "Café #1", 30.1, -97.5, "/g/11abc")
    assert hours["friday"] == "11 AM-2 PM, 5-10 PM"
    session, place = rec.requests
    assert session.url.path == "/maps"
    assert place.url.path == "/maps/preview/place"
    assert place.headers["origin"] == "https://www.google.com"
    place.headers["referer"].encode("ascii")


def test_place_hours_requires_hex_id():
    with make_client(Recorder()) as client, pytest.raises(ValueError):
        client.place_hours("")


def test_one_retry_then_success():
    rec = Recorder([(429, "slow down"), (200, SEARCH)])
    with make_client(rec) as client:
        assert len(client.search_page("coffee", 1, 2)) == 3
    assert len(rec.api_requests()) == 2


@pytest.mark.parametrize(
    "answer,error",
    [((429, "x"), RateLimitError), ((503, "x"), RequestError), ((200, ")]}'\n[]"), RequestError)],
)
def test_gives_up_after_exactly_one_retry(answer, error):
    rec = Recorder(default=answer)
    with make_client(rec) as client, pytest.raises(error):
        client.search_page("coffee", 1, 2)
    assert len(rec.api_requests()) == 2


def test_consent_redirect_is_a_failure():
    def handler(request):
        if request.url.host == "consent.google.com":
            return httpx.Response(200, text="<html>" + "consent " * 50 + "</html>", request=request)
        return httpx.Response(302, headers={"location": "https://consent.google.com/ml?continue=x"}, request=request)

    client = GMapsClient(transport=httpx.MockTransport(handler), min_interval=0)
    with client, pytest.raises(RequestError, match="consent page"):
        client.search_page("coffee", 1, 2)


def test_network_error_is_retried_once():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("refused", request=request)

    client = GMapsClient(transport=httpx.MockTransport(handler), min_interval=0)
    with client, pytest.raises(RequestError, match="network error"):
        client.search_page("coffee", 1, 2)
    assert len(calls) == 2


def test_resolve_proxy_argument_wins(monkeypatch):
    monkeypatch.setenv("GMAPS_PROXY_URL", "http://env:env@envhost:1")
    assert resolve_proxy("http://user:pass@host:8080") == "http://user:pass@host:8080"


def test_resolve_proxy_from_env(monkeypatch):
    assert resolve_proxy() is None
    monkeypatch.setenv("GMAPS_PROXY_HOST", "host:8080")
    assert resolve_proxy() == "http://host:8080"
    monkeypatch.setenv("GMAPS_PROXY_USER", "user")
    monkeypatch.setenv("GMAPS_PROXY_PASS", "p@ss:word")
    assert resolve_proxy() == "http://user:p%40ss%3Aword@host:8080"
    monkeypatch.setenv("GMAPS_PROXY_URL", "socks5://other:1080")
    assert resolve_proxy() == "socks5://other:1080"


def test_resolve_proxy_rejects_half_config(monkeypatch):
    monkeypatch.setenv("GMAPS_PROXY_HOST", "host:8080")
    monkeypatch.setenv("GMAPS_PROXY_USER", "user")
    with pytest.raises(ConfigurationError):
        resolve_proxy()
    monkeypatch.setenv("GMAPS_PROXY_HOST", "http://host:8080")
    with pytest.raises(ConfigurationError):
        resolve_proxy()


def test_proxy_is_passed_to_httpx(monkeypatch):
    seen = {}
    real = httpx.Client

    def spy(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(httpx, "Client", spy)
    GMapsClient(proxy="http://user:pass@host:8080").close()
    assert seen["proxy"] == "http://user:pass@host:8080"


def test_consent_cookie_is_deterministic_for_a_time():
    a = generate_consent_cookie(now=1_800_000_000)
    assert a == generate_consent_cookie(now=1_800_000_000)
    assert a != generate_consent_cookie(now=1_800_086_400)
    assert "=" not in a
