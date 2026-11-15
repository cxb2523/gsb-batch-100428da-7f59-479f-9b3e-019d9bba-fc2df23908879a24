#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""End-to-end usecase tests with injected fake repositories (no network)."""

import asyncio
import os
import re

from src.application.usecase.rsc_wiki_usecase import (
    ConvertWikiSiteToMarkdownViaRSCUsecase,
)
from src.domain.models.wiki_page import (
    ContentPage,
    NavigationPage,
    PageOutcome,
    WikiContent,
)


BASE = "https://deepwiki.com/acme/widget"
OUTPUT_BASE = "/tmp/deepwiki-output"


class FakeRSCRepository:
    def __init__(self, wiki_content):
        self.wiki_content = wiki_content
        self.calls = []

    async def fetch_wiki_content(self, base_url):
        self.calls.append(base_url)
        return self.wiki_content


class FakeFileRepository:
    """Records writes instead of touching the disk."""

    def __init__(self):
        self.files = {}
        self.directories = []

    def ensure_directory(self, dir_path):
        self.directories.append(dir_path)
        return dir_path

    def save_markdown(self, content, filepath):
        self.files[filepath] = content

    def read(self, filename):
        return self.files[os.path.join(OUTPUT_DIR, filename)]


OUTPUT_DIR = os.path.join(OUTPUT_BASE, "wiki", "acme", "widget")


def run(usecase):
    return asyncio.run(usecase.execute(BASE, OUTPUT_BASE))


def make_usecase(navigation_pages, content_pages):
    rsc = FakeRSCRepository(
        WikiContent(
            navigation_pages=navigation_pages,
            content_pages=content_pages,
        )
    )
    files = FakeFileRepository()
    return ConvertWikiSiteToMarkdownViaRSCUsecase(rsc, files), files


def nav(title, page_id):
    return NavigationPage(title=title, url=f"{BASE}/{page_id}")


def content(page_id, title, body):
    return ContentPage(page_id=page_id, title=title, content=body)


def test_only_matched_pages_written_and_index_lists_them():
    usecase, files = make_usecase(
        navigation_pages=[
            nav("1 Overview", "1"),
            nav("2 Architecture", "2"),
            nav("3 Missing Page", "3"),
        ],
        content_pages=[
            content("1", "Overview", "# Overview\n\nBody one"),
            content("2", "Architecture", "# Architecture\n\nBody two"),
        ],
    )
    result = run(usecase)

    assert result.count(PageOutcome.SAVED) == 2
    assert result.count(PageOutcome.CONTENT_MISSING) == 1
    missing = result.pages_with_status(PageOutcome.CONTENT_MISSING)[0]
    assert missing.title == "3 Missing Page"
    assert missing.reason

    saved_filenames = {page.filename for page in result.saved_pages}
    assert "3-missing-page.md" not in saved_filenames

    # Both saved pages plus index.md, nothing for the missing page.
    written_names = {
        os.path.basename(path) for path in files.files
    }
    assert written_names == {"1-Overview.md", "2-Architecture.md", "index.md"}
    assert result.index_path is not None

    index = files.read("index.md")
    assert "[1 Overview](./1-Overview.md)" in index
    assert "[2 Architecture](./2-Architecture.md)" in index
    assert "3 Missing Page" not in index


def test_mismatched_title_not_written_as_wrong_page():
    usecase, files = make_usecase(
        navigation_pages=[nav("Authentication", "1-overview")],
        content_pages=[content("2", "Totally Unrelated", "# Unrelated\n\nBody")],
    )
    result = run(usecase)

    assert result.count(PageOutcome.SAVED) == 0
    assert len(result.pages) == 1
    page = result.pages[0]
    assert page.status is PageOutcome.TITLE_MISMATCH
    assert page.reason
    assert page.filename is None

    # No page file written; the index is refreshed and lists no pages.
    page_files = [
        path for path in files.files if not path.endswith("index.md")
    ]
    assert page_files == []
    index = files.read("index.md")
    assert "](" not in index


def test_no_empty_file_for_empty_content():
    usecase, files = make_usecase(
        navigation_pages=[nav("Empty", "1")],
        content_pages=[content("1", "Empty", "   ")],
    )
    result = run(usecase)

    assert result.count(PageOutcome.SAVED) == 0
    assert result.pages[0].status is PageOutcome.CONTENT_MISSING
    assert set(files.files) == {
        os.path.join(OUTPUT_DIR, "index.md")
    }


def test_index_links_match_files_on_disk():
    usecase, files = make_usecase(
        navigation_pages=[
            nav("2.1 Frontend / UI", "2.1"),
            nav("2.2 Backend", "2.2"),
        ],
        content_pages=[
            content("2.1", "Frontend architecture", "# Frontend\n\nBody"),
            content("2.2", "Backend", "# Backend\n\nBody"),
        ],
    )
    result = run(usecase)
    index = files.read("index.md")

    links = re.findall(r"\]\((\./.+?)\)", index)
    written = {
        os.path.basename(path) for path in files.files if path.endswith(".md")
    }
    from urllib.parse import unquote

    assert {unquote(link[2:]) for link in links} == written - {"index.md"}
    assert links == ["./2.1-Frontend%20-%20UI.md", "./2.2-Backend.md"]


def test_duplicate_content_not_reused_across_pages():
    usecase, files = make_usecase(
        navigation_pages=[nav("Intro", "1"), nav("Setup", "2")],
        content_pages=[content("1", "Intro", "# Intro\n\nShared")],
    )
    result = run(usecase)

    assert result.count(PageOutcome.SAVED) == 1
    assert result.count(PageOutcome.CONTENT_MISSING) == 1
    page_files = [path for path in files.files if not path.endswith("index.md")]
    assert len(page_files) == 1


def test_colliding_filenames_are_disambiguated():
    usecase, files = make_usecase(
        navigation_pages=[
            nav("Same title", "1"),
            nav("Same title", "2"),
        ],
        content_pages=[
            content("1", "Same title", "# One\n\nBody one"),
            content("2", "Same title", "# Two\n\nBody two"),
        ],
    )
    result = run(usecase)

    filenames = [page.filename for page in result.saved_pages]
    assert filenames == ["1-Same title.md", "2-Same title.md"]
    assert len(set(filenames)) == 2
    index = files.read("index.md")
    for filename in filenames:
        assert f"./{filename.replace(' ', '%20')}" in index


def test_fetch_failure_is_reported_without_writing():
    class FailingRepository:
        async def fetch_wiki_content(self, base_url):
            raise RuntimeError("network down")

    files = FakeFileRepository()
    usecase = ConvertWikiSiteToMarkdownViaRSCUsecase(FailingRepository(), files)
    result = run(usecase)

    assert result.error and "network down" in result.error
    assert result.pages == []
    assert files.files == {}


def test_navigation_only_still_settles_every_page():
    usecase, files = make_usecase(
        navigation_pages=[nav("A", "1"), nav("B", "2")],
        content_pages=[],
    )
    result = run(usecase)

    assert len(result.pages) == 2
    assert all(
        page.status is PageOutcome.CONTENT_MISSING for page in result.pages
    )
    assert set(files.files) == {os.path.join(OUTPUT_DIR, "index.md")}


def test_write_failure_is_reported_and_not_indexed():
    class PartiallyFailingFiles(FakeFileRepository):
        def save_markdown(self, content, filepath):
            if filepath.endswith("2-B.md"):
                raise OSError("disk full")
            super().save_markdown(content, filepath)

    rsc = FakeRSCRepository(
        WikiContent(
            navigation_pages=[nav("A", "1"), nav("B", "2")],
            content_pages=[
                content("1", "A", "# A\n\nBody A"),
                content("2", "B", "# B\n\nBody B"),
            ],
        )
    )
    files = PartiallyFailingFiles()
    result = run(ConvertWikiSiteToMarkdownViaRSCUsecase(rsc, files))

    assert result.count(PageOutcome.SAVED) == 1
    failed = [
        page for page in result.pages
        if page.status is PageOutcome.CONTENT_MISSING
    ]
    assert len(failed) == 1
    assert "disk full" in failed[0].reason
    index = files.read("index.md")
    assert "./1-A.md" in index
    assert "2-B.md" not in index


def test_no_navigation_refreshes_index_to_empty_listing():
    class EmptyRSC:
        async def fetch_wiki_content(self, base_url):
            return WikiContent()

    files = FakeFileRepository()
    # Simulate an index left over from a previous run.
    files.files[os.path.join(OUTPUT_DIR, "index.md")] = (
        "# acme/widget\n\n## Pages\n\n- [Old](./old.md)\n"
    )
    result = run(ConvertWikiSiteToMarkdownViaRSCUsecase(EmptyRSC(), files))

    assert result.error == "no navigation pages found"
    assert result.pages == []
    index = files.read("index.md")
    assert "old.md" not in index
    assert "](" not in index


def test_legacy_adapter_tuple_shape_is_accepted():
    """An injected adapter may still return the old (nav, content) tuple."""

    class LegacyRepository:
        async def fetch_wiki_content(self, base_url):
            return (
                [{"title": "Intro", "url": f"{BASE}/1-intro"}],
                {f"{BASE}/1-intro": "# Intro\n\nLegacy body"},
            )

    files = FakeFileRepository()
    result = run(ConvertWikiSiteToMarkdownViaRSCUsecase(LegacyRepository(), files))

    assert result.count(PageOutcome.SAVED) == 1
    assert files.read("1-Intro.md") == "# Intro\n\nLegacy body"
