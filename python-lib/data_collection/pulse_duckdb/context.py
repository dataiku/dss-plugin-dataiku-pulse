from __future__ import annotations

from pathlib import Path

from .constants import db_path as resolve_db_path
from shared_duckdb.context import StorageContext, build_storage_context


def db_path() -> Path:
    """Backward-compatible alias for `data_collection.pulse_duckdb.constants.db_path`."""

    return resolve_db_path()
