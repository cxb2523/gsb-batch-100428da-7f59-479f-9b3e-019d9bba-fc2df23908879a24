#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Tests for parsing RSC payloads into content pages (no network)."""

import json
import os

from src.domain.models.wiki_page import (
    NavigationPage,
    PageOutcome,
    WikiContent,
)
from src.domain.services.page_matcher import PageMatcher
from src.infrastructure.gateway.rsc_parser import (
    decode_rsc_text,
    extract_markdown_blobs,
    extract_page_metadata,
    parse_rsc_response,
    pair_content_with_metadata,
)

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def push_script(markdown: str) -> str:
    """Build a ``self.__next_f.push`` script tag for a Markdown document."""
    payload = json.dumps(markdown)
    return f"<script>self.__next_f.push([1,{payload}])</script>"


def metadata_script(pages):
    """Build a script tag carrying escaped DeepWiki page metadata."""
    inner = ",".join(
        '{\\"page_plan\\":{\\"id\\":\\"%s\\",\\"title\\":\\"%s\\"}}'
        % (page_id, title)
        for page_id, title in pages
    )
    return f"<script>self.__next_f.push([1,\"{{\\\"pages\\\":[{inner}]}}\"])</script>"


def build_rsc_response(blobs, pages):
    return metadata_script(pages) + "".join(push_script(blob) for blob in blobs)


def test_extract_markdown_blobs_ignores_json_payloads():
    response = (
        push_script("# Real Page\n\nSome body")
        + push_script('{"not": "markdown"}')
    )
    blobs = extract_markdown_blobs(response)
    assert blobs == ["# Real Page\n\nSome body"]


def test_extract_page_metadata_reads_page_plan():
    response = metadata_script([("1", "Overview"), ("2.1", "Architecture")])
    metadata = extract_page_metadata(response)
    assert metadata == [
        {"id": "1", "title": "Overview", "url": ""},
        {"id": "2.1", "title": "Architecture", "url": ""},
    ]


def test_pair_content_uses_heading_when_counts_differ():
    blobs = ["# Architecture\n\nA body", "# Overview\n\nO body"]
    metadata = [{"id": "1", "title": "Overview", "url": ""}]
    pages = pair_content_with_metadata(blobs, metadata)
    assert len(pages) == 2
    by_title = {page.title: page for page in pages}
    assert by_title["Overview"].page_id == "1"
    assert by_title["Architecture"].page_id == ""
    assert by_title["Architecture"].content.startswith("# Architecture")


def test_parse_rsc_response_aligns_positionally_when_counts_match():
    response = build_rsc_response(
        ["# Overview\n\nO body", "# Architecture\n\nA body"],
        [("1", "Overview"), ("2", "Architecture")],
    )
    wiki = parse_rsc_response(response)
    assert [page.page_id for page in wiki.content_pages] == ["1", "2"]
    assert wiki.content_pages[1].content.startswith("# Architecture")


def test_parsed_content_then_matches_navigation_by_id():
    response = build_rsc_response(
        ["# Different title\n\nBody"],
        [("2.3", "Frontend architecture")],
    )
    wiki = parse_rsc_response(response)

    matched = PageMatcher().match(
        WikiContent(
            navigation_pages=[
                NavigationPage(
                    title="Frontend Architecture",
                    url="https://deepwiki.com/org/repo/2.3-frontend-architecture",
                )
            ],
            content_pages=wiki.content_pages,
        )
    )
    assert matched[0].status is PageOutcome.SAVED
    assert matched[0].content.content == "# Different title\n\nBody"


def load_fixture() -> str:
    with open(
        os.path.join(FIXTURES, "sample_rsc_payload.html"), encoding="utf-8"
    ) as handle:
        return handle.read()


def test_fixture_decodes_unicode_markdown():
    blobs = extract_markdown_blobs(load_fixture())
    assert any("中文 section" in blob for blob in blobs)
    assert all(not blob.lstrip().startswith("{") for blob in blobs)


def test_fixture_pairs_metadata_positionally():
    wiki = parse_rsc_response(load_fixture())
    assert [page.page_id for page in wiki.content_pages] == ["1", "2", "2.1", "3"]


def test_fixture_navigation_matches_every_page_by_id():
    wiki = parse_rsc_response(load_fixture())
    navigation = [
        NavigationPage(
            title="OVERVIEW",
            url=f"https://deepwiki.com/acme/widget/{page_id}-slug",
        )
        for page_id in ("1", "2", "2.1", "3", "4")
    ]
    matches = PageMatcher().match(
        WikiContent(navigation_pages=navigation, content_pages=wiki.content_pages)
    )
    by_id = {
        page_id: match
        for page_id, match in zip(("1", "2", "2.1", "3", "4"), matches)
    }
    for page_id in ("1", "2", "2.1", "3"):
        assert by_id[page_id].status is PageOutcome.SAVED
        assert by_id[page_id].match_kind == "page_id"
    assert by_id["4"].status is PageOutcome.CONTENT_MISSING


def test_decode_uses_manual_unescape_for_broken_json():
    # A raw (non JSON-escaped) fragment with a literal unicode escape.
    assert decode_rsc_text(r"\u4e2d\u6587") == "中文"
