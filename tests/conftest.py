import sys
from pathlib import Path

# Make the package importable when pytest is launched from its console script
# (where the repository root is not automatically on sys.path).
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
