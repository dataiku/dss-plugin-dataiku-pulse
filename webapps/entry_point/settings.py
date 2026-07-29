"""Centralized configuration for the Pulse (Flask + React) webapp.

Keep runtime configuration here so that:
- Flask endpoints
- background jobs / initialization code
- React build assumptions (paths, ports)

…all agree on the same defaults.

This module intentionally avoids importing heavy dependencies.
"""

from __future__ import annotations

import os
from pathlib import Path


# ----------------------------------------------------------------------------
# App identity
# ----------------------------------------------------------------------------
APP_NAME = os.getenv("PULSE_APP_NAME", "dataiku_pulse")


# ----------------------------------------------------------------------------
# Flask / webapp configuration
# ----------------------------------------------------------------------------
HOST = os.getenv("PULSE_HOST") or "127.0.0.1"
PORT = int(os.getenv("PULSE_PORT", "8995"))


# ----------------------------------------------------------------------------
# DuckDB configuration
# ----------------------------------------------------------------------------
DUCKDB_DIR = Path(os.getenv("PULSE_DUCKDB_DIR") or str(Path.cwd() / ".tmp" / "pulse"))
DUCKDB_PATH = Path(os.getenv("PULSE_DUCKDB_PATH", str(DUCKDB_DIR / "dataiku_pulse.db")))

# If set, avoid any writes (useful for future read-only modes)
DUCKDB_READ_ONLY = os.getenv("PULSE_DUCKDB_READ_ONLY", "0").lower() in ("1", "true", "yes")

# Auto-initialization (create DB + optionally load GOLD parquet tables)
PULSE_AUTO_INIT_DUCKDB = os.getenv("PULSE_AUTO_INIT_DUCKDB", "1").lower() in ("1", "true", "yes")
PULSE_AUTO_LOAD_GOLD_TABLES = os.getenv("PULSE_AUTO_LOAD_GOLD_TABLES", "1").lower() in (
    "1",
    "true",
    "yes",
)

# When auto-loading, replace existing tables (default: no, only load if missing)
PULSE_AUTO_LOAD_REPLACE = os.getenv("PULSE_AUTO_LOAD_REPLACE", "0").lower() in ("1", "true", "yes")

# Limit which files are loaded from the managed folder (useful for staged rollout)
#
# Default to the real pipeline output: parquet files under `gold/`.
#
# We keep `allowed_table_names` filtering in `init_db.py`, so widening the glob
# does not automatically ingest everything.
PULSE_GOLD_LOAD_PREFIX = os.getenv("PULSE_GOLD_LOAD_PREFIX", "gold/")
PULSE_GOLD_LOAD_NAME_GLOB = os.getenv("PULSE_GOLD_LOAD_NAME_GLOB", "*.parquet")

# If true, the loader will infer types from CSV using DuckDB's `read_csv_auto`
# and will reject non-base tables unless explicitly allowed.
PULSE_GOLD_LOAD_USE_DUCKDB_CSV_AUTO = os.getenv("PULSE_GOLD_LOAD_USE_DUCKDB_CSV_AUTO", "1").lower() in (
    "1",
    "true",
    "yes",
)

# Prevent multiple gunicorn workers rebuilding concurrently
PULSE_DUCKDB_INIT_LOCK_PATH = Path(
    os.getenv("PULSE_DUCKDB_INIT_LOCK_PATH", str(DUCKDB_DIR / ".duckdb_init.lock"))
)
PULSE_DUCKDB_INIT_TIMEOUT_SEC = float(os.getenv("PULSE_DUCKDB_INIT_TIMEOUT_SEC", "60"))

# Dummy data mode (intended for DEMO/dev)
# When enabled, the app will DROP all existing DuckDB tables/views and rebuild
# a schema based on `pulse_duckdb/datasets/**/*.md`, then insert small dummy datasets.
PULSE_USE_DUMMY_DATA = os.getenv("PULSE_USE_DUMMY_DATA", "0").lower() in ("1", "true", "yes")

# If true, and if the dev-activity base tables exist but are empty, seed them with
# small demo datasets so the UI can render without a full GOLD pipeline.
PULSE_SEED_DEMO_DEV_ACTIVITY = os.getenv("PULSE_SEED_DEMO_DEV_ACTIVITY", "0").lower() in (
    "1",
    "true",
    "yes",
)


# ----------------------------------------------------------------------------
# Dataiku source configuration
# ----------------------------------------------------------------------------
# Managed folder lookups
# ----------------------------------------------------------------------------
# The app always reads from the *current* DSS project (`client.get_default_project()`)
# and fixed folder names:
# - SILVER: `partitioned_data`
# - GOLD: `gold_data`
PULSE_GOLD_TABLES_FOLDER_ID = os.getenv("PULSE_GOLD_TABLES_FOLDER_ID", "")
PULSE_GOLD_TABLES_FOLDER_NAME = "gold_data"

PULSE_SILVER_FOLDER_ID = os.getenv("PULSE_SILVER_FOLDER_ID", "")
PULSE_SILVER_FOLDER_NAME = "partitioned_data"

# If enabled, mirror `silver/category=event_mapping/**` parquet locally and expose
# DuckDB views over them.
PULSE_LOAD_SILVER_EVENT_MAPPING = os.getenv("PULSE_LOAD_SILVER_EVENT_MAPPING", "1").lower() in (
    "1",
    "true",
    "yes",
)


def ensure_duckdb_parent_dir() -> None:
    """Create parent directory for DuckDB file if needed."""
    DUCKDB_PATH.parent.mkdir(parents=True, exist_ok=True)
