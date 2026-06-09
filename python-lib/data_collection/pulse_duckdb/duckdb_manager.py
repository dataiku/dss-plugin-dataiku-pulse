from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from .context import StorageContext
from shared_duckdb.bootstrap import DuckDBBootstrapResult, prepare_duckdb as shared_prepare_duckdb


def query_df(
    *,
    ctx: StorageContext,
    query: str,
    read_only: bool = False,
    reset: bool = True,
    db_path: Path | None = None,
):
    """Run a SQL query and return a pandas DataFrame.

    This is a convenience wrapper around `prepare_duckdb()` that ensures the
    connection is always closed.

    Notes:
    - Uses DuckDB's `.df()` result conversion.
    - Keeps `reset=True` by default (deterministic builds).
    """

    setup = prepare_duckdb(ctx=ctx, read_only=read_only, reset=reset, db_path=db_path)
    try:
        return setup.conn.sql(query).df()
    finally:
        setup.conn.close()


DuckDBSetupResult = DuckDBBootstrapResult


def prepare_duckdb(
    *,
    ctx: StorageContext,
    read_only: bool = False,
    reset: bool = True,
    db_path: Path | None = None,
) -> DuckDBSetupResult:
    """Create and configure a DuckDB connection for reading blob storage.

    - Creates/wipes the DB file (default).
    - Opens a connection.
    - Applies `configure_storage()` for the managed-folder backing store.

    Returns a small object containing the open connection and resolved settings.
    """

    return shared_prepare_duckdb(
        project_key=ctx.project_key,
        folder_lookup=ctx.folder_lookup,
        read_only=read_only,
        reset=reset,
        db_path=db_path,
        purpose="recipe_gold_builder",
        configure_storage_access=True,
    )
