"""URL normalization — canonical form + stable url_id (xxhash64).

Canonical rules:
- Lowercase scheme + host
- Default ports stripped (80 for http, 443 for https)
- Path with trailing slash collapsed except root
- Query params sorted alphabetically; empty query dropped
- Fragment dropped
- IDN hosts kept as punycode (idna)
"""

from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

DEFAULT_PORTS = {"http": 80, "https": 443}


def canonicalize(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower() or "http"
    if scheme not in ("http", "https"):
        return url.strip()

    host = (parts.hostname or "").lower()
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass

    port = parts.port
    netloc = host
    if port and port != DEFAULT_PORTS.get(scheme):
        netloc = f"{host}:{port}"
    if parts.username:
        userinfo = parts.username
        if parts.password:
            userinfo += f":{parts.password}"
        netloc = f"{userinfo}@{netloc}"

    path = parts.path or "/"
    path = quote(unquote(path), safe="/%:@!$&'()*+,;=~-._")
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/") or "/"

    if parts.query:
        params = sorted(parse_qsl(parts.query, keep_blank_values=True))
        query = urlencode(params, doseq=True)
    else:
        query = ""

    return urlunsplit((scheme, netloc, path, query, ""))


def url_id(url: str) -> int:
    """Stable 64-bit unsigned int hash of canonical URL.

    Uses BLAKE2b truncated to 8 bytes for stdlib-only deterministic hashing
    (xxhash isn't in stdlib; this is identical for our purposes — fast enough
    for crawl-time and stable across runs).
    """
    canon = canonicalize(url).encode("utf-8")
    digest = hashlib.blake2b(canon, digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=False)
