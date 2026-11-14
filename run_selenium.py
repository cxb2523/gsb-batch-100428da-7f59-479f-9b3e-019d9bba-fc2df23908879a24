"""
Selenium 入口脚本（调用 src/selenium_with_proxy.py）。
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SRC_DIR = BASE_DIR / "src"
sys.path.insert(0, str(SRC_DIR))

from src.selenium_with_proxy import main as run_main  # noqa: E402


if __name__ == "__main__":
    run_main()
