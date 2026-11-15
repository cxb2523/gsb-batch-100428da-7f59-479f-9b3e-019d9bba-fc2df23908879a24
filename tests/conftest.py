# -*- coding: utf-8 -*-
"""Pytest bootstrap: make the Scrapy project importable from the repo root."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRAPY_PROJECT_DIR = PROJECT_ROOT / "olx_scraper"
TESTS_DIR = Path(__file__).resolve().parent

for path in (str(SCRAPY_PROJECT_DIR), str(PROJECT_ROOT), str(TESTS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)
