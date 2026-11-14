from urllib.parse import parse_qs, unquote, urlparse

import pytest

from gmaps_extractor.urls import (
    build_place_pb,
    build_place_url,
    build_search_url,
    fmt_coord,
    google_maps_url,
    place_referer,
    place_request_name,
)


def _nested_check(pb: str) -> None:
    """Every ``!<n>m<count>`` message must hold exactly ``count`` following tokens."""
    tokens = pb.split("!")[1:]
    pos = 0

    def read_field() -> int:
        """Read one field (and its whole sub-message); return the tokens consumed."""
        nonlocal pos
        assert pos < len(tokens), "a message runs past the end of the pb string"
        token = tokens[pos]
        pos += 1
        digits = len(token) - len(token.lstrip("0123456789"))
        assert digits > 0 and len(token) > digits, f"bad token {token!r}"
        if token[digits] != "m":
            return 1
        count = int(token[digits + 1 :])
        used = 0
        while used < count:
            used += read_field()
        assert used == count, f"message {token!r} overruns its count"
        return 1 + count

    while pos < len(tokens):
        read_field()


def test_nested_check_catches_a_wrong_count():
    _nested_check("!1m2!1i1!2i2!3i3")
    with pytest.raises(AssertionError):
        _nested_check("!1m3!1i1!2i2")


@pytest.mark.parametrize(
    "value,expected",
    [(30.2672, "30.2672"), (-97.7431, "-97.7431"), (0.00001234, "0.0000123"), (5, "5"), (float("nan"), "0"),
     (float("inf"), "0"), ("x", "0"), (None, "0"), (-0.0, "0")],
)
def test_fmt_coord(value, expected):
    assert fmt_coord(value) == expected


def test_search_url_shape():
    url = build_search_url("coffee shop", 30.2672, -97.7431, results_count=20, offset=40)
    parsed = urlparse(url)
    assert parsed.netloc == "www.google.com" and parsed.path == "/search"
    assert "tbm=map" in url and "&q=coffee+shop&" in url
    assert "!1scoffee%20shop!" in url
    assert "!2d-97.7431!3d30.2672!" in url
    assert "!7i20!8i40!" in url
    assert "e-0" not in url  # no scientific notation


def test_search_url_tiny_coordinate_is_fixed_point():
    url = build_search_url("x", 0.00001, -0.00001)
    assert "!2d-0.00001!3d0.00001!" in url


@pytest.mark.parametrize("ftid,first", [(None, "!1m14!"), ("/g/11abc", "!1m17!")])
def test_place_pb_first_message_count(ftid, first):
    pb = build_place_pb("0x1:0x2", "Example Cafe", 30.1, -97.5, ftid)
    assert pb.startswith(first)
    assert ("!15m2!1m1!4s/g/11abc" in pb) == bool(ftid)
    # Inside the first message: 1s, 2s, 3m8 (+8), 4m2 (+2) [+ 15m2 (+2)] = 14 or 17 tokens.
    _nested_check(pb)


def test_place_pb_name_and_ids_raw():
    pb = build_place_pb("0x8644:0xabc", "Joe Cafe", 30.1, -97.5)
    assert "!1s0x8644:0xabc!2sJoe+Cafe!" in pb


@pytest.mark.parametrize(
    "name,expected",
    [("China! Cafe", "China Cafe"), ("No #1 Diner", "No 1 Diner"), ("A*B", "A B"), ("Plain", "Plain"),
     ("", ""), (None, "")],
)
def test_place_request_name(name, expected):
    assert place_request_name(name) == expected


def test_place_url_carries_safe_name_and_encoded_pb():
    url = build_place_url("0x1:0x2", "Café #1 & Co!", 30.1, -97.5, "/g/11abc")
    parsed = urlparse(url)
    assert parsed.path == "/maps/preview/place"
    assert parsed.fragment == ""
    query = parse_qs(parsed.query)
    assert query["q"] == ["Café 1 & Co"]
    pb = query["pb"][0]
    assert pb.startswith("!1m17!1s0x1:0x2!2sCaf")
    _nested_check(pb)


def test_referer_is_ascii():
    referer = place_referer("Joe’s Café")
    referer.encode("ascii")
    assert unquote(referer) == "https://www.google.com/maps/place/Joe’s+Café/"
    assert place_referer("Plain Name") == "https://www.google.com/maps/place/Plain+Name/"


def test_google_maps_url():
    assert google_maps_url("Example Cafe", "ChIJabc") == (
        "https://www.google.com/maps/search/?api=1&query=Example%20Cafe&query_place_id=ChIJabc"
    )
    assert google_maps_url("Example Cafe", None) is None


def test_search_pb_counts_are_consistent():
    url = build_search_url("coffee shop", 30.2672, -97.7431)
    pb = unquote(parse_qs(urlparse(url).query.replace("+", "%2B"))["pb"][0])
    _nested_check(pb)
