import sys
from pathlib import Path

# make the crawler package (src layout) importable in tests
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
