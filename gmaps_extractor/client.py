"""Plain HTTP client for Google Maps search pages and place opening hours."""

import base64
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx

from .exceptions import ConfigurationError, RateLimitError, RequestError
from .parsers import extract_businesses, extract_hours, parse_google_response
from .urls import build_place_url, build_search_url, place_referer

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
RETRY_WAIT_SECONDS = 2.0

PROXY_URL_ENV = "GMAPS_PROXY_URL"
PROXY_HOST_ENV = "GMAPS_PROXY_HOST"
PROXY_USER_ENV = "GMAPS_PROXY_USER"
PROXY_PASS_ENV = "GMAPS_PROXY_PASS"


def resolve_proxy(proxy: Optional[str] = None) -> Optional[str]:
    """Return the proxy URL to use, or ``None`` for a direct connection.

    The proxy comes only from the ``proxy`` argument or from environment
    variables, in this order:

    1. ``proxy`` (e.g. ``"http://user:pass@host:port"``)
    2. ``GMAPS_PROXY_URL``
    3. ``GMAPS_PROXY_HOST`` (``host:port``) with optional ``GMAPS_PROXY_USER``
       and ``GMAPS_PROXY_PASS``
    """
    if proxy:
        return proxy
    url = os.environ.get(PROXY_URL_ENV, "").strip()
    if url:
        return url
    host = os.environ.get(PROXY_HOST_ENV, "").strip()
    if not host:
        return None
    if "://" in host:
        raise ConfigurationError(f"{PROXY_HOST_ENV} must be host:port; put a full URL in {PROXY_URL_ENV}")
    user = os.environ.get(PROXY_USER_ENV, "")
    password = os.environ.get(PROXY_PASS_ENV, "")
    if user and password:
        return f"http://{quote(user, safe='')}:{quote(password, safe='')}@{host}"
    if user or password:
        raise ConfigurationError(f"set both {PROXY_USER_ENV} and {PROXY_PASS_ENV}, or neither")
    return f"http://{host}"


def _varint(value: int) -> bytes:
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def generate_consent_cookie(language: str = "en", now: Optional[float] = None) -> str:
    """Build a ``SOCS`` consent cookie value, as set by Google's consent page.

    Without it, visitors from some regions are redirected to
    ``consent.google.com`` instead of getting data. The value holds only a
    consent-page version string, a language and a timestamp.
    """
    stamp = now if now is not None else time.time()
    version = f"boq_identityfrontenduiserver_{datetime.fromtimestamp(stamp, timezone.utc):%Y%m%d}.09_p0".encode()
    lang = language.encode()
    inner = b"\x08\x04\x12" + bytes([len(version)]) + version + b"\x1a" + bytes([len(lang)]) + lang + b"\x20\x02"
    timestamp = b"\x08" + _varint(int(stamp))
    outer = b"\x08\x02\x12" + bytes([len(inner)]) + inner + b"\x1a" + bytes([len(timestamp)]) + timestamp
    return base64.b64encode(outer).decode().rstrip("=")


class GMapsClient:
    """Fetch Google Maps search pages and place opening hours over plain HTTP.

    Args:
        proxy: Optional proxy URL, passed straight to httpx. When omitted, the
            ``GMAPS_PROXY_*`` environment variables are read (see
            :func:`resolve_proxy`).
        timeout: Seconds per HTTP request.
        min_interval: Minimum seconds between two requests of this client
            (shared by all threads that use it).
        user_agent: ``User-Agent`` header to send.
        transport: Optional httpx transport (used by the tests to serve
            recorded answers instead of the network).

    A failed request (network error, HTTP error, consent page or an empty
    answer) is retried once after a short fixed wait; if the retry fails too,
    :class:`~gmaps_extractor.exceptions.RequestError` is raised.
    """

    def __init__(
        self,
        proxy: Optional[str] = None,
        timeout: float = 30.0,
        min_interval: float = 0.5,
        user_agent: str = DEFAULT_USER_AGENT,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self._proxy = resolve_proxy(proxy)
        self._user_agent = user_agent
        self._min_interval = max(0.0, float(min_interval))
        self._lock = threading.Lock()
        self._next_request_at = 0.0
        self._session_lock = threading.Lock()
        self._session_ready = False
        kwargs: Dict[str, Any] = {"timeout": timeout, "follow_redirects": True}
        if self._proxy:
            kwargs["proxy"] = self._proxy
        if transport is not None:
            kwargs["transport"] = transport
        self._http = httpx.Client(**kwargs)
        self._set_consent_cookie()

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "GMapsClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- public API --------------------------------------------------------

    def search_page(
        self,
        query: str,
        lat: float,
        lng: float,
        offset: int = 0,
        results_count: int = 20,
        max_radius: int = 5000,
        viewport_dist: float = 10000,
    ) -> List[Dict[str, Any]]:
        """Return the places of one search page around ``(lat, lng)``.

        Each dict has ``name``, ``address``, ``phone``, ``website``,
        ``rating``, ``reviews_count``, ``category``, ``categories``,
        ``latitude``, ``longitude``, ``place_id`` and the two request
        identifiers ``hex_id`` and ``ftid`` used by :meth:`place_hours`.
        """
        url = build_search_url(query, lat, lng, results_count, max_radius, viewport_dist, offset)
        data = self._get_json(url, self._headers("https://www.google.com/"))
        return extract_businesses(data)

    def place_hours(
        self,
        hex_id: str,
        name: Optional[str] = None,
        lat: float = 0.0,
        lng: float = 0.0,
        ftid: Optional[str] = None,
    ) -> Optional[Dict[str, str]]:
        """Return the opening hours of one place, or ``None`` if Google lists none.

        ``hex_id`` (and optionally ``ftid``, ``name`` and the coordinates) come
        from :meth:`search_page`.
        """
        if not hex_id:
            raise ValueError("hex_id is required")
        self._ensure_session()
        url = build_place_url(hex_id, name or "", lat, lng, ftid)
        headers = self._headers(place_referer(name), origin=True)
        return extract_hours(self._get_json(url, headers))

    # -- internals -----------------------------------------------------------

    def _headers(self, referer: str, origin: bool = False) -> Dict[str, str]:
        headers = {
            "User-Agent": self._user_agent,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": referer,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
        if origin:
            headers["Origin"] = "https://www.google.com"
        return headers

    def _set_consent_cookie(self) -> None:
        self._http.cookies.set("SOCS", generate_consent_cookie(), domain=".google.com")

    def _ensure_session(self, refresh: bool = False) -> None:
        """Open google.com/maps once so Google sets its session cookies."""
        with self._session_lock:
            if self._session_ready and not refresh:
                return
            if refresh:
                self._http.cookies.clear()
                self._set_consent_cookie()
            headers = {
                "User-Agent": self._user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
            try:
                self._wait_turn()
                self._http.get("https://www.google.com/maps?hl=en", headers=headers)
            except httpx.HTTPError as exc:
                logger.debug("session page failed: %s", type(exc).__name__)
            self._session_ready = True

    def _wait_turn(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._next_request_at - now
            self._next_request_at = max(now, self._next_request_at) + self._min_interval
        if wait > 0:
            time.sleep(wait)

    def _get_json(self, url: str, headers: Dict[str, str]) -> Any:
        problem = ""
        for attempt in range(2):
            if attempt:
                time.sleep(RETRY_WAIT_SECONDS)
                if self._session_ready:
                    self._ensure_session(refresh=True)
            self._wait_turn()
            try:
                response = self._http.get(url, headers=headers)
            except httpx.HTTPError as exc:
                problem = f"network error ({type(exc).__name__})"
                continue
            problem = _response_problem(response)
            if not problem:
                return parse_google_response(response.text)
            logger.debug("request failed: %s", problem)
        if problem.startswith("HTTP 429"):
            raise RateLimitError(f"Google Maps request failed after one retry: {problem}")
        raise RequestError(f"Google Maps request failed after one retry: {problem}")


def _response_problem(response: httpx.Response) -> str:
    """Return why ``response`` is unusable, or ``""`` when it looks fine."""
    if response.status_code != 200:
        return f"HTTP {response.status_code}"
    if response.url.host == "consent.google.com":
        return "consent page"
    try:
        body = response.text.strip()
    except Exception:  # undecodable body (bad compression or charset)
        return "undecodable body"
    if body.startswith(")]}'"):
        body = body[4:].strip()
    if len(body) < 100:
        return "empty answer"
    return ""
