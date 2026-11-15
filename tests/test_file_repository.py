#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Guards on the filesystem adapter: never write empty page files."""

import os

import pytest

from src.infrastructure.gateway.file_adapter import FileAdapter
from src.infrastructure.repository.file_repository import FileRepository


def test_save_markdown_rejects_empty_content(tmp_path):
    repository = FileRepository(FileAdapter())
    target = os.path.join(str(tmp_path), "empty.md")

    with pytest.raises(ValueError):
        repository.save_markdown("   \n\t ", target)

    assert not os.path.exists(target)


def test_save_markdown_writes_real_content(tmp_path):
    repository = FileRepository(FileAdapter())
    target = os.path.join(str(tmp_path), "page.md")

    repository.save_markdown("# Title\n\nBody", target)

    assert os.path.isfile(target)
    with open(target, encoding="utf-8") as handle:
        assert handle.read() == "# Title\n\nBody"
