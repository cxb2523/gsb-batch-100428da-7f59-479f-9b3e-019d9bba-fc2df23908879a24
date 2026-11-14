import json
import logging
import os
import tempfile

from geopy.location import Location

from trouver_une_fresque_scraper.utils import location
from trouver_une_fresque_scraper.utils.errors import (
    FreskAddressBadFormat,
    FreskAddressIncomplete,
    FreskAddressNotFound,
    FreskCountryNotSupported,
)


class FakeGeocoder:
    """Local stand-in for ``Nominatim`` that records every queried string."""

    def __init__(self, results):
        # results maps a candidate string to the raw payload returned by
        # Nominatim, or None when Nominatim finds nothing.
        self.results = results
        self.calls = []

    def geocode(self, query, addressdetails=True):
        self.calls.append(query)
        raw = self.results.get(query)
        if raw is None:
            return None
        return Location(
            address=raw.get("display_name", ""),
            point=(raw["lat"], raw["lon"]),
            raw=raw,
        )


def _raw_payload(country_code="fr", **address_overrides):
    address = {
        "country_code": country_code,
        "road": "Rue de la Paix",
        "city": "Paris",
        "state_district": "Paris",
        "postcode": "75002",
    }
    address.update(address_overrides)
    address = {key: value for key, value in address.items() if value is not None}
    return {
        "display_name": "Rue de la Paix, Paris, France",
        "name": "Rue de la Paix",
        "lat": "48.8698",
        "lon": "2.3311",
        "address": address,
    }


def _reset_state(geocoder_results, cache_file=None, cache_entries=None):
    """
    Puts the module in a known state without ever touching the real Nominatim.
    ``cache_entries`` are treated as in-memory-only entries.
    """
    location._geocode_cache = dict(cache_entries or {})
    location._geocode_cache_from_disk = set()
    location._geocode_cache_file = cache_file
    location._last_geocode_source = None
    location.last_resolution_trace = None
    location.geolocator = FakeGeocoder(geocoder_results)
    return location.geolocator


def _check(name, condition, detail=""):
    if condition:
        logging.info(f"OK: {name}")
        return True
    logging.error(f"FAILED: {name}" + (f" — {detail}" if detail else ""))
    return False


def _attempts(trace):
    return [entry for entry in trace if "candidate" in entry]


def _expect_exception(expected_type, fn):
    try:
        fn()
    except expected_type:
        return True
    except Exception as e:  # noqa: BLE001 - report the actual type in the log
        logging.error(f"Expected {expected_type.__name__}, got {type(e).__name__}: {e}")
        return False
    logging.error(f"Expected {expected_type.__name__}, nothing was raised")
    return False


def run_tests():
    results = []

    ############################################################################
    # 1. Fallback order and trace content on a successful resolution.
    ############################################################################
    full_location = "Salle des Fêtes (accès par la rue A)\nParis, Ile-de-France, France"
    # ``geocode_location_string`` strips the candidate before querying.
    after_comma = "Ile-de-France, France"
    expected_candidates = [
        full_location,
        "Salle des Fêtes \nParis, Ile-de-France, France",
        after_comma,
    ]
    fake = _reset_state({after_comma: _raw_payload()})
    trace = []
    address_dict = location.get_address(full_location, trace=trace)

    results.append(
        _check(
            "fallback order matches the legacy candidate sequence",
            fake.calls == expected_candidates,
            f"calls={fake.calls!r}",
        )
    )
    results.append(
        _check(
            "trace lists each attempted candidate, in order, with its source",
            [a["candidate"] for a in _attempts(trace)] == expected_candidates
            and [a["source"] for a in _attempts(trace)]
            == ["request", "request", "request"]
            and [a["found"] for a in _attempts(trace)] == [False, False, True],
            f"attempts={_attempts(trace)!r}",
        )
    )
    results.append(
        _check(
            "trace records the candidate that was adopted",
            trace[-1]["outcome"] == "success"
            and trace[-1]["matched_candidate"] == after_comma,
            f"tail={trace[-1]!r}",
        )
    )
    results.append(
        _check(
            "returned dictionary keeps its keys and values",
            list(address_dict.keys())
            == [
                "location_name",
                "address",
                "city",
                "department",
                "zip_code",
                "country_code",
                "latitude",
                "longitude",
            ]
            and address_dict
            == {
                "location_name": "Rue de la Paix",
                "address": "Rue de la Paix",
                "city": "Paris",
                "department": "75",
                "zip_code": "75002",
                "country_code": "fr",
                "latitude": "48.8698",
                "longitude": "2.3311",
            },
            f"address_dict={address_dict!r}",
        )
    )
    results.append(
        _check(
            "trace is also published as readable text",
            location.last_resolution_trace is not None
            and "Address resolution trace" in location.last_resolution_trace
            and "outcome: success" in location.last_resolution_trace,
            f"trace={location.last_resolution_trace!r}",
        )
    )

    ############################################################################
    # 2. First-line fallback.
    ############################################################################
    multi_line = "Atelier\n10 rue de la Paix, Paris, France"
    only_second_line = "10 rue de la Paix, Paris, France"
    expected_calls = [multi_line, "Paris, France", only_second_line]
    fake = _reset_state({only_second_line: _raw_payload()})
    trace = []
    location.get_address(multi_line, trace=trace)
    results.append(
        _check(
            "multi-line fallback tries the line after the first one",
            fake.calls == expected_calls,
            f"calls={fake.calls!r}",
        )
    )
    results.append(
        _check(
            "multi-line fallback trace ends on the adopted candidate",
            trace[-1]["matched_candidate"] == only_second_line
            and trace[-1]["outcome"] == "success",
            f"tail={trace[-1]!r}",
        )
    )

    ############################################################################
    # 3. In-memory cache hits are labelled and never re-requested.
    ############################################################################
    cached_positive = "Paris, France"
    fake = _reset_state({}, cache_entries={cached_positive: _raw_payload()})
    trace = []
    location.get_address(cached_positive, trace=trace)
    results.append(
        _check(
            "positive memory cache hit skips Nominatim",
            fake.calls == [] and _attempts(trace)[0]["source"] == "memory_cache",
            f"calls={fake.calls!r}, attempts={_attempts(trace)!r}",
        )
    )

    fake = _reset_state({}, cache_entries={cached_positive: None})
    trace = []
    try:
        location.get_address(cached_positive, trace=trace)
    except FreskAddressNotFound:
        pass
    results.append(
        _check(
            "cached negative answer is reused without a request",
            fake.calls == ["France"]
            and _attempts(trace)[0]["source"] == "memory_cache",
            f"calls={fake.calls!r}, attempts={_attempts(trace)!r}",
        )
    )

    # Without parentheses the second step reproduces the very same string, so
    # the legacy code attempted it again — here it is served by the cache and
    # must not reach Nominatim twice.
    no_parentheses = "Salle des Fêtes, Paris, France"
    fake = _reset_state({})
    trace = []
    try:
        location.get_address(no_parentheses, trace=trace)
    except FreskAddressNotFound:
        pass
    attempts = _attempts(trace)
    results.append(
        _check(
            "repeated candidate is answered from memory, not re-requested",
            [a["candidate"] for a in attempts][:2] == [no_parentheses, no_parentheses]
            and [a["source"] for a in attempts][:2] == ["request", "memory_cache"]
            and fake.calls == [no_parentheses, "Paris, France"],
            f"attempts={attempts!r}, calls={fake.calls!r}",
        )
    )

    ############################################################################
    # 4. Disk cache provenance, and the disk -> memory transition.
    ############################################################################
    with tempfile.TemporaryDirectory(prefix="geocode-test-") as tmpdir:
        cache_path = os.path.join(tmpdir, "cache.json")
        disk_candidate = "Rue de la Paix, Paris, France"
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({disk_candidate: _raw_payload()}, f, ensure_ascii=False)

        fake = _reset_state({}, cache_file=cache_path)
        location._load_geocode_cache()
        location.geolocator = fake
        trace = []
        location.get_address(disk_candidate, trace=trace)
        results.append(
            _check(
                "disk cache hit is labelled and skips Nominatim",
                fake.calls == [] and _attempts(trace)[0]["source"] == "disk_cache",
                f"calls={fake.calls!r}, attempts={_attempts(trace)!r}",
            )
        )

        # Drop the entry, resolve it with a real request, then look it up again.
        location._geocode_cache.pop(disk_candidate)
        location._geocode_cache_from_disk.discard(disk_candidate)
        fake.results = {disk_candidate: _raw_payload()}
        trace = []
        location.get_address(disk_candidate, trace=trace)
        results.append(
            _check(
                "a key resolved by a request is reported as a request",
                _attempts(trace)[0]["source"] == "request"
                and fake.calls == [disk_candidate],
                f"attempts={_attempts(trace)!r}, calls={fake.calls!r}",
            )
        )
        trace = []
        location.get_address(disk_candidate, trace=trace)
        results.append(
            _check(
                "a key just resolved is then served from memory",
                _attempts(trace)[0]["source"] == "memory_cache"
                and fake.calls == [disk_candidate],
                f"attempts={_attempts(trace)!r}, calls={fake.calls!r}",
            )
        )

        # Negative disk entries are usable and labelled as disk hits.
        negative_candidate = "Lyon, France"
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({negative_candidate: None}, f)
        location._geocode_cache = {}
        location._geocode_cache_from_disk = set()
        location._geocode_cache_file = cache_path
        location._load_geocode_cache()
        fake = FakeGeocoder({})
        location.geolocator = fake
        trace = []
        try:
            location.get_address(negative_candidate, trace=trace)
        except FreskAddressNotFound:
            pass
        results.append(
            _check(
                "negative disk entry is a disk hit, not a request",
                # Only the negative entry is cached, so the "France" fallback
                # legitimately reaches the fake geocoder and misses.
                fake.calls == ["France"]
                and _attempts(trace)[0]["source"] == "disk_cache",
                f"calls={fake.calls!r}, attempts={_attempts(trace)!r}",
            )
        )

        ########################################################################
        # 5. Missing / corrupt / malformed disk cache degrades gracefully.
        ########################################################################
        missing_path = os.path.join(tmpdir, "does-not-exist.json")
        location._geocode_cache = {"sentinel": _raw_payload()}
        location._geocode_cache_from_disk = {"sentinel"}
        location._geocode_cache_file = missing_path
        location._load_geocode_cache()
        results.append(
            _check(
                "missing cache file keeps the in-memory state intact",
                location._geocode_cache.get("sentinel", {}).get("lat") == "48.8698",
                f"cache={location._geocode_cache!r}",
            )
        )

        corrupt_path = os.path.join(tmpdir, "corrupt.json")
        with open(corrupt_path, "w", encoding="utf-8") as f:
            f.write("{not valid json")
        location._geocode_cache = {"sentinel": _raw_payload()}
        location._geocode_cache_from_disk = {"sentinel"}
        location._geocode_cache_file = corrupt_path
        location._load_geocode_cache()
        results.append(
            _check(
                "corrupt cache file falls back to an empty cache",
                location._geocode_cache == {}
                and location._geocode_cache_from_disk == set(),
                f"cache={location._geocode_cache!r}",
            )
        )

        # The run continues: a lookup after a corrupt load still succeeds.
        location._geocode_cache_file = corrupt_path
        fake = FakeGeocoder({"Paris, France": _raw_payload()})
        location.geolocator = fake
        trace = []
        address_dict = location.get_address("Paris, France", trace=trace)
        results.append(
            _check(
                "lookups still succeed after a corrupt cache load",
                address_dict is not None
                and address_dict["city"] == "Paris"
                and fake.calls == ["Paris, France"],
                f"calls={fake.calls!r}",
            )
        )

        malformed_path = os.path.join(tmpdir, "malformed.json")
        with open(malformed_path, "w", encoding="utf-8") as f:
            json.dump({"broken": {"lat": "1"}, "also_broken": ["nope"]}, f)
        location._geocode_cache_file = malformed_path
        location._load_geocode_cache()
        results.append(
            _check(
                "malformed entries are dropped instead of crashing",
                location._geocode_cache == {},
                f"cache={location._geocode_cache!r}",
            )
        )

        list_root_path = os.path.join(tmpdir, "list-root.json")
        with open(list_root_path, "w", encoding="utf-8") as f:
            json.dump(["unexpected"], f)
        location._geocode_cache_file = list_root_path
        location._load_geocode_cache()
        results.append(
            _check(
                "unexpected cache root is treated as an empty cache",
                location._geocode_cache == {},
                f"cache={location._geocode_cache!r}",
            )
        )

    ############################################################################
    # 6. Existing FreskError conditions are unchanged by tracing.
    ############################################################################
    trace = []
    results.append(
        _check(
            "empty input still raises FreskAddressNotFound",
            _expect_exception(
                FreskAddressNotFound, lambda: location.get_address("", trace=trace)
            )
            and "empty input" in (location.last_resolution_trace or ""),
            f"trace={location.last_resolution_trace!r}",
        )
    )

    fake = _reset_state({})
    results.append(
        _check(
            "no geocoding match still raises FreskAddressNotFound",
            _expect_exception(
                FreskAddressNotFound,
                lambda: location.get_address("Nowhere in particular", trace=[]),
            ),
            f"calls={fake.calls!r}",
        )
    )

    _reset_state({"Paris, France": _raw_payload(country_code="de")})
    results.append(
        _check(
            "unsupported country still raises FreskCountryNotSupported",
            _expect_exception(
                FreskCountryNotSupported,
                lambda: location.get_address("Paris, France", trace=[]),
            ),
        )
    )

    _reset_state({"Paris, France": _raw_payload(road=None)})
    results.append(
        _check(
            "missing road still raises FreskAddressBadFormat",
            _expect_exception(
                FreskAddressBadFormat,
                lambda: location.get_address("Paris, France", trace=[]),
            ),
        )
    )

    _reset_state({"Paris, France": _raw_payload(postcode=None)})
    results.append(
        _check(
            "missing postcode still raises FreskAddressIncomplete",
            _expect_exception(
                FreskAddressIncomplete,
                lambda: location.get_address("Paris, France", trace=[]),
            ),
        )
    )

    ############################################################################
    # 7. Failure traces name every attempt and the step where it stopped.
    ############################################################################
    _reset_state({})
    trace = []
    try:
        location.get_address("Salle (fête)\nParis, France", trace=trace)
    except FreskAddressNotFound:
        pass
    results.append(
        _check(
            "failure trace lists every attempted candidate",
            [a["candidate"] for a in _attempts(trace)]
            == [
                "Salle (fête)\nParis, France",
                "Salle \nParis, France",
                "France",
                "Paris, France",
            ]
            and trace[-1]["outcome"] == "failure"
            and "no candidate geocoded" in trace[-1]["detail"],
            f"trace={trace!r}",
        )
    )

    ############################################################################
    # 8. A geocoder exception is attributed to the candidate that triggered it.
    ############################################################################

    class ExplodingGeocoder(FakeGeocoder):
        def geocode(self, query, addressdetails=True):
            self.calls.append(query)
            if query == "Paris, France":
                raise RuntimeError("network down")
            return None

    location._geocode_cache = {}
    location._geocode_cache_from_disk = set()
    location.last_resolution_trace = None
    exploding = ExplodingGeocoder({})
    location.geolocator = exploding
    trace = []
    results.append(
        _check(
            "geocoder exceptions still propagate unchanged",
            _expect_exception(
                RuntimeError,
                lambda: location.get_address("Atelier, Paris, France", trace=trace),
            ),
            f"calls={exploding.calls!r}",
        )
    )
    attempts = _attempts(trace)
    results.append(
        _check(
            "failing candidate is named in the trace",
            [a["candidate"] for a in attempts]
            == ["Atelier, Paris, France", "Atelier, Paris, France", "Paris, France"]
            and attempts[-1]["error"] == "RuntimeError('network down')"
            and trace[-1]["outcome"] == "failure"
            and "Paris, France" in trace[-1]["detail"],
            f"attempts={attempts!r}, tail={trace[-1]!r}",
        )
    )

    failed = sum(1 for ok in results if not ok)
    logging.info(f"location tests: {len(results) - failed}/{len(results)} passed")
    return failed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(run_tests())
