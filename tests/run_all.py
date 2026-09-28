"""一键跑全部单测（无需 pytest）。

    python tests/run_all.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SRC_DIR = TESTS_DIR.parent / "src"

for path in (str(SRC_DIR), str(TESTS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)


def main() -> int:
    suite = unittest.defaultTestLoader.discover(start_dir=str(TESTS_DIR), pattern="test_*.py", top_level_dir=str(TESTS_DIR))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
