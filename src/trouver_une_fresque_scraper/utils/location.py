import atexit
import json
import logging
import os
import re

from geopy.location import Location
from trouver_une_fresque_scraper.utils.errors import *

from geopy.geocoders import Nominatim

geolocator = Nominatim(user_agent="trouver-une-fresque", timeout=10)

# Disk-backed geocode cache
_geocode_cache = {}
# Keys that were loaded from disk and have not been answered by a fresh
# request during this process. Used only to label the resolution trace.
_geocode_cache_from_disk = set()
_geocode_cache_file = os.environ.get("GEOCODE_CACHE_FILE")

# Source of the most recent ``geocode_location_string`` answer:
# "memory_cache", "disk_cache" or "request". Tracing only.
_last_geocode_source = None
# Human-readable trace produced by the most recent ``get_address`` call.
last_resolution_trace = None


def log_resolution_trace(trace):
    """Logs a resolution trace; separated so it can be observed in tests."""
    logging.info(trace)


def _is_usable_cache_entry(entry):
    """A cache entry is either a negative result or a raw Nominatim payload."""
    if entry is None:
        return True
    return isinstance(entry, dict) and "lat" in entry and "lon" in entry


def _load_geocode_cache():
    """Load the geocode cache from disk if GEOCODE_CACHE_FILE is set."""
    global _geocode_cache, _geocode_cache_from_disk
    if _geocode_cache_file and os.path.exists(_geocode_cache_file):
        try:
            with open(_geocode_cache_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if not isinstance(loaded, dict):
                raise ValueError("geocode cache root is not a JSON object")
            _geocode_cache = {
                key: entry
                for key, entry in loaded.items()
                if _is_usable_cache_entry(entry)
            }
            if len(_geocode_cache) != len(loaded):
                logging.warning(
                    f"Dropped {len(loaded) - len(_geocode_cache)} malformed geocode cache entries"
                )
            _geocode_cache_from_disk = set(_geocode_cache)
            logging.info(
                f"Loaded {len(_geocode_cache)} geocode cache entries from {_geocode_cache_file}"
            )
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logging.warning(f"Could not load geocode cache: {e}")
            _geocode_cache = {}
            _geocode_cache_from_disk = set()


def _save_geocode_cache():
    """Save the geocode cache to disk if GEOCODE_CACHE_FILE is set."""
    if _geocode_cache_file and _geocode_cache:
        try:
            with open(_geocode_cache_file, "w", encoding="utf-8") as f:
                json.dump(_geocode_cache, f, ensure_ascii=False, indent=2)
            logging.info(
                f"Saved {len(_geocode_cache)} geocode cache entries to {_geocode_cache_file}"
            )
        except OSError as e:
            logging.warning(f"Could not save geocode cache: {e}")


# Load cache on import and register save on exit
_load_geocode_cache()
atexit.register(_save_geocode_cache)

departments = {
    "01": "Ain",
    "02": "Aisne",
    "03": "Allier",
    "04": "Alpes-de-Haute-Provence",
    "05": "Hautes-Alpes",
    "06": "Alpes-Maritimes",
    "07": "Ardèche",
    "08": "Ardennes",
    "09": "Ariège",
    "10": "Aube",
    "11": "Aude",
    "12": "Aveyron",
    "13": "Bouches-du-Rhône",
    "14": "Calvados",
    "15": "Cantal",
    "16": "Charente",
    "17": "Charente-Maritime",
    "18": "Cher",
    "19": "Corrèze",
    "2A": "Corse-du-Sud",
    "2B": "Haute-Corse",
    "21": "Côte-d'Or",
    "22": "Côtes-d'Armor",
    "23": "Creuse",
    "24": "Dordogne",
    "25": "Doubs",
    "26": "Drôme",
    "27": "Eure",
    "28": "Eure-et-Loir",
    "29": "Finistère",
    "30": "Gard",
    "31": "Haute-Garonne",
    "32": "Gers",
    "33": "Gironde",
    "34": "Hérault",
    "35": "Ille-et-Vilaine",
    "36": "Indre",
    "37": "Indre-et-Loire",
    "38": "Isère",
    "39": "Jura",
    "40": "Landes",
    "41": "Loir-et-Cher",
    "42": "Loire",
    "43": "Haute-Loire",
    "44": "Loire-Atlantique",
    "45": "Loiret",
    "46": "Lot",
    "47": "Lot-et-Garonne",
    "48": "Lozère",
    "49": "Maine-et-Loire",
    "50": "Manche",
    "51": "Marne",
    "52": "Haute-Marne",
    "53": "Mayenne",
    "54": "Meurthe-et-Moselle",
    "55": "Meuse",
    "56": "Morbihan",
    "57": "Moselle",
    "58": "Nièvre",
    "59": "Nord",
    "60": "Oise",
    "61": "Orne",
    "62": "Pas-de-Calais",
    "63": "Puy-de-Dôme",
    "64": "Pyrénées-Atlantiques",
    "65": "Hautes-Pyrénées",
    "66": "Pyrénées-Orientales",
    "67": "Bas-Rhin",
    "68": "Haut-Rhin",
    "69": "Rhône",
    "70": "Haute-Saône",
    "71": "Saône-et-Loire",
    "72": "Sarthe",
    "73": "Savoie",
    "74": "Haute-Savoie",
    "75": "Paris",
    "76": "Seine-Maritime",
    "77": "Seine-et-Marne",
    "78": "Yvelines",
    "79": "Deux-Sèvres",
    "80": "Somme",
    "81": "Tarn",
    "82": "Tarn-et-Garonne",
    "83": "Var",
    "84": "Vaucluse",
    "85": "Vendée",
    "86": "Vienne",
    "87": "Haute-Vienne",
    "88": "Vosges",
    "89": "Yonne",
    "90": "Territoire de Belfort",
    "91": "Essonne",
    "92": "Hauts-de-Seine",
    "93": "Seine-Saint-Denis",
    "94": "Val-de-Marne",
    "95": "Val-d'Oise",
    "971": "Guadeloupe",
    "972": "Martinique",
    "973": "Guyane",
    "974": "La Réunion",
    "976": "Mayotte",
}

cache = {}


def geocode_location_string(location_string):
    """
    Requests Nominatim to geocode an input string. Results are cached in memory
    and persisted to disk (when GEOCODE_CACHE_FILE is set) so they survive
    across scraping attempts.

    Returns the ``geopy.location.Location`` (or ``None``) as before; the source
    of the result is recorded through ``_last_geocode_source`` for tracing only
    and does not change the return value.
    """
    global _last_geocode_source
    location_string = location_string.strip()
    if location_string in _geocode_cache:
        raw = _geocode_cache[location_string]
        _last_geocode_source = (
            "disk_cache"
            if location_string in _geocode_cache_from_disk
            else "memory_cache"
        )
        if raw is None:
            return None
        return Location(
            address=raw.get("display_name", ""),
            point=(raw["lat"], raw["lon"]),
            raw=raw,
        )

    _last_geocode_source = "request"
    logging.info(f"Calling geocoder: {location_string}")
    result = geolocator.geocode(location_string, addressdetails=True)
    _geocode_cache[location_string] = result.raw if result else None
    _geocode_cache_from_disk.discard(location_string)
    return result


def _candidate_strings(full_location):
    """
    Builds the ordered list of strings Nominatim may be queried with, mirroring
    the historical fallback order exactly: raw string, parenthesis-stripped,
    substring after the first comma, then everything past the first line.

    Duplicates are kept on purpose: the historical code attempted each step in
    turn even when it produced the same string again, and such a repeat is
    answered by the cache instead of Nominatim. ``geocode_location_string``
    strips each candidate before querying/caching, as it always did.
    """
    candidates = [full_location]

    without_parentheses = re.sub(r"\(.*\)", "", full_location)
    candidates.append(without_parentheses)

    if "," in without_parentheses:
        candidates.append(without_parentheses.split(",", 1)[1])

    lines = without_parentheses.splitlines(keepends=True)
    if len(lines) > 1:
        candidates.append("".join(lines[1:]))

    return candidates


def _format_trace(input_str, attempts, outcome, detail=""):
    """Renders the resolution trace as human-readable log lines."""
    lines = [f'Address resolution trace for "{input_str}":']
    for index, attempt in enumerate(attempts, start=1):
        candidate = attempt["candidate"].replace("\n", "\\n")
        status = "hit" if attempt["found"] else "miss"
        lines.append(f"  {index}. {candidate!r} -> {attempt['source']} ({status})")
    if not attempts:
        lines.append("  (no candidate was queried)")
    lines.append(f"  outcome: {outcome}" + (f" ({detail})" if detail else ""))
    return "\n".join(lines)


def _record_trace(input_str, attempts, outcome, detail, matched_candidate, trace):
    """
    Publishes the trace without altering the geocoding result: writes it to the
    log, stores it on the module-level ``last_resolution_trace`` and appends the
    structured records to the optional ``trace`` list provided by the caller.
    """
    global last_resolution_trace
    rendered = _format_trace(input_str, attempts, outcome, detail)
    last_resolution_trace = rendered
    log_resolution_trace(rendered)
    if trace is not None:
        trace.extend(attempts)
        trace.append(
            {
                "outcome": outcome,
                "detail": detail,
                "matched_candidate": matched_candidate,
            }
        )


def get_address(full_location, trace=None):
    """
    Gets structured location data from an input string, tries substrings if
    relevant, verifies that the result is sufficiently precise (address or park
    level) and returns a dictionnary with the address properties.

    The returned dictionary and the ``FreskError`` conditions are unchanged.
    Each call also emits a readable resolution trace: which candidate string was
    tried (in order), whether it came from the in-memory cache, the disk cache
    or a real Nominatim request, and the final outcome. The trace is written to
    the log, kept on the module-level ``last_resolution_trace`` attribute, and
    (optionally) appended to the ``trace`` list passed by the caller.
    """
    attempts = []
    matched_candidate = None
    outcome = "failure"
    detail = ""
    # Matches the historical error-message input: once the raw string fails,
    # the parenthesis-stripped form becomes the reference string.
    input_for_errors = full_location

    try:
        if not full_location:
            detail = "empty input"
            raise FreskAddressNotFound("")

        normalized_location = re.sub(r"\(.*\)", "", full_location)
        location = None
        queried = None
        for index, candidate in enumerate(_candidate_strings(full_location)):
            # Record the string as Nominatim/cache actually saw it: the geocoder
            # helper strips its input.
            queried = candidate.strip()
            attempt = {"candidate": queried, "source": None, "found": False}
            attempts.append(attempt)
            try:
                location = geocode_location_string(candidate)
            except Exception as e:
                attempt["source"] = _last_geocode_source or "request"
                attempt["error"] = repr(e)
                detail = f"geocoder raised on candidate {queried!r}: {e}"
                raise
            attempt["source"] = _last_geocode_source
            attempt["found"] = location is not None
            if location is not None:
                matched_candidate = queried
                break
            if index == 0:
                input_for_errors = normalized_location
        if location is None:
            detail = "no candidate geocoded"
            raise FreskAddressNotFound(input_for_errors)

        address = location.raw["address"]

        if (
            address["country_code"] != "fr"
            and address["country_code"] != "ch"
            and address["country_code"] != "gb"
        ):
            detail = f"unsupported country; matched candidate {matched_candidate!r}"
            raise FreskCountryNotSupported(address, input_for_errors)

        house_number = ""
        if "house_number" in address.keys():
            house_number = f"{address['house_number']} "

        road = ""
        if "road" in address.keys():
            road = address["road"]
        elif "square" in address.keys():
            road = address["square"]
        elif "park" in address.keys():
            road = address["park"]
        else:
            detail = f"missing road; matched candidate {matched_candidate!r}"
            raise FreskAddressBadFormat(address, input_for_errors, "road")

        city = None
        if "city" in address.keys():
            city = address["city"]
        elif "town" in address.keys():
            city = address["town"]
        elif "village" in address.keys():
            city = address["village"]
        else:
            detail = f"missing city; matched candidate {matched_candidate!r}"
            raise FreskAddressBadFormat(address, input_for_errors, "city")

        # Trying to infer the "department" code
        num_department = None
        if address["country_code"] == "fr":
            department = None
            if "state_district" in address.keys():
                department = address["state_district"]
            elif "county" in address.keys():
                department = address["county"]
            elif "city_district" in address.keys():
                department = address["city_district"]
            elif "state" in address.keys():
                department = address["state"]
            else:
                detail = f"missing department; matched candidate {matched_candidate!r}"
                raise FreskAddressBadFormat(address, input_for_errors, "department")
            try:
                num_department = department_to_num(department)
            except FreskError:
                detail = f"unknown department; matched candidate {matched_candidate!r}"
                raise
        if address["country_code"] == "ch":
            # Swiss department "numbers" are ISO codes from https://en.wikipedia.org/wiki/ISO_3166-2:CH.
            if "ISO3166-2-lvl4" in address.keys():
                canton = address["ISO3166-2-lvl4"]
                if not canton.startswith("CH-"):
                    detail = f"bad canton; matched candidate {matched_candidate!r}"
                    raise FreskAddressBadFormat(address, input_for_errors, "department")
                num_department = canton[3:]
            else:
                detail = f"missing canton; matched candidate {matched_candidate!r}"
                raise FreskAddressBadFormat(address, input_for_errors, "department")

        # Missing fields
        if "postcode" not in address:
            detail = f"missing postcode; matched candidate {matched_candidate!r}"
            raise FreskAddressIncomplete(address, input_for_errors, "postcode")

        outcome = "success"
        detail = f"matched candidate {matched_candidate!r}"

    except FreskError as e:
        logging.error(f"get_address: {e}")
        if not detail:
            detail = str(e)
        raise
    except Exception as e:
        # Unexpected failures keep propagating unchanged; the trace still
        # records how far the resolution got.
        if not detail:
            detail = f"unexpected error: {e!r}"
        raise
    finally:
        _record_trace(
            full_location or "", attempts, outcome, detail, matched_candidate, trace
        )

    return {
        "location_name": location.raw["name"],
        "address": f"{house_number}{road}",
        "city": city,
        "department": num_department,
        "zip_code": address["postcode"],
        "country_code": address["country_code"],
        "latitude": location.raw["lat"],
        "longitude": location.raw["lon"],
    }


def department_to_num(department):
    for k, v in departments.items():
        if v == department:
            return k
    raise FreskDepartmentNotFound(f"Department number.")
