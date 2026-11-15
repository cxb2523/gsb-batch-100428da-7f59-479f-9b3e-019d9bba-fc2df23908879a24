#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Tests for filename generation and the index/disk contract."""

import asyncio
import os
from urllib.parse import unquote

from src.application.usecase.rsc_wiki_usecase import (
    ConvertWikiSiteToMarkdownViaRSCUsecase,
)
from src.domain.models.wiki_page import (
    ContentPage,
    NavigationPage,
    PageOutcome,
    WikiContent,
)
from src.domain.services.page_filename import (
    build_page_filename,
    relative_link,
    sanitize_title,
)
from src.infrastructure.gateway.file_adapter import FileAdapter
from src.infrastructure.repository.file_repository import FileRepository


def test_sanitize_replaces_path_separators_and_illegal_chars():
    assert sanitize_title("Frontend / Backend") == "Frontend - Backend"
    assert sanitize_title('a:b*c?d"e<f>g|h') == "abcdefgh"
    assert sanitize_title("  trailing dots...  ") == "trailing dots"
    assert sanitize_title("中文 标题") == "中文 标题"
    assert sanitize_title("") == ""
    assert sanitize_title("CON") == ""


def test_filename_strips_duplicate_id_prefix():
    assert (
        build_page_filename("2.1", "2.1 Frontend", 1)
        == "2.1-Frontend.md"
    )
    assert (
        build_page_filename("2.1", "Frontend architecture", 1)
        == "2.1-Frontend architecture.md"
    )
    assert build_page_filename(None, "Overview", 3) == "3-Overview.md"


def test_filename_uniqueness_is_case_insensitive():
    first = build_page_filename("1", "Same", 1)
    second = build_page_filename("1", "Same", 1, {first})
    third = build_page_filename("1", "same", 1, {first, second})
    assert first.casefold() != second.casefold()
    assert second == "1-Same-2.md"
    assert third == "1-same-3.md"


def test_relative_link_encodes_spaces_and_keeps_extension():
    link = relative_link("2.1-Frontend architecture.md")
    assert link == "./2.1-Frontend%20architecture.md"
    # Spying decoded target must match the filename exactly.
    assert unquote(link[2:]) == "2.1-Frontend architecture.md"


def _write_pages(output_dir):
    class StaticRSC:
        async def fetch_wiki_content(self, base_url):
            return WikiContent(
                navigation_pages=[
                    NavigationPage(
                        title="1 Overview",
                        url="https://deepwiki.com/acme/widget/1-overview",
                    ),
                    NavigationPage(
                        title="2 Missing",
                        url="https://deepwiki.com/acme/widget/2-missing",
                    ),
                    NavigationPage(
                        title="3 Deep / Dive",
                        url="https://deepwiki.com/acme/widget/3-deep-dive",
                    ),
                ],
                content_pages=[
                    ContentPage("1", "Overview", "# Overview\n\nBody"),
                    ContentPage("3", "Deep Dive", "# Deep\n\nBody"),
                ],
            )

    usecase = ConvertWikiSiteToMarkdownViaRSCUsecase(
        StaticRSC(), FileRepository(FileAdapter())
    )
    return asyncio.run(
        usecase.execute("https://deepwiki.com/acme/widget", output_dir)
    )


def test_index_links_resolve_to_files_on_disk(tmp_path):
    result = _write_pages(str(tmp_path))

    output_dir = os.path.join(str(tmp_path), "wiki", "acme", "widget")
    assert result.output_directory == output_dir

    on_disk = set(os.listdir(output_dir))
    assert on_disk == {"1-Overview.md", "3-Deep - Dive.md", "index.md"}
    assert "2-Missing.md" not in on_disk

    with open(os.path.join(output_dir, "index.md"), encoding="utf-8") as handle:
        index = handle.read()

    links = [
        line.split("](", 1)[1].rstrip(")")
        for line in index.splitlines()
        if "](" in line
    ]
    assert links == ["./1-Overview.md", "./3-Deep%20-%20Dive.md"]
    for link in links:
        target = os.path.join(output_dir, unquote(link[2:]))
        assert os.path.isfile(target)
        assert os.path.getsize(target) > 0

    assert "2 Missing" not in index


def test_bracketed_title_does_not_break_index_link():
    from src.application.usecase.rsc_wiki_usecase import (
        ConvertWikiSiteToMarkdownViaRSCUsecase,
    )

    class StaticRSC:
        async def fetch_wiki_content(self, base_url):
            return WikiContent(
                navigation_pages=[
                    NavigationPage(
                        title="API [v2]",
                        url="https://deepwiki.com/acme/widget/1-api",
                    )
                ],
                content_pages=[
                    ContentPage("1", "API [v2]", "# API\n\nBody"),
                ],
            )

    class RecordingFiles:
        def __init__(self):
            self.files = {}

        def ensure_directory(self, dir_path):
            return dir_path

        def save_markdown(self, content, filepath):
            self.files[filepath] = content

    files = RecordingFiles()
    result = asyncio.run(
        ConvertWikiSiteToMarkdownViaRSCUsecase(StaticRSC(), files).execute(
            "https://deepwiki.com/acme/widget", "/tmp/out"
        )
    )
    assert result.count(PageOutcome.SAVED) == 1

    index = next(
        content
        for path, content in files.files.items()
        if path.endswith("index.md")
    )
    links = [
        line.split("](", 1)[1].rstrip(")")
        for line in index.splitlines()
        if "](" in line
    ]
    assert links == ["./1-API%20%5Bv2%5D.md"]
    assert "1-API [v2].md" in {
        os.path.basename(path) for path in files.files
    }
