#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""CLI contract: entry point, ``wiki`` command and ``-o`` output directory."""

import asyncio
import os
import sys

import pytest

from src.application.interface import cli
from src.domain.models.wiki_page import (
    ContentPage,
    NavigationPage,
    WikiContent,
)
from src.infrastructure.gateway.file_adapter import FileAdapter
from src.infrastructure.repository.file_repository import FileRepository


class StaticRSCRepository:
    def __init__(self):
        self.requested = []

    async def fetch_wiki_content(self, base_url):
        self.requested.append(base_url)
        return WikiContent(
            navigation_pages=[
                NavigationPage(
                    title="Overview",
                    url="https://deepwiki.com/acme/widget/1-overview",
                )
            ],
            content_pages=[
                ContentPage("1", "Overview", "# Overview\n\nBody"),
            ],
        )


def test_parse_arguments_keeps_o_flag(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys,
        "argv",
        ["cli", "wiki", "https://deepwiki.com/acme/widget", "-o", str(tmp_path)],
    )
    args = cli.parse_arguments()
    assert args.command == "wiki"
    assert args.url == "https://deepwiki.com/acme/widget"
    assert args.output == str(tmp_path)


def test_parse_arguments_defaults_output_to_cwd(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["cli", "wiki", "https://deepwiki.com/acme/widget"],
    )
    args = cli.parse_arguments()
    assert args.output == os.getcwd()


def test_run_cli_uses_injected_repositories(monkeypatch, tmp_path):
    rsc = StaticRSCRepository()
    files = FileRepository(FileAdapter())

    monkeypatch.setattr(sys, "argv", ["cli", "wiki", "https://deepwiki.com/acme/widget", "-o", str(tmp_path)])
    monkeypatch.setattr(cli, "RSCRepository", lambda adapter: rsc)
    monkeypatch.setattr(cli, "FileRepository", lambda adapter: files)
    monkeypatch.setattr(cli, "RSCAdapter", lambda: object())
    monkeypatch.setattr(cli, "FileAdapter", lambda: object())

    exit_code = asyncio.run(cli.run_cli())

    assert exit_code == 0
    assert rsc.requested == ["https://deepwiki.com/acme/widget"]
    output_dir = tmp_path / "wiki" / "acme" / "widget"
    assert (output_dir / "1-Overview.md").is_file()
    assert (output_dir / "index.md").is_file()


def test_run_cli_reports_fetch_failure(monkeypatch, tmp_path):
    class FailingRSCRepository:
        async def fetch_wiki_content(self, base_url):
            raise RuntimeError("boom")

    monkeypatch.setattr(sys, "argv", ["cli", "wiki", "https://deepwiki.com/acme/widget", "-o", str(tmp_path)])
    monkeypatch.setattr(cli, "RSCRepository", lambda adapter: FailingRSCRepository())
    monkeypatch.setattr(cli, "FileRepository", lambda adapter: FileRepository(FileAdapter()))
    monkeypatch.setattr(cli, "RSCAdapter", lambda: object())
    monkeypatch.setattr(cli, "FileAdapter", lambda: object())

    assert asyncio.run(cli.run_cli()) == 1


def test_cli_help_without_arguments(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["cli"])
    with pytest.raises(SystemExit) as excinfo:
        cli.parse_arguments()
    assert excinfo.value.code == 1
