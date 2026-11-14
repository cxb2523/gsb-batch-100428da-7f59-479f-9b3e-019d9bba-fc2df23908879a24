"""Pytest bootstrap: make the repo root importable even under `python -I` (isolated mode skips .pth files)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
