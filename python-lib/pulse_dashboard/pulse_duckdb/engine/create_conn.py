"""Connection helpers for DuckDB."""

from __future__ import annotations

import duckdb

from ...settings import DUCKDB_PATH, DUCKDB_READ_ONLY, ensure_duckdb_parent_dir
from .init_state import is_initialization_in_progress
from shared_duckdb.bootstrap import prepare_duckdb as shared_prepare_duckdb


def create_connection(read_only: bool | None = None) -> duckdb.DuckDBPyConnection:
    """Create a dashboard DuckDB connection.

    Default read-only behavior is controlled by `settings.DUCKDB_READ_ONLY`.
    Low-level DuckDB config is delegated to the shared bootstrap layer.
    """
    ensure_duckdb_parent_dir()

    if read_only is None:
        read_only = DUCKDB_READ_ONLY

    effective_read_only = bool(read_only)
    if effective_read_only and is_initialization_in_progress():
        effective_read_only = False

    bootstrap = shared_prepare_duckdb(
        read_only=effective_read_only,
        db_path=DUCKDB_PATH,
        purpose="dashboard",
        configure_storage_access=False,
    )
    return bootstrap.conn
