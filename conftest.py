"""Pytest bootstrap.

The tests are run with ``python -I -m pytest`` (isololated mode), which
does not put the current working directory on ``sys.path``. Importing
this root-level conftest makes pytest insert the repository root, so the
``facebook`` package is importable without installing it.
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
