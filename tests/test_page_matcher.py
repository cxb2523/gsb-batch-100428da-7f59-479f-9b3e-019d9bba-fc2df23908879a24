#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Tests for the layered navigation/content matcher."""

from src.domain.models.wiki_page import (
    ContentPage,
    NavigationPage,
    PageOutcome,
    WikiContent,
)
from src.domain.services.page_matcher import (
    PageMatcher,
    normalize_page_id,
    page_id_from_url,
    title_key,
)


def make_nav(title, url):
    return NavigationPage(title=title, url=url)


def make_content(page_id, title, content="body", url=""):
    return ContentPage(page_id=page_id, title=title, content=content, url=url)


BASE = "https://deepwiki.com/org/repo"


def match(nav_pages, content_pages):
    return PageMatcher().match(
        WikiContent(navigation_pages=nav_pages, content_pages=content_pages)
    )


def test_page_id_parsing_from_url():
    assert page_id_from_url(f"{BASE}/2.3-architecture") == "2.3"
    assert page_id_from_url(f"{BASE}/02.3-architecture") == "2.3"
    assert page_id_from_url(f"{BASE}/2") == "2"
    assert page_id_from_url(f"{BASE}/2.3-architecture.md") == "2.3"
    assert page_id_from_url(BASE) is None
    assert page_id_from_url(f"{BASE}/overview") is None
    assert normalize_page_id("02.10") == "2.10"


def test_page_id_match_beats_title_case_difference():
    nav = [
        make_nav("2.3 Frontend Architecture", f"{BASE}/2.3-frontend-architecture"),
        make_nav("2.4 Backend", f"{BASE}/2.4-backend"),
    ]
    content = [
        make_content("2.4", "backend", "BACKEND BODY"),
        make_content("2.3", "FRONTEND  architecture", "FRONTEND BODY"),
    ]
    matches = match(nav, content)
    by_title = {m.navigation.title: m for m in matches}
    assert by_title["2.3 Frontend Architecture"].content.content == "FRONTEND BODY"
    assert by_title["2.4 Backend"].content.content == "BACKEND BODY"
    assert all(m.status is PageOutcome.SAVED for m in matches)


def test_url_path_match_when_content_has_no_id():
    nav = [make_nav("Overview", f"{BASE}/1-overview")]
    content = [
        make_content("", "Overview", "OVERVIEW", url=f"{BASE}/1-overview"),
    ]
    matches = match(nav, content)
    assert matches[0].status is PageOutcome.SAVED
    assert matches[0].match_kind == "url"


def test_same_content_never_assigned_twice():
    nav = [
        make_nav("Getting Started", f"{BASE}/1-getting-started"),
        make_nav("Introduction", f"{BASE}/2-introduction"),
    ]
    content = [make_content("1", "Getting Started", "ONLY BODY")]
    matches = match(nav, content)

    saved = [m for m in matches if m.status is PageOutcome.SAVED]
    assert len(saved) == 1
    assert saved[0].navigation.title == "Getting Started"

    other = next(m for m in matches if m.navigation.title == "Introduction")
    assert other.status is PageOutcome.CONTENT_MISSING
    assert other.content is None


def test_case_and_spacing_title_fallback_pairs():
    nav = [make_nav("authentication flow", f"{BASE}/auth")]
    content = [make_content("", "Authentication  Flow", "AUTH")]
    matches = match(nav, content)
    assert matches[0].status is PageOutcome.SAVED
    assert matches[0].content.content == "AUTH"


def test_hierarchy_number_in_title_does_not_break_fallback():
    assert title_key("2.3 Frontend, Architecture!") == title_key("frontend architecture")


def test_ambiguous_title_is_not_guessed():
    nav = [make_nav("Overview", f"{BASE}/overview")]
    content = [
        make_content("1", "Overview", "FIRST"),
        make_content("2", "overview", "SECOND"),
    ]
    matches = match(nav, content)
    assert matches[0].status is PageOutcome.TITLE_MISMATCH
    assert matches[0].content is None


def test_stable_id_cannot_be_stolen_by_earlier_title_match():
    nav = [
        make_nav("Introduction", f"{BASE}/1-introduction"),
        make_nav("Setup", f"{BASE}/2-setup"),
    ]
    # The content titled "Introduction" actually belongs to page id 2 via its
    # title, while id 1 has no title match.  The id match must win.
    content = [
        make_content("1", "Something Else", "ID ONE"),
        make_content("2", "Introduction", "ID TWO"),
    ]
    matches = match(nav, content)
    by_title = {m.navigation.title: m for m in matches}
    assert by_title["Introduction"].content.content == "ID ONE"
    assert by_title["Introduction"].match_kind == "page_id"
    assert by_title["Setup"].content.content == "ID TWO"


def test_no_content_yields_content_missing():
    nav = [make_nav("Anything", f"{BASE}/1-anything")]
    matches = match(nav, [])
    assert matches[0].status is PageOutcome.CONTENT_MISSING
    assert matches[0].content is None


def test_empty_content_is_treated_as_missing():
    nav = [make_nav("Page", f"{BASE}/1-page")]
    content = [make_content("1", "Page", "   \n  ")]
    matches = match(nav, content)
    assert matches[0].status is PageOutcome.CONTENT_MISSING


def test_duplicate_navigation_links_are_collapsed():
    nav = [
        make_nav("Overview", f"{BASE}/1-overview"),
        make_nav("Overview", f"{BASE}/1-overview/"),
    ]
    content = [make_content("1", "Overview", "BODY")]
    matches = match(nav, content)
    assert len(matches) == 1
    assert matches[0].status is PageOutcome.SAVED


def test_duplicate_extracted_content_is_collapsed():
    nav = [
        make_nav("A", f"{BASE}/1-a"),
        make_nav("B", f"{BASE}/2-b"),
    ]
    content = [
        make_content("1", "A", "A BODY"),
        make_content("1", "A", "A BODY"),
        make_content("2", "B", "B BODY"),
    ]
    matches = match(nav, content)
    assert all(m.status is PageOutcome.SAVED for m in matches)
    assert {m.content.content for m in matches} == {"A BODY", "B BODY"}


def test_duplicate_bodies_never_used_for_two_pages():
    nav = [
        make_nav("A", f"{BASE}/1-a"),
        make_nav("B", f"{BASE}/2-b"),
    ]
    content = [
        make_content("1", "A", "SHARED BODY"),
        make_content("2", "B", "SHARED BODY"),
    ]
    matches = match(nav, content)
    assert sum(1 for m in matches if m.status is PageOutcome.SAVED) == 1
    assert any(
        m.status is PageOutcome.CONTENT_MISSING for m in matches
    )
