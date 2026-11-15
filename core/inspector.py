"""
JS Intel pass — runs on app-tagged files only.

Surfaces:
- Dangerous sinks (eval, innerHTML, document.write, etc.)
- Client-side auth/role gates (isAdmin, hasRole, etc.)
- Debug / feature flags
- postMessage / addEventListener("message") without an origin check anywhere in the file
- Endpoint enrichment (verb, auth_context, state_changing)

All matches are pure-regex against pre-beautified JS. No AST, no LLM.
"""

import re
from pathlib import Path


SINK_PATTERNS = [
    ("eval",                    re.compile(r"\beval\s*\(")),
    ("new Function",            re.compile(r"\bnew\s+Function\s*\(")),
    ("document.write",          re.compile(r"\bdocument\.write\s*\(")),
    ("innerHTML=",              re.compile(r"\.innerHTML\s*=")),
    ("outerHTML=",              re.compile(r"\.outerHTML\s*=")),
    ("dangerouslySetInnerHTML", re.compile(r"dangerouslySetInnerHTML")),
    ("setTimeout(string)",      re.compile(r"setTimeout\s*\(\s*['\"]")),
    ("setInterval(string)",     re.compile(r"setInterval\s*\(\s*['\"]")),
    ("insertAdjacentHTML",      re.compile(r"\.insertAdjacentHTML\s*\(")),
]

GATE_PATTERNS = [
    re.compile(r"\bisAdmin\b"),
    re.compile(r"\bhasRole\s*\("),
    re.compile(r"\bcheckPermission\s*\("),
    re.compile(r"\.role\s*===?\s*['\"][^'\"]+['\"]"),
    re.compile(r"if\s*\(\s*user\.[a-zA-Z_]+\s*\)"),
    re.compile(r"localStorage\.getItem\s*\(\s*['\"]user"),
]

FLAG_PATTERNS = [
    re.compile(r"\bDEBUG\s*[:=]\s*(?:true|1)\b"),
    re.compile(r"\bisDev\b\s*[:=]\s*true\b"),
    re.compile(r"\bfeatureFlags?\b"),
    re.compile(r"\b__DEV__\b"),
    re.compile(r"\?debug="),
]

POSTMESSAGE_RE = re.compile(r"\.postMessage\s*\(")
MESSAGE_LISTENER_RE = re.compile(r"addEventListener\s*\(\s*['\"]message['\"]")
ORIGIN_RE = re.compile(r"event\.origin|e\.origin|\.origin\s*===?")

VERB_FETCH_OPTS_RE = re.compile(
    r"method\s*:\s*['\"](GET|POST|PUT|DELETE|PATCH)['\"]", re.IGNORECASE
)
VERB_HTTP_METHOD_RE = re.compile(
    r"\.(get|post|put|delete|patch)\s*\(", re.IGNORECASE
)

AUTH_CONTEXT_RE = re.compile(
    r"Authorization|Bearer|\btoken\b|getToken\s*\(", re.IGNORECASE
)

STATE_CHANGING = {"POST", "PUT", "DELETE", "PATCH"}


def _read(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _line_of(text, idx):
    return text.count("\n", 0, idx) + 1


def _snippet(text, idx, span=80):
    start = max(0, idx - span // 2)
    end = min(len(text), idx + span // 2)
    return text[start:end].replace("\n", " ").strip()


def _scan_file_patterns(filename, text, named_patterns):
    out = []
    for label, regex in named_patterns:
        for m in regex.finditer(text):
            out.append({
                "file": filename,
                "line": _line_of(text, m.start()),
                "snippet": _snippet(text, m.start()),
                "label": label,
            })
    return out


def _scan_file_anonymous(filename, text, regexes):
    seen = set()
    out = []
    for regex in regexes:
        for m in regex.finditer(text):
            line = _line_of(text, m.start())
            key = (filename, line)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "file": filename,
                "line": line,
                "snippet": _snippet(text, m.start()),
            })
    return out


def _detect_verb(text, hit_idx, span=120):
    start = max(0, hit_idx - span)
    end = min(len(text), hit_idx + span)
    window = text[start:end]
    m = VERB_FETCH_OPTS_RE.search(window)
    if m:
        return m.group(1).upper()
    m = VERB_HTTP_METHOD_RE.search(window)
    if m:
        return m.group(1).upper()
    return None


def _detect_auth(text, hit_idx, span=200):
    start = max(0, hit_idx - span)
    end = min(len(text), hit_idx + span)
    return "likely_authed" if AUTH_CONTEXT_RE.search(text[start:end]) else "unknown"


def inspect_app_files(js_files: list, endpoints: list) -> dict:
    """Run intel pass on app-tagged files only.

    Returns dict with: app_files, sinks, gates, flags, postmessage_no_origin,
    endpoints_enriched.
    """
    app_files = [f for f in js_files if f.get("category") == "app"]

    sinks, gates, flags, pm_no_origin = [], [], [], []
    text_by_filename = {}

    for f in app_files:
        text = _read(f.get("filepath", ""))
        if not text:
            continue
        text_by_filename[f["filename"]] = text
        sinks.extend(_scan_file_patterns(f["filename"], text, SINK_PATTERNS))
        gates.extend(_scan_file_anonymous(f["filename"], text, GATE_PATTERNS))
        flags.extend(_scan_file_anonymous(f["filename"], text, FLAG_PATTERNS))

        has_postmessage = bool(POSTMESSAGE_RE.search(text))
        has_message_listener = bool(MESSAGE_LISTENER_RE.search(text))
        has_origin = bool(ORIGIN_RE.search(text))

        if (has_postmessage or has_message_listener) and not has_origin:
            note_parts = []
            if has_postmessage:
                note_parts.append("postMessage call")
            if has_message_listener:
                note_parts.append('addEventListener("message")')
            pm_no_origin.append({
                "file": f["filename"],
                "note": f"{' + '.join(note_parts)} present, no event.origin check anywhere in file",
            })

    enriched = []
    app_filenames = {f["filename"] for f in app_files}
    for ep in endpoints:
        if ep.get("file") not in app_filenames:
            continue
        text = text_by_filename.get(ep["file"], "")
        url = ep.get("url", "")
        idx = text.find(url) if url else -1
        if idx < 0 and "/" in url:
            idx = text.find(url.rstrip("/"))
        verb = _detect_verb(text, idx) if idx >= 0 else None
        auth = _detect_auth(text, idx) if idx >= 0 else "unknown"
        enriched.append({
            "url": url,
            "file": ep["file"],
            "verb": verb,
            "auth_context": auth,
            "state_changing": verb in STATE_CHANGING if verb else False,
        })

    return {
        "app_files": [
            {"filename": f["filename"], "size": f.get("size", 0), "url": f.get("url", "")}
            for f in app_files
        ],
        "sinks": sinks,
        "gates": gates,
        "flags": flags,
        "postmessage_no_origin": pm_no_origin,
        "endpoints_enriched": enriched,
    }
