"""
tests/conftest.py - Shared pytest fixtures and path setup.

Adds the project root to sys.path so tests can import modules without
installing the package.
"""

import sys
from pathlib import Path

# Ensure the project root (parent of tests/) is on the path
ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
