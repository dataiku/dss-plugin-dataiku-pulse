from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON_LIB = REPO_ROOT / "python-lib"
STUBS = REPO_ROOT / "tests" / "stubs"

# Stubs first so the fake `dataiku` wins; python-lib for the plugin packages.
for entry in (str(STUBS), str(PYTHON_LIB)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import dataiku  # noqa: E402,F401  (registers dataiku.runnables / dataiku.customrecipe stubs)
