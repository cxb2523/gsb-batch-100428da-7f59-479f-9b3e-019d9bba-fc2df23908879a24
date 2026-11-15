"""
Classify JS files as vendor / app / inline.

Vendor = bundled third-party library code (Angular, MSAL, PrimeNG, etc.)
App    = first-party application code worth deeper inspection
Inline = scripts inlined into HTML (already named inline_*)

Decisions are filename-based, deterministic, and cheap. No content scanning.
"""

import re

VENDOR_FILENAME_PREFIXES = (
    "polyfills",
    "runtime",
    "framework",
    "webpack",
    "react-dom",
    "vendor",
    "chunk",
    "main-app",
    "pages-",
    "_app-",
    "_buildManifest",
    "_ssgManifest",
)

VENDOR_NAME_RE = re.compile(
    r"^("
    r"es-module-shims|core-js|regenerator|lodash|moment|jquery|"
    r"swiper|primeng|primeicons|angular|react|vue|ember|rxjs|zone\.?js|"
    r"tslib|axios|d3|three|chart|highcharts|leaflet|mapbox"
    r")(?:[-_.@]|$)",
    re.IGNORECASE,
)

NODE_MODULES_RE = re.compile(r"node_modules_", re.IGNORECASE)

HASH_CHUNK_RE = re.compile(r"^\d{3,5}[-.][0-9a-f]{8,}\.js$", re.IGNORECASE)
PURE_HASH_RE = re.compile(r"^[0-9a-f]{8,}\.js$", re.IGNORECASE)
HEX_HEX_CHUNK_RE = re.compile(r"^[0-9a-f]{6,}[-.][0-9a-f]{6,}\.js$", re.IGNORECASE)

INLINE_RE = re.compile(r"^inline_", re.IGNORECASE)


def classify_js_file(js_file: dict) -> str:
    """Return 'vendor', 'app', or 'inline'."""
    name = js_file.get("filename", "")

    if INLINE_RE.match(name):
        return "inline"

    lower = name.lower()
    for prefix in VENDOR_FILENAME_PREFIXES:
        if lower.startswith(prefix):
            return "vendor"

    if NODE_MODULES_RE.search(name):
        return "vendor"

    if VENDOR_NAME_RE.search(name):
        return "vendor"

    if HASH_CHUNK_RE.match(name) or PURE_HASH_RE.match(name) or HEX_HEX_CHUNK_RE.match(name):
        return "vendor"

    return "app"


def classify_all(js_files: list) -> dict:
    """Tag every js_file dict with 'category' in place; return grouped dict."""
    buckets = {"vendor": [], "app": [], "inline": []}
    for f in js_files:
        cat = classify_js_file(f)
        f["category"] = cat
        buckets[cat].append(f)
    return buckets
