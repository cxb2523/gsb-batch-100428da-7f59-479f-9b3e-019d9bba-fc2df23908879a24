from gmaps_extractor.parsers.business import _address


def test_address_drops_leading_place_name():
    assert _address("BRB Coffee, 3501 Guadalupe St, Austin, TX 78705", "BRB Coffee") == "3501 Guadalupe St, Austin, TX 78705"


def test_address_without_prefix_is_kept():
    assert _address("3501 Guadalupe St, Austin, TX 78705", "BRB Coffee") == "3501 Guadalupe St, Austin, TX 78705"
    assert _address(None, "BRB Coffee") is None
