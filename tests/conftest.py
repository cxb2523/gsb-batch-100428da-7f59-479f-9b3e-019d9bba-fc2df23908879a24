"""
Pytest setup: pipeline modules use flat imports (e.g. `from config import ...`)
because they are run as `python pipeline/fetch_feeds.py`. Add the pipeline
directory to sys.path so the tests can import them the same way.
"""

import sys
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent / "pipeline"
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))
