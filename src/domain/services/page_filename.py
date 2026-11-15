#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Deterministic, filesystem-safe naming for saved wiki pages."""

import re
import unicodedata
from typing import Iterable, Optional

_ILLEGAL_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE_RE = re.compile(r"\s+")
_LEADING_NUMBER_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)[.)\-:\s]+")
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_MAX_STEM_LENGTH = 120


def _normalized_number(value: str) -> str:
    parts = []
    for part in value.split("."):
        try:
            parts.append(str(int(part)))
        except ValueError:
            return value
    return ".".join(parts)


def strip_id_prefix(title: str, page_id: Optional[str]) -> str:
    """Remove a hierarchy number from the title when it repeats the page id.

    Navigation titles often already carry the number (``"2.1 Frontend"``);
    without this the filename would read ``2.1-2.1 Frontend.md``.
    """
    if not title or not page_id:
        return title
    match = _LEADING_NUMBER_RE.match(title)
    if not match:
        return title
    if _normalized_number(match.group(1)) != _normalized_number(page_id.strip()):
        return title
    return title[match.end():].strip()


def sanitize_title(title: str) -> str:
    """Turn a title into a safe filename fragment.

    Slashes and other characters that are illegal (or path separators) on
    Windows are replaced, control characters are dropped, and trailing dots or
    spaces are trimmed.  Unicode letters are preserved so Chinese titles keep
    their names.
    """
    value = unicodedata.normalize("NFC", title or "")
    value = value.replace("/", "-").replace("\\", "-")
    value = _ILLEGAL_CHARS_RE.sub("", value)
    value = _WHITESPACE_RE.sub(" ", value).strip()
    value = value.strip(". ")
    if len(value) > _MAX_STEM_LENGTH:
        value = value[:_MAX_STEM_LENGTH].rstrip(". ")
    if not value or value.upper() in _WINDOWS_RESERVED:
        return ""
    return value


def build_page_filename(
    page_id: Optional[str],
    title: str,
    fallback_number: int,
    used_names: Optional[Iterable[str]] = None,
) -> str:
    """Build a unique ``.md`` filename for one page.

    The stem is ``<id>-<title>`` when a hierarchy id exists, otherwise
    ``<n>-<title>`` with the page's 1-based position.  Uniqueness is enforced
    against ``used_names`` (case-insensitive) by appending ``-2``, ``-3`` ...
    """
    used = {name.casefold() for name in (used_names or ())}
    prefix = sanitize_title((page_id or "").strip()) or str(fallback_number)
    title_fragment = sanitize_title(strip_id_prefix(title, page_id))

    stem = f"{prefix}-{title_fragment}" if title_fragment else prefix
    stem = stem.strip(". ") or f"page-{fallback_number}"

    candidate = f"{stem}.md"
    suffix = 2
    while candidate.casefold() in used:
        candidate = f"{stem}-{suffix}.md"
        suffix += 1
    return candidate


def relative_link(filename: str) -> str:
    """Encode a filename for use inside a Markdown link target."""
    from urllib.parse import quote

    return "./" + quote(filename, safe=".")


def escape_link_text(text: str) -> str:
    """Escape characters that would break the ``[text](target)`` syntax.

    Brackets in a title (common in wiki headings) would otherwise terminate
    the link label early and produce a broken index link.
    """
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )
