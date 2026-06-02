"""Centralized configuration for the Pulse (Flask + React) dashboard webapp.

This is based on the previous DEMO app's `settings.py`, but lives in the shared
`pulse_dashboard` package so the plugin stays lightweight and
the webapp backend can import it reliably.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


APP_NAME = os.getenv("PULSE_APP_NAME", "dataiku_pulse")

HOST = os.getenv("PULSE_HOST", "127.0.0.1")  # nosec B104 (local dev default; DSS controls binding)
PORT = int(os.getenv("PULSE_PORT", "8995"))

DUCKDB_DIR = Path(os.getenv("PULSE_DUCKDB_DIR", str(Path(tempfile.gettempdir()) / "pulse")))
DUCKDB_PATH = Path(os.getenv("PULSE_DUCKDB_PATH", str(DUCKDB_DIR / "dataiku_pulse.db")))
DUCKDB_METADATA_PATH = Path(os.getenv("PULSE_DUCKDB_METADATA_PATH", f"{DUCKDB_PATH}.meta.json"))

DUCKDB_READ_ONLY = os.getenv("PULSE_DUCKDB_READ_ONLY", "0").lower() in ("1", "true", "yes")

PULSE_AUTO_INIT_DUCKDB = os.getenv("PULSE_AUTO_INIT_DUCKDB", "1").lower() in ("1", "true", "yes")
PULSE_AUTO_LOAD_GOLD_TABLES = os.getenv("PULSE_AUTO_LOAD_GOLD_TABLES", "1").lower() in ("1", "true", "yes")
PULSE_AUTO_LOAD_REPLACE = os.getenv("PULSE_AUTO_LOAD_REPLACE", "0").lower() in ("1", "true", "yes")
PULSE_DUCKDB_REBUILD_ON_STARTUP_STALE = os.getenv("PULSE_DUCKDB_REBUILD_ON_STARTUP_STALE", "1").lower() in (
    "1",
    "true",
    "yes",
)
PULSE_DUCKDB_STARTUP_STALE_TOLERANCE_SEC = float(os.getenv("PULSE_DUCKDB_STARTUP_STALE_TOLERANCE_SEC", "5"))


def _resolve_default_project_key() -> str | None:
    """Best-effort lookup of the current DSS project key.

    In DSS (including webapp backends), Dataiku exposes helpers for resolving the
    current project context. For local dev runs (gunicorn/flask), these helpers
    may be unavailable.
    """

    try:
        import dataiku  # type: ignore

        get_default_project = getattr(dataiku, "get_default_project", None)
        if callable(get_default_project):
            project = get_default_project()
            if isinstance(project, str):
                return project
            key = getattr(project, "project_key", None) or getattr(project, "projectKey", None)
            if key:
                return str(key)

        default_project_key = getattr(dataiku, "default_project_key", None)
        if callable(default_project_key):
            key = default_project_key()
            if key:
                return str(key)

    except Exception:
        return None

    return None


# GOLD folder lookup defaults.
#
# Resolution order for the source project:
# 1) explicit env var `PULSE_SOURCE_PROJECT_KEY`
# 2) DSS current project (when available)
# 3) fallback for local dev
PULSE_SOURCE_PROJECT_KEY = os.getenv(
    "PULSE_SOURCE_PROJECT_KEY",
    _resolve_default_project_key() or "DATAIKU_PULSE_DASHBOARD",
)
PULSE_GOLD_TABLES_FOLDER_ID = os.getenv("PULSE_GOLD_TABLES_FOLDER_ID", "")
PULSE_GOLD_TABLES_FOLDER_NAME = os.getenv("PULSE_GOLD_TABLES_FOLDER_NAME", "gold_data")

# GOLD auto-load filters (managed-folder paths).
PULSE_GOLD_LOAD_PREFIX = os.getenv("PULSE_GOLD_LOAD_PREFIX", "")
PULSE_GOLD_LOAD_NAME_GLOB = os.getenv("PULSE_GOLD_LOAD_NAME_GLOB", "*")
PULSE_GOLD_LOAD_USE_DUCKDB_CSV_AUTO = os.getenv("PULSE_GOLD_LOAD_USE_DUCKDB_CSV_AUTO", "1").lower() in (
    "1",
    "true",
    "yes",
)

# DuckDB init locking.
PULSE_DUCKDB_INIT_LOCK_PATH = os.getenv(
    "PULSE_DUCKDB_INIT_LOCK_PATH", str(DUCKDB_DIR / ".duckdb_init.lock")
)
PULSE_DUCKDB_INIT_TIMEOUT_SEC = float(os.getenv("PULSE_DUCKDB_INIT_TIMEOUT_SEC", "300"))
PULSE_DUCKDB_INIT_LOCK_STALE_SEC = float(os.getenv("PULSE_DUCKDB_INIT_LOCK_STALE_SEC", "600"))

# Demo/dev helpers.
PULSE_SEED_DEMO_DEV_ACTIVITY = os.getenv("PULSE_SEED_DEMO_DEV_ACTIVITY", "0").lower() in (
    "1",
    "true",
    "yes",
)

# Comma-separated list of instance_name values that should win tie-breakers
# when selecting a canonical user record across multiple instances.
#
# Example: "hub-dss,tam-design-us"
PULSE_HUB_INSTANCE_NAMES = os.getenv("PULSE_HUB_INSTANCE_NAMES", "")


def ensure_duckdb_parent_dir() -> None:
    DUCKDB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DUCKDB_METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)
