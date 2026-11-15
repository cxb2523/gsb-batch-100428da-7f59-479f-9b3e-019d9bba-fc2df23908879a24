# -*- coding: utf-8 -*-
"""Tests for loading the legacy args.py configuration file."""

import importlib

from olx_scraper import config


def test_load_info_reads_the_repository_args_file():
    info = config.load_info()
    assert info("city") == "lahore"
    assert info("product") == "iphone"
    assert info("minimumprice") == "25000"


def test_unknown_keys_return_empty_string():
    info = config.load_info()
    assert info("something-else") == ""


def test_missing_args_file_falls_back_safely(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "__file__", str(tmp_path / "pkg" / "config.py"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        importlib, "import_module", lambda name: (_ for _ in ()).throw(ImportError)
    )
    info = config.load_info()
    assert info("city") == ""
    assert info("product") == ""


def test_file_loader_is_used_when_package_import_fails(monkeypatch, tmp_path):
    args_file = tmp_path / "args.py"
    args_file.write_text(
        "def info(key):\n"
        "    return {'city': 'karachi', 'product': 'bike',"
        " 'minimumprice': '5000'}.get(key.lower(), '')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "__file__", str(tmp_path / "pkg" / "config.py"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        importlib, "import_module", lambda name: (_ for _ in ()).throw(ImportError)
    )

    info = config.load_info()
    assert info("city") == "karachi"
    assert info("minimumprice") == "5000"
