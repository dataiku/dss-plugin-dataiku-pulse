"""Connection helpers for DuckDB."""

from __future__ import annotations

import duckdb

from ...settings import DUCKDB_PATH, DUCKDB_READ_ONLY, ensure_duckdb_parent_dir


def create_connection(read_only: bool | None = None) -> duckdb.DuckDBPyConnection:
    """Create a DuckDB connection.

    Default read-only behavior is controlled by `settings.DUCKDB_READ_ONLY`.
    """
    ensure_duckdb_parent_dir()

    if read_only is None:
        read_only = DUCKDB_READ_ONLY

    return duckdb.connect(str(DUCKDB_PATH), read_only=read_only)
