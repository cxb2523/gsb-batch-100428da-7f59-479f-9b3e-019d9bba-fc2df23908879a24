"""Request builders for Google Maps' internal search and place endpoints.

Google Maps encodes its request options in a ``pb`` parameter: a flat,
``!``-separated serialisation of a protobuf message. Each token is
``!<field><type><value>``; a message token ``!<field>m<n>`` says that the next
``n`` tokens belong to it. The strings below were captured from a browser and
must keep their exact shape: a wrong element count makes Google answer with
HTTP 400 or an empty place.
"""

import math
import re
from typing import Optional
from urllib.parse import quote

SEARCH_ENDPOINT = "https://www.google.com/search"
PLACE_ENDPOINT = "https://www.google.com/maps/preview/place"


def fmt_coord(value) -> str:
    """Format a coordinate or distance for a ``pb`` field.

    A plain f-string writes small numbers in scientific notation
    (``1.23e-05``) and non-finite numbers as ``nan``/``inf``; Google reads
    neither as a number. This writes fixed-point with up to 7 decimals and
    falls back to ``0`` for anything that is not a finite number.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "0"
    if not math.isfinite(number):
        return "0"
    text = f"{number:.7f}".rstrip("0").rstrip(".")
    return text if text and text != "-0" else "0"


def build_search_url(
    query: str,
    lat: float,
    lng: float,
    results_count: int = 20,
    max_radius: int = 5000,
    viewport_dist: float = 10000,
    offset: int = 0,
) -> str:
    """Build the URL of one Google Maps search page around ``(lat, lng)``.

    ``q=`` uses ``+`` for spaces, the ``!1s`` field inside ``pb`` uses
    ``%20``; the ``pb`` value is placed in the URL as is.
    """
    query_encoded = quote(query)
    query_url = query_encoded.replace("%20", "+")
    return (
        f"{SEARCH_ENDPOINT}"
        f"?tbm=map&authuser=0&hl=en&gl=us"
        f"&q={query_url}"
        f"&pb=!1s{query_encoded}"
        f"!4m8!1m3!1d{fmt_coord(viewport_dist)}!2d{fmt_coord(lng)}!3d{fmt_coord(lat)}"
        f"!3m2!1i1024!2i768!4f13.1"
        f"!7i{int(results_count)}!8i{int(offset)}"
        f"!10b1"
        f"!12m50!1m5!18b1!30b1!31m1!1b1!34e1"
        f"!2m4!5m1!6e2!20e3!39b1"
        f"!6m23!49b1!63m0!66b1!74i{int(max_radius)}"
        f"!85b1!91b1!114b1!149b1!206b1!209b1!212b1!213b1"
        f"!223b1!232b1!233b1!234b1!244b1!246b1!250b1!253b1"
        f"!258b1!260b1!263b1"
        f"!10b1!12b1!13b1!14b1!16b1"
        f"!17m1!3e1!20m3!5e2!6b1!14b1!46m1!1b0!96b1!99b1"
    )


# "!" is the pb field separator and "*" its escape character, so either one in a
# business name corrupts the pb message; "#" in the q= value starts the URL
# fragment, so the pb parameter is never sent. The name is only a hint next to
# the place's hex id, so these characters are replaced by a space.
_NAME_BREAKERS = re.compile(r"[!#*]+")


def place_request_name(name: Optional[str]) -> str:
    """Return ``name`` made safe for the place request's ``pb`` and ``q`` values."""
    if not isinstance(name, str) or not name:
        return ""
    if not _NAME_BREAKERS.search(name):
        return name
    return re.sub(r"\s{2,}", " ", _NAME_BREAKERS.sub(" ", name)).strip()


def build_place_pb(hex_id: str, name: str, lat: float, lng: float, ftid: Optional[str] = None) -> str:
    """Build the ``pb`` value of a place request.

    ``hex_id`` (``0x...:0x...``) and ``ftid`` (``/g/...``) are written raw; the
    name uses ``+`` for spaces. The first message counts its elements: 14
    without the ftid sub-message (``!15m2!1m1!4s<ftid>`` adds 3), 17 with it.
    The string is the one a browser sends. Its answer also carries other
    place data; this library reads only the opening hours from it.
    """
    name_plus = place_request_name(name).replace(" ", "+")
    ftid_part = f"!15m2!1m1!4s{ftid}" if ftid else ""
    first_count = 17 if ftid_part else 14
    return (
        f"!1m{first_count}"
        f"!1s{hex_id or ''}"
        f"!2s{name_plus}"
        f"!3m8!1m3!1d3022.7!2d{fmt_coord(lng)}!3d{fmt_coord(lat)}!3m2!1i1024!2i768!4f13.1"
        f"!4m2!3d{fmt_coord(lat)}!4d{fmt_coord(lng)}"
        f"{ftid_part}"
        "!12m4!2m3!1i360!2i120!4i8"
        "!13m57!2m2!1i203!2i100!3m2!2i4!5b1"
        "!6m6!1m2!1i86!2i86!1m2!1i408!2i240"
        "!7m33!1m3!1e1!2b0!3e3!1m3!1e2!2b1!3e2!1m3!1e2!2b0!3e3!1m3!1e8!2b0!3e3"
        "!1m3!1e10!2b0!3e3!1m3!1e10!2b1!3e2!1m3!1e10!2b0!3e4!1m3!1e9!2b1!3e2!2b1!9b0"
        "!15m8!1m7!1m2!1m1!1e2!2m2!1i195!2i195!3i20"
        "!15m108!1m29!13m9!2b1!3b1!4b1!6i1!8b1!9b1!14b1!20b1!25b1"
        "!18m18!3b1!4b1!5b1!6b1!13b1!14b1!17b1!21b1!22b1!27m1!1b0!28b0!30b1!32b1!33m1!1b1!34b1!36e2"
        "!10m1!8e3!11m1!3e1!14m1!3b0!17b1!20m2!1e3!1e6!24b1!25b1!26b1!27b1!29b1!30m1!2b1!36b1!37b1"
        "!39m3!2m2!2i1!3i1!43b1!52b1!54m1!1b1!55b1!56m1!1b1!61m2!1m1!1e1!65m5!3m4!1m3!1m2!1i224!2i298"
        "!72m22!1m8!2b1!5b1!7b1!12m4!1b1!2b1!4m1!1e1!4b1!8m10!1m6!4m1!1e1!4m1!1e3!4m1!1e4"
        "!3sother_user_google_review_posts__and__hotel_and_vr_partner_review_posts!6m1!1e1!9b1"
        "!89b1!98m3!1b1!2b1!3b1!103b1!113b1!114m3!1b1!2m1!1b1!117b1!122m1!1b1!126b1!127b1"
        "!21m0!22m1!1e81"
        "!30m8!3b1!6m2!1b1!2b1!7m2!1e3!2b1!9b1"
        "!34m5!7b1!10b1!14b1!15m1!1b0"
        "!37i763"
    )


def build_place_url(hex_id: str, name: str, lat: float, lng: float, ftid: Optional[str] = None) -> str:
    """Build the URL of one place request."""
    pb = quote(build_place_pb(hex_id, name, lat, lng, ftid), safe="")
    name_plus = quote(place_request_name(name).replace(" ", "+"), safe="+")
    return f"{PLACE_ENDPOINT}?authuser=0&hl=en&gl=us&q={name_plus}&pb={pb}"


# Printable ASCII is kept as is (so ASCII names give the same Referer a browser
# sends); anything else is percent-encoded as UTF-8, because HTTP header values
# must be ASCII.
_REFERER_SAFE = "".join(chr(c) for c in range(0x21, 0x7F))


def place_referer(name: Optional[str]) -> str:
    """Return the ``Referer`` header for a place request."""
    name_plus = place_request_name(name).replace(" ", "+")
    return "https://www.google.com/maps/place/" + quote(name_plus, safe=_REFERER_SAFE) + "/"


def google_maps_url(name: Optional[str], place_id: Optional[str]) -> Optional[str]:
    """Return a public Google Maps link for a place (Maps URLs format)."""
    if not place_id:
        return None
    query = quote(name or "place")
    return f"https://www.google.com/maps/search/?api=1&query={query}&query_place_id={quote(place_id)}"
