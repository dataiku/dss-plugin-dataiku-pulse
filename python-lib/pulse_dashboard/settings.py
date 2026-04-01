from __future__ import annotations

"""Centralized configuration for the Pulse (Flask + React) dashboard webapp.

This is based on the previous DEMO app's `settings.py`, but lives in the shared
`pulse_dashboard` package so the plugin stays lightweight and
the webapp backend can import it reliably.
"""

import os
from pathlib import Path


APP_NAME = os.getenv("PULSE_APP_NAME", "dataiku_pulse")

HOST = os.getenv("PULSE_HOST", "0.0.0.0")
PORT = int(os.getenv("PULSE_PORT", "8995"))

DUCKDB_DIR = Path(os.getenv("PULSE_DUCKDB_DIR", "/tmp/pulse"))
DUCKDB_PATH = Path(os.getenv("PULSE_DUCKDB_PATH", str(DUCKDB_DIR / "dataiku_pulse.db")))

DUCKDB_READ_ONLY = os.getenv("PULSE_DUCKDB_READ_ONLY", "0").lower() in ("1", "true", "yes")

PULSE_AUTO_INIT_DUCKDB = os.getenv("PULSE_AUTO_INIT_DUCKDB", "1").lower() in ("1", "true", "yes")
PULSE_AUTO_LOAD_GOLD_TABLES = os.getenv("PULSE_AUTO_LOAD_GOLD_TABLES", "1").lower() in ("1", "true", "yes")
PULSE_AUTO_LOAD_REPLACE = os.getenv("PULSE_AUTO_LOAD_REPLACE", "0").lower() in ("1", "true", "yes")

# GOLD folder lookup defaults.
PULSE_SOURCE_PROJECT_KEY = os.getenv("PULSE_SOURCE_PROJECT_KEY", "DATA_COLLECTION")
PULSE_GOLD_TABLES_FOLDER_ID = os.getenv("PULSE_GOLD_TABLES_FOLDER_ID", "")
PULSE_GOLD_TABLES_FOLDER_NAME = os.getenv("PULSE_GOLD_TABLES_FOLDER_NAME", "gold_data")


def ensure_duckdb_parent_dir() -> None:
    DUCKDB_PATH.parent.mkdir(parents=True, exist_ok=True)
