import json

from conftest import fixture_text

from gmaps_extractor.parsers import extract_businesses, extract_hours, parse_google_response, safe_get
from gmaps_extractor.parsers.hours import parse_hours_current


def test_parse_strips_prefix():
    assert parse_google_response(')]}\'\n[1,[2,"x"]]') == [1, [2, "x"]]
    assert parse_google_response("") == []
    assert parse_google_response(")]}'") == []
    assert parse_google_response("<html>no json</html>") == []


def test_parse_takes_first_array_when_trailing_data():
    assert parse_google_response(')]}\'\n[1,"a]b"]\n/*""*/{"x":1}') == [1, "a]b"]


def test_safe_get():
    data = [0, [1, {"k": [5]}]]
    assert safe_get(data, 1, 1, "k", 0) == 5
    assert safe_get(data, 3) is None
    assert safe_get(data, 1, 0, 0, default="d") == "d"
    assert safe_get(None, 0) is None


def test_extract_businesses_from_search_answer():
    places = extract_businesses(parse_google_response(fixture_text("search_page.txt")))
    assert [p["name"] for p in places] == ["Example Coffee Roasters", "Sample Espresso Bar", "Café Ünïcode & Co!"]
    first = places[0]
    assert first["address"] == "100 Example St, Austin, TX 78701"
    assert first["phone"] == "(512) 555-0100"
    assert first["website"] == "https://example-coffee.test/"
    assert first["rating"] == 4.6 and first["reviews_count"] == 812
    assert first["category"] == "Coffee shop" and first["categories"] == ["Coffee shop", "Cafe"]
    assert (first["latitude"], first["longitude"]) == (30.2672, -97.7431)
    assert first["place_id"] == "ChIJexample0000000000001"
    assert first["hex_id"] == "0x8644b5a000000001:0x1111111111111111"
    assert first["ftid"] == "/g/11example001"


def test_extract_businesses_optional_fields():
    places = extract_businesses(parse_google_response(fixture_text("search_page.txt")))
    second, third = places[1], places[2]
    assert second["website"] == "https://sample-espresso.test/"
    assert second["phone"] is None and second["ftid"] is None
    assert third["address"] is None and third["rating"] is None and third["reviews_count"] is None
    assert third["category"] is None and third["categories"] == []
    assert third["phone"] == "+1 512-555-0199"


def test_extract_businesses_empty_and_garbage():
    assert extract_businesses(parse_google_response(fixture_text("search_empty.txt"))) == []
    assert extract_businesses(None) == []
    assert extract_businesses({"not": "a list"}) == []


def test_hours_current_format():
    hours = extract_hours(parse_google_response(fixture_text("place_hours.txt")))
    assert hours == {
        "sunday": "Closed",
        "monday": "7 AM-6 PM",
        "tuesday": "7 AM-6 PM",
        "wednesday": "7 AM-6 PM",
        "thursday": "7 AM-6 PM",
        "friday": "11 AM-2 PM, 5-10 PM",
        "saturday": "Open 24 hours",
    }


def test_hours_output_has_nothing_but_hours():
    hours = extract_hours(parse_google_response(fixture_text("place_hours.txt")))
    text = json.dumps(hours)
    assert "UNPARSED" not in text and "Synthetic" not in text and "@" not in text


def test_hours_legacy_format():
    hours = extract_hours(parse_google_response(fixture_text("place_hours_legacy.txt")))
    assert hours["sunday"] == "Closed"
    assert hours["monday"] == "9 AM-5 PM"
    assert hours["friday"] == "9 AM-3 PM"
    assert hours["saturday"] == "Closed"


def test_hours_short_place_array():
    hours = extract_hours(parse_google_response(fixture_text("place_hours_short.txt")))
    assert hours == {"sunday": "Closed", "monday": "7 AM-6 PM", "tuesday": "7 AM-6 PM"}


def test_no_hours():
    assert extract_hours(parse_google_response(fixture_text("place_no_hours.txt"))) is None
    assert extract_hours([]) is None
    assert extract_hours(None) is None


def test_day_number_zero_and_unknown_label():
    rows = [["Sun", 0, None, [["Closed"]]], ["Lundi", None, None, [["9-17"]]], ["Bad"], "junk"]
    assert parse_hours_current(rows) == {"sunday": "Closed", "lundi": "9-17"}
