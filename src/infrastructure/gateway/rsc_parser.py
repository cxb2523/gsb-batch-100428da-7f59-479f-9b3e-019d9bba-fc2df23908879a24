#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Pure parsing of Next.js RSC payloads (no browser, no network).

The RSC response contains the Markdown for every wiki page plus a ``page_plan``
metadata array.  This module turns that raw text into ``ContentPage`` models so
the fetching adapter and the tests share exactly the same logic.
"""

import html
import json
import re
from typing import Dict, List, Optional, Sequence

from src.domain.models.wiki_page import ContentPage, WikiContent


_SCRIPT_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')
_PAGE_PLAN_RE = re.compile(
    r'page_plan\\?"\s*:\s*\{\\?"id\\?"\s*:\s*\\?"(?P<id>.+?)\\?"\s*,'
    r'\s*\\?"title\\?"\s*:\s*\\?"(?P<title>.+?)\\?"'
)
_PAGES_ARRAY_RE = re.compile(r'\\?"pages\\?"\s*:\s*\[(?P<body>.*?)\]', re.DOTALL)
_GENERIC_PAGE_RE = re.compile(
    r'\\?"id\\?"\s*:\s*\\?"(?P<id>\d+(?:\.\d+)*)\\?"\s*,\s*'
    r'\\?"title\\?"\s*:\s*\\?"(?P<title>(?:[^"\\]|\\.)+?)\\?"'
)
_HEADING_RE = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)
_JSON_ESCAPE_RE = re.compile(r"\\(u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|[nrt\"\\/])")
_SIMPLE_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\", "/": "/"}


def _unescape_rsc_fragment(content: str) -> str:
    """Decode a JSON string fragment that could not be parsed as JSON."""

    def replace(match: "re.Match[str]") -> str:
        token = match.group(1)
        if token[0] in "ux":
            return chr(int(token[1:], 16))
        return _SIMPLE_ESCAPES.get(token, match.group(0))

    return _JSON_ESCAPE_RE.sub(replace, content)


def decode_rsc_text(content: str) -> str:
    """Decode JSON/unicode/HTML escaping used inside RSC payloads."""
    if not content:
        return ""

    try:
        decoded = json.loads(f'"{content}"')
    except (json.JSONDecodeError, ValueError):
        # Manual unescaping avoids ``unicode_escape``, which would corrupt
        # non-ASCII (e.g. Chinese) titles.
        decoded = _unescape_rsc_fragment(content)

    return html.unescape(decoded)


def _looks_like_markdown(text: str) -> bool:
    stripped = text.lstrip()
    if not stripped:
        return False
    # ``self.__next_f.push`` also carries flight/JSON rows; those never start
    # like a Markdown document and must not be mistaken for page content.
    if stripped.startswith(("{", "[", "<")):
        return False
    # DeepWiki pushes each page's Markdown as a document whose first content
    # line is the page title heading (optionally preceded by YAML front
    # matter).  Requiring that shape keeps JSON noise out.
    if stripped.startswith("---"):
        return bool(_HEADING_RE.search(stripped))
    first_line = stripped.split("\n", 1)[0]
    return bool(re.match(r"^\s{0,3}#{1,6}\s+\S", first_line))


def extract_markdown_blobs(rsc_response: str) -> List[str]:
    """Return every Markdown document embedded in the RSC payload.

    DeepWiki pushes whole Markdown documents as ``self.__next_f.push`` chunks.
    Other pushes carry flight/JSON payloads and are ignored so they cannot be
    mistaken for page content.
    """
    blobs = []
    for match in _SCRIPT_PUSH_RE.finditer(rsc_response or ""):
        decoded = decode_rsc_text(match.group(1))
        if _looks_like_markdown(decoded):
            blobs.append(decoded)
    return blobs


def _normalise_metadata(entries: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    seen = set()
    unique = []
    for entry in entries:
        page_id = (entry.get("id") or "").strip()
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        key = (page_id, title.casefold())
        if key in seen:
            continue
        seen.add(key)
        unique.append(
            {
                "id": page_id,
                "title": title,
                "url": (entry.get("url") or "").strip(),
            }
        )
    return unique


def extract_page_metadata(rsc_response: str) -> List[Dict[str, str]]:
    """Extract ``page_plan`` metadata (id/title/optional url) from RSC text."""
    response = rsc_response or ""
    entries: List[Dict[str, str]] = []

    for match in _PAGE_PLAN_RE.finditer(response):
        entries.append(
            {
                "id": decode_rsc_text(match.group("id")),
                "title": decode_rsc_text(match.group("title")),
            }
        )

    if not entries:
        # Fallback: look for numeric id/title pairs.  Prefer the ``pages`` array
        # when it can be located, but retry on the whole payload (the regex
        # above is non-greedy and may stop at a nested array).
        array_match = _PAGES_ARRAY_RE.search(response)
        scopes = [array_match.group("body")] if array_match else []
        scopes.append(response)
        for scope in scopes:
            for match in _GENERIC_PAGE_RE.finditer(scope):
                entries.append(
                    {
                        "id": decode_rsc_text(match.group("id")),
                        "title": decode_rsc_text(match.group("title")),
                    }
                )
            if entries:
                break

    return _normalise_metadata(entries)


def _heading_title(markdown: str) -> str:
    match = _HEADING_RE.search(markdown or "")
    return match.group(1).strip() if match else ""


def pair_content_with_metadata(
    markdown_blobs: Sequence[str],
    metadata: Sequence[Dict[str, str]],
) -> List[ContentPage]:
    """Associate Markdown blobs with page metadata.

    Positional alignment is used only when the counts agree; otherwise blobs
    are matched by their leading Markdown heading.  Unmatched blobs keep a
    heading-derived title and an empty id so the matcher can still try URLs.
    """
    blobs = [blob for blob in markdown_blobs if blob and blob.strip()]
    pages: List[ContentPage] = []
    unclaimed = list(range(len(metadata)))

    positional = len(blobs) == len(metadata)
    for index, blob in enumerate(blobs):
        heading = _heading_title(blob)
        meta: Optional[Dict[str, str]] = None

        if positional:
            meta = metadata[index]
        elif heading:
            heading_key = heading.casefold()
            for position in unclaimed:
                if metadata[position]["title"].casefold() == heading_key:
                    meta = metadata[position]
                    unclaimed.remove(position)
                    break

        if meta is None:
            pages.append(
                ContentPage(
                    page_id="",
                    title=heading or f"Page {index + 1}",
                    content=blob,
                )
            )
            continue

        pages.append(
            ContentPage(
                page_id=meta["id"],
                title=meta["title"],
                content=blob,
                url=meta.get("url", ""),
            )
        )

    return pages


def parse_rsc_response(rsc_response: str) -> WikiContent:
    """Parse a raw RSC payload into content pages (pure, no network)."""
    return WikiContent(
        content_pages=pair_content_with_metadata(
            extract_markdown_blobs(rsc_response),
            extract_page_metadata(rsc_response),
        )
    )
