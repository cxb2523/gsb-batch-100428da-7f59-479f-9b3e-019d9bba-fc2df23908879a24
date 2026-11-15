#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Domain service that pairs navigation links with RSC content pages.

Matching is deliberately layered:

1. stable identifiers (page id / URL path) are tried first for every page,
2. only the pages left over fall back to title comparison.

Each content page can be claimed by at most one navigation page.  Pages that
cannot be matched are reported instead of being written with borrowed or empty
content.
"""

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import unquote, urlsplit

from src.domain.models.wiki_page import (
    ContentPage,
    NavigationPage,
    PageOutcome,
    WikiContent,
)


_WHITESPACE_RE = re.compile(r"\s+")
_NUMBER_PREFIX_RE = re.compile(r"^\s*\d+(?:\.\d+)*[.)\-:\s]+")
_ID_SEGMENT_RE = re.compile(r"^(\d+(?:\.\d+)*)(?:[-_.\s].*)?$")
_TITLE_NOISE_RE = re.compile(r"[^\w\s]", re.UNICODE)
_MD_SUFFIX_RE = re.compile(r"\.md$", re.IGNORECASE)


def normalize_title(title: str) -> str:
    """Case/whitespace/underscore insensitive view of a title."""
    if not title:
        return ""
    return _WHITESPACE_RE.sub(" ", title.replace("_", " ").strip()).casefold()


def title_key(title: str) -> str:
    """Stronger title key used by the fallback matcher.

    Removes a leading hierarchy number (``2.3``, ``1.`` ...) and punctuation so
    that "2.3 Frontend, architecture!" and "Frontend architecture" compare
    equal, while still avoiding loose substring matching.
    """
    normalized = normalize_title(title)
    normalized = _NUMBER_PREFIX_RE.sub("", normalized)
    normalized = _TITLE_NOISE_RE.sub(" ", normalized)
    return _WHITESPACE_RE.sub(" ", normalized).strip()


def normalize_page_id(page_id: str) -> str:
    """Normalise ``02.1`` to ``2.1`` so equal ids compare equal."""
    if not page_id:
        return ""
    parts = str(page_id).strip().split(".")
    normalised = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        try:
            normalised.append(str(int(part)))
        except ValueError:
            return str(page_id).strip().casefold()
    return ".".join(normalised)


def page_id_from_url(url_or_path: str) -> Optional[str]:
    """Extract the hierarchy id from a DeepWiki page URL/path.

    ``https://deepwiki.com/org/repo/2.3-architecture`` -> ``2.3``.
    Returns ``None`` for the wiki root or when the trailing segment does not
    start with a hierarchy number.

    DeepWiki links may also be flat (``/org/repo/architecture``) or include a
    locale/extra prefix; in those cases the *last* segment is inspected, so a
    non-numeric trailing segment simply yields ``None`` and the caller falls
    back to title/URL matching.
    """
    if not url_or_path:
        return None

    path = urlsplit(url_or_path).path

    segments = [unquote(segment) for segment in path.split("/") if segment]
    # org/repo/page.md -> need at least the wiki name and one page segment.
    if len(segments) < 3:
        return None

    last = _MD_SUFFIX_RE.sub("", segments[-1])
    match = _ID_SEGMENT_RE.match(last)
    if not match:
        return None
    return normalize_page_id(match.group(1))


def canonical_url(url: str) -> str:
    """Stable URL key: scheme-less, query/fragment-less, no trailing slash."""
    if not url:
        return ""
    split = urlsplit(url)
    path = unquote(split.path).rstrip("/")
    if split.netloc:
        return f"{split.netloc.casefold()}{path}"
    return path


@dataclass(frozen=True)
class PageMatch:
    """One navigation page and the content page (if any) it owns."""

    navigation: NavigationPage
    content: Optional[ContentPage]
    status: PageOutcome
    reason: str = ""
    match_kind: str = ""

    @property
    def page_id(self) -> Optional[str]:
        if self.content is not None and self.content.page_id:
            return self.content.page_id
        return page_id_from_url(self.navigation.url) or page_id_from_url(
            self.navigation.path
        )


class PageMatcher:
    """Match navigation links to extracted content pages."""

    def match(self, wiki_content: WikiContent) -> List[PageMatch]:
        navigation_pages = self._deduplicate_navigation(wiki_content.navigation_pages)
        content_pages = self._deduplicate_content(wiki_content.content_pages)

        if not navigation_pages:
            return []

        usable_indexes = [
            index
            for index, page in enumerate(content_pages)
            if page is not None and page.content and page.content.strip()
        ]
        claimed: set[int] = set()
        matches: Dict[int, PageMatch] = {}
        pending: List[Tuple[int, NavigationPage]] = []

        # Pass 1: stable identifiers only.  Running this for every page before
        # any title fallback prevents a title match from stealing a content
        # page that another navigation entry can identify by id/URL.
        for position, navigation in enumerate(navigation_pages):
            content_index, kind = self._stable_match(
                navigation, content_pages, usable_indexes, claimed
            )
            if content_index is None:
                pending.append((position, navigation))
                continue
            claimed.add(content_index)
            matches[position] = PageMatch(
                navigation=navigation,
                content=content_pages[content_index],
                status=PageOutcome.SAVED,
                match_kind=kind,
            )

        # Pass 2: title-based fallback for whatever is left.
        for position, navigation in pending:
            content_index, kind, reason = self._fallback_match(
                navigation, content_pages, usable_indexes, claimed
            )
            if content_index is None:
                matches[position] = self._failure(
                    navigation,
                    content_pages,
                    usable_indexes,
                    claimed,
                    reason,
                )
                continue
            claimed.add(content_index)
            matches[position] = PageMatch(
                navigation=navigation,
                content=content_pages[content_index],
                status=PageOutcome.SAVED,
                match_kind=kind,
            )

        return [matches[position] for position in range(len(navigation_pages))]

    # ------------------------------------------------------------------
    # Stable identifier strategies
    # ------------------------------------------------------------------
    def _stable_match(
        self,
        navigation: NavigationPage,
        content_pages: Sequence[ContentPage],
        usable_indexes: Sequence[int],
        claimed: set[int],
    ) -> Tuple[Optional[int], str]:
        navigation_id = page_id_from_url(navigation.url) or page_id_from_url(
            navigation.path
        )

        # 1. Page hierarchy id.
        if navigation_id:
            candidates = [
                index
                for index in usable_indexes
                if index not in claimed
                and normalize_page_id(content_pages[index].page_id) == navigation_id
            ]
            if len(candidates) == 1:
                return candidates[0], "page_id"
            if len(candidates) > 1:
                # Duplicate ids: disambiguate by title, never guess.
                exact = [
                    index
                    for index in candidates
                    if normalize_title(content_pages[index].title)
                    == normalize_title(navigation.title)
                ]
                if len(exact) == 1:
                    return exact[0], "page_id"
                return None, ""

        # 2. Full URL/path equality (only possible when the RSC page carries
        #    its own URL).
        nav_canonical = canonical_url(navigation.url)
        if nav_canonical:
            candidates = [
                index
                for index in usable_indexes
                if index not in claimed
                and content_pages[index].url
                and canonical_url(content_pages[index].url) == nav_canonical
            ]
            if len(candidates) == 1:
                return candidates[0], "url"

        # 3. Trailing path segment equality, e.g. both end in
        #    ``2.3-architecture``.
        navigation_segment = self._last_path_segment(navigation.url)
        if navigation_segment:
            candidates = [
                index
                for index in usable_indexes
                if index not in claimed
                and content_pages[index].url
                and self._last_path_segment(content_pages[index].url)
                == navigation_segment
            ]
            if len(candidates) == 1:
                return candidates[0], "url"

        return None, ""

    @staticmethod
    def _last_path_segment(url: str) -> str:
        path = urlsplit(url).path if "://" in url else url
        segments = [unquote(segment) for segment in path.split("/") if segment]
        if not segments:
            return ""
        return _MD_SUFFIX_RE.sub("", segments[-1]).casefold()

    # ------------------------------------------------------------------
    # Title fallback
    # ------------------------------------------------------------------
    def _fallback_match(
        self,
        navigation: NavigationPage,
        content_pages: Sequence[ContentPage],
        usable_indexes: Sequence[int],
        claimed: set[int],
    ) -> Tuple[Optional[int], str, str]:
        available = [index for index in usable_indexes if index not in claimed]
        if not available:
            return None, "", ""

        exact_title = normalize_title(navigation.title)
        exact = [
            index
            for index in available
            if normalize_title(content_pages[index].title) == exact_title
        ]
        if len(exact) == 1:
            return exact[0], "title", ""
        if len(exact) > 1:
            return None, "", "ambiguous title match"

        key = title_key(navigation.title)
        if key:
            fuzzy = [
                index
                for index in available
                if title_key(content_pages[index].title) == key
            ]
            if len(fuzzy) == 1:
                return fuzzy[0], "title_fuzzy", ""
            if len(fuzzy) > 1:
                return None, "", "ambiguous title match"

        return None, "", "no unique title match"

    def _failure(
        self,
        navigation: NavigationPage,
        content_pages: Sequence[ContentPage],
        usable_indexes: Sequence[int],
        claimed: set[int],
        reason: str,
    ) -> PageMatch:
        navigation_id = page_id_from_url(navigation.url) or page_id_from_url(
            navigation.path
        )

        if not content_pages:
            status = PageOutcome.CONTENT_MISSING
            reason = "the RSC response contained no page content"
        elif not usable_indexes:
            status = PageOutcome.CONTENT_MISSING
            reason = "the extracted page content was empty"
        elif all(index in claimed for index in usable_indexes):
            status = PageOutcome.CONTENT_MISSING
            reason = "all extracted content is already assigned to other pages"
        else:
            status = PageOutcome.TITLE_MISMATCH
            reason = reason or "no unique title match"
            if navigation_id and not any(
                normalize_page_id(page.page_id) == navigation_id
                for page in content_pages
            ):
                reason = f"{reason} (no content with page id {navigation_id})"

        return PageMatch(
            navigation=navigation,
            content=None,
            status=status,
            reason=reason,
        )

    # ------------------------------------------------------------------
    # Input hygiene
    # ------------------------------------------------------------------
    @staticmethod
    def _deduplicate_navigation(
        pages: Sequence[NavigationPage],
    ) -> List[NavigationPage]:
        seen = set()
        unique: List[NavigationPage] = []
        for page in pages:
            key = canonical_url(page.url) or (
                normalize_title(page.title),
                canonical_url(page.path),
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(page)
        return unique

    @staticmethod
    def _deduplicate_content(pages: Sequence[ContentPage]) -> List[ContentPage]:
        """Drop extraction artifacts so one body can never be used twice.

        RSC payloads occasionally repeat a Markdown document.  ``PageMatcher``
        also guarantees that no two navigation pages end up owning the same
        body, which requires duplicate bodies to be dropped up front.  Entries
        whose body is empty are collapsed by ``(id, title)`` instead, since
        they all render as "content missing" anyway.
        """
        seen = set()
        unique: List[ContentPage] = []
        for page in pages:
            body = (page.content or "").strip()
            if body:
                key = ("body", body)
            else:
                key = (
                    "empty",
                    normalize_page_id(page.page_id),
                    normalize_title(page.title),
                )
            if key in seen:
                continue
            seen.add(key)
            unique.append(page)
        return unique
