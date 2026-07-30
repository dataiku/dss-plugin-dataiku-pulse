"""Bootstrap that ensures the Pulse plugin python-lib is importable.

We intentionally depend on the plugin implementation for:
- managed folder storage discovery
- DuckDB blob (S3/Azure/GCS) configuration

This module MUST be imported before other Pulse modules that touch DuckDB.

Mode A (chosen): fail fast if the plugin python-lib is not available.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _plugin_pythonlib_path() -> Path:
    # Repo layouts (Code Studio workspace):
    #
    # A) Legacy (older workspace structure)
    # project-lib-versioned/python/
    #   webapps/entry_point/
    #   dss-plugin-dataiku-pulse/python-lib/
    #
    # B) Current (this repo)
    # project-lib-versioned/python/
    #   webapps/entry_point/
    #   dataiku-pulse/python-lib/
    base = Path(__file__).resolve().parent
    candidates = [
        # Repo checkout (when running from the plugin repo)
        (base / ".." / ".." / "dataiku-pulse" / "python-lib").resolve(),
        # Workspace-managed plugin env layout
        (base / ".." / ".." / ".." / "dss-plugin-dataiku-pulse" / "python-lib").resolve(),
        # Legacy layout (kept for backwards compatibility)
        (base / ".." / ".." / "dss-plugin-dataiku-pulse" / "python-lib").resolve(),
    ]
    for path in candidates:
        if path.exists():
            return path

    # Default to the current repo path for a clearer error message.
    return candidates[0]


def ensure_plugin_pythonlib_on_path() -> Path:
    path = _plugin_pythonlib_path()
    if not path.exists():
        raise RuntimeError(f"Pulse plugin python-lib not found at: {path}")

    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

    # Fail fast: these imports must work in all deployments.
    #
    # In the repo checkout, the top-level package is `data_collection`.
    # In the workspace-managed plugin checkout, the equivalent code lives under `pulse_duckdb`.
    try:
        import data_collection.pulse_duckdb.context  # noqa: F401
        import data_collection.pulse_duckdb.engine.storage_config  # noqa: F401
    except Exception:
        import pulse_duckdb.engine.storage_config  # noqa: F401

    return path


# Execute on import.
PLUGIN_PYTHONLIB_PATH = ensure_plugin_pythonlib_on_path()
