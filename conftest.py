import os
import sys

# Make the repository root importable (pinterest.py, exceptions.py, ...)
# even when Python runs in isolated mode (-I), which ignores PYTHONPATH.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
