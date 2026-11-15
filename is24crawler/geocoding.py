import random
import time

import requests

from is24crawler import settings

# Failure reasons written to the pending file. They are stable machine readable
# codes; the human readable explanation lives in the record's ``detail`` field.
REASON_INCOMPLETE_ADDRESS = "incomplete_address"
REASON_AMBIGUOUS_RESULT = "ambiguous_result"
REASON_REQUEST_ERROR = "request_error"

GEOCODE_REASONS = (REASON_INCOMPLETE_ADDRESS, REASON_AMBIGUOUS_RESULT, REASON_REQUEST_ERROR)

EXACT_MATCH_TYPES = ("street_address", "establishment", "premise")

GOOGLE_GEOCODE_ENDPOINT = "http://maps.google.com/maps/api/geocode/json?address={}"


class GeocodeError(Exception):
    """Raised when an address cannot be turned into coordinates.

    ``reason`` is one of the ``REASON_*`` codes above, ``detail`` explains the
    failure in more detail and is stored verbatim in the pending file.
    """

    def __init__(self, reason, detail = ""):
        super(GeocodeError, self).__init__(detail)
        self.reason = reason
        self.detail = detail


def split_address(address):
    """
    Splits an immobilienscout24 map link text like
    ``"Soldiner Straße 20, Wedding (Wedding), Berlin"`` into its parts.
    :return: a tuple (street, district, subdistrict, city).
    :raises GeocodeError: with reason "incomplete_address" if a part is missing.
    """
    parts = [part.strip() for part in str(address).split(",")]
    if len(parts) != 3 or any(len(part) == 0 for part in parts):
        raise GeocodeError(REASON_INCOMPLETE_ADDRESS,
                           "expected 'street, district (subdistrict), city' but got {} part(s): '{}'".format(
                               len(parts), address))

    street, area, city = parts
    if "(" not in area or ")" not in area or area.count("(") != 1:
        raise GeocodeError(REASON_INCOMPLETE_ADDRESS,
                           "district/subdistrict missing in address part '{}' of '{}'".format(area, address))

    district, subdistrict = area.replace(")", "").split("(")
    district = district.strip()
    subdistrict = subdistrict.strip()
    if len(street) == 0 or len(district) == 0 or len(subdistrict) == 0:
        raise GeocodeError(REASON_INCOMPLETE_ADDRESS,
                           "empty street, district or subdistrict in '{}'".format(address))

    return street, district, subdistrict, city


def build_geocode_url(address):
    """Builds the Google geocoding request URL for an immobilienscout24 address."""
    street, district, subdistrict, city = split_address(address)
    if district != subdistrict:  # duplicate params result in ambiguous results
        query = "{}, {}, {}, {}".format(street, district, subdistrict, city)
    else:
        query = "{}, {}, {}".format(street, district, city)
    return GOOGLE_GEOCODE_ENDPOINT.format(query)


def geocode_address(address, client):
    """
    Resolves an address to (latitude, longitude) using the given geocoding client.
    :param address: the address string as shown on the result page.
    :param client: a callable that takes a request URL and returns the decoded JSON response.
    :return: a tuple (lat, lng).
    :raises GeocodeError: if the address is incomplete, ambiguous, has no result or the
                          request itself failed.
    """
    url = build_geocode_url(address)
    try:
        payload = client(url)
    except GeocodeError:
        raise
    except (requests.RequestException, OSError, ValueError) as error:
        # a geocoding client that does not normalise its transport errors must not abort the page
        raise GeocodeError(REASON_REQUEST_ERROR, "request to geocoding API failed: {}".format(error))

    if not isinstance(payload, dict):
        raise GeocodeError(REASON_REQUEST_ERROR, "geocoding API returned a non-object JSON payload")

    status = payload.get("status")
    if status == "ZERO_RESULTS":
        raise GeocodeError(REASON_AMBIGUOUS_RESULT,
                           "geocoding API returned no results at all for '{}'".format(address))
    if status is not None and status != "OK":
        raise GeocodeError(REASON_REQUEST_ERROR, "geocoding API returned status '{}'".format(status))

    results = payload.get("results") or []
    if not isinstance(results, list):
        raise GeocodeError(REASON_REQUEST_ERROR, "geocoding API returned malformed 'results': {!r}".format(results))
    exact_matches = [result for result in results if _is_exact_match(result)]

    if len(exact_matches) == 0:
        raise GeocodeError(REASON_AMBIGUOUS_RESULT,
                           "no exact match among {} geocoding result(s) for '{}'".format(len(results), address))
    if len(exact_matches) > 1:
        raise GeocodeError(REASON_AMBIGUOUS_RESULT,
                           "{} exact matches for '{}', expected exactly one".format(len(exact_matches), address))

    location = (exact_matches[0].get("geometry") or {}).get("location") or {}
    try:
        lat = float(location["lat"])
        lng = float(location["lng"])
    except (KeyError, TypeError, ValueError):
        raise GeocodeError(REASON_REQUEST_ERROR,
                           "geocoding result for '{}' has no usable geometry".format(address))
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
        # an out of range coordinate would make the UTM conversion explode later on
        raise GeocodeError(REASON_REQUEST_ERROR,
                           "geocoding result for '{}' has out of range coordinates ({}, {})".format(address, lat, lng))
    return lat, lng


def _is_exact_match(result):
    if not isinstance(result, dict):
        return False
    types = result.get("types") or []
    return any(match_type in types for match_type in EXACT_MATCH_TYPES)


class GoogleMapsGeocodingClient(object):
    """Geocoding client for the Google Maps API with throttling and error normalisation."""

    def __init__(self, get = requests.get, timeout = None, min_delay = None, max_delay = None, sleep = time.sleep):
        self._get = get
        self._timeout = settings.GEOCODE_TIMEOUT if timeout is None else timeout
        self._min_delay = settings.GEOCODE_MIN_DELAY if min_delay is None else min_delay
        self._max_delay = settings.GEOCODE_MAX_DELAY if max_delay is None else max_delay
        self._sleep = sleep
        self._calls = 0

    def __call__(self, url):
        if self._calls > 0 and self._max_delay > 0:  # prevent getting blocked from the google API
            self._sleep(random.randint(self._min_delay, self._max_delay))
        self._calls += 1

        try:
            response = self._get(url, timeout = self._timeout)
        except (requests.RequestException, OSError, ValueError) as error:
            # ConnectionError, Timeout, HTTPError and clients raising raw transport errors all
            # end up as the same machine readable reason instead of aborting the page.
            raise GeocodeError(REASON_REQUEST_ERROR, "request to geocoding API failed: {}".format(error))

        if hasattr(response, "ok") and not response.ok:
            raise GeocodeError(REASON_REQUEST_ERROR,
                               "geocoding API answered with HTTP status {}".format(getattr(response, "status_code",
                                                                                           "unknown")))

        try:
            return response.json()
        except ValueError as error:
            raise GeocodeError(REASON_REQUEST_ERROR, "geocoding API returned invalid JSON: {}".format(error))
