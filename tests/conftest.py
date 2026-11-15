"""Shared fixtures for the module-failure tests.

The tests build tiny fake Moodle endpoints / load functions so that no network
access is needed.
"""

import sys
from pathlib import Path

# Make the repository importable when pytest is not run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
