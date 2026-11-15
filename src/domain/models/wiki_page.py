#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Domain models describing wiki pages and the outcome of a conversion run."""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional, Sequence


@dataclass(frozen=True)
class NavigationPage:
    """A page link discovered in the wiki navigation.

    ``title`` is what the site displays, while ``url``/``path`` are the stable
    identifiers we can use to match the link against an RSC content page.
    """

    title: str
    url: str
    path: str = ""


@dataclass(frozen=True)
class ContentPage:
    """A Markdown document extracted from an RSC response."""

    page_id: str
    title: str
    content: str
    url: str = ""


@dataclass(frozen=True)
class WikiContent:
    """Everything a single RSC fetch produced."""

    navigation_pages: Sequence[NavigationPage] = ()
    content_pages: Sequence[ContentPage] = ()

    def __post_init__(self) -> None:
        # Freeze the sequences so callers cannot mutate a shared bundle.
        object.__setattr__(self, "navigation_pages", tuple(self.navigation_pages))
        object.__setattr__(self, "content_pages", tuple(self.content_pages))

    @classmethod
    def coerce(cls, value: Any) -> "WikiContent":
        """Normalise whatever a fetch adapter returns into ``WikiContent``.

        Adapters are an injectable boundary, so tests (and older adapter
        versions) may hand back ``WikiContent`` itself, a ``(pages, content)``
        tuple, or plain dicts.  Converting here keeps the usecase simple and
        keeps the adapter contract narrow.
        """
        if isinstance(value, WikiContent):
            return value
        if value is None:
            return cls()

        if isinstance(value, (tuple, list)):
            navigation = value[0] if len(value) > 0 else ()
            content = value[1] if len(value) > 1 else ()
            return cls(
                navigation_pages=_coerce_navigation(navigation),
                content_pages=_coerce_content(content),
            )

        if isinstance(value, dict):
            return cls(
                navigation_pages=_coerce_navigation(value.get("navigation_pages", ())),
                content_pages=_coerce_content(value.get("content_pages", ())),
            )

        raise TypeError(f"cannot interpret {type(value)!r} as WikiContent")


def _coerce_navigation(pages: Any) -> List[NavigationPage]:
    result = []
    for page in pages or ():
        if isinstance(page, NavigationPage):
            result.append(page)
        elif isinstance(page, dict):
            result.append(
                NavigationPage(
                    title=str(page.get("title", "")).strip(),
                    url=str(page.get("url", "")).strip(),
                    path=str(page.get("path", "")).strip(),
                )
            )
        elif isinstance(page, (tuple, list)):
            title = str(page[0]).strip() if len(page) > 0 else ""
            url = str(page[1]).strip() if len(page) > 1 else ""
            path = str(page[2]).strip() if len(page) > 2 else ""
            result.append(NavigationPage(title=title, url=url, path=path))
    return result


def _coerce_content(pages: Any) -> List[ContentPage]:
    result = []
    if isinstance(pages, dict):
        # Legacy shape: {url_or_key: markdown}.  The key is a location, not a
        # page id, so keep it as ``url`` and derive the title from the body.
        converted = []
        for key, value in pages.items():
            if isinstance(value, dict):
                converted.append({"page_id": key, **value})
            else:
                content = "" if value is None else str(value)
                converted.append(
                    {
                        "page_id": "",
                        "title": _first_heading(content),
                        "content": content,
                        "url": str(key) if "://" in str(key) else "",
                    }
                )
        pages = converted
    for page in pages or ():
        if isinstance(page, ContentPage):
            result.append(page)
        elif isinstance(page, dict):
            content = str(page.get("content", "") or "")
            result.append(
                ContentPage(
                    page_id=str(page.get("page_id", page.get("id", "")) or "").strip(),
                    title=str(page.get("title", "") or _first_heading(content)).strip(),
                    content=content,
                    url=str(page.get("url", "") or "").strip(),
                )
            )
        elif isinstance(page, (tuple, list)):
            page_id = str(page[0]).strip() if len(page) > 0 else ""
            title = str(page[1]).strip() if len(page) > 1 else ""
            content = str(page[2]) if len(page) > 2 else ""
            url = str(page[3]).strip() if len(page) > 3 else ""
            result.append(
                ContentPage(page_id=page_id, title=title, content=content, url=url)
            )
    return result


_HEADING_RE = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)


def _first_heading(markdown: str) -> str:
    match = _HEADING_RE.search(markdown or "")
    return match.group(1).strip() if match else ""


class PageOutcome(str, Enum):
    """The explicit end state of one navigation page."""

    SAVED = "saved"
    TITLE_MISMATCH = "title_mismatch"
    CONTENT_MISSING = "content_missing"


@dataclass
class PageResult:
    """The outcome for a single navigation page."""

    title: str
    url: str
    status: PageOutcome
    reason: str = ""
    filename: Optional[str] = None
    page_id: Optional[str] = None

    @property
    def saved(self) -> bool:
        return self.status is PageOutcome.SAVED


@dataclass
class ConversionResult:
    """The settled outcome of a whole conversion run."""

    output_directory: str
    pages: List[PageResult] = field(default_factory=list)
    index_path: Optional[str] = None
    error: Optional[str] = None

    @property
    def saved_pages(self) -> List[PageResult]:
        return [page for page in self.pages if page.status is PageOutcome.SAVED]

    def pages_with_status(self, status: PageOutcome) -> List[PageResult]:
        return [page for page in self.pages if page.status is status]

    def count(self, status: PageOutcome) -> int:
        return len(self.pages_with_status(status))
