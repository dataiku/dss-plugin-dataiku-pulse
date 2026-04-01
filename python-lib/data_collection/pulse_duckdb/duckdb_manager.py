from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from .context import StorageContext
from .engine.create_conn import create_connection, reset_duckdb
from .engine.storage_config import configure_storage


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


@dataclass(frozen=True)
class DuckDBSetupResult:
    conn: duckdb.DuckDBPyConnection
    db_path: Path
    provider: str
    credential_mode: str | None


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

    if reset:
        reset_duckdb(path=db_path, project_key=ctx.project_key)

    conn = create_connection(read_only=read_only, path=db_path, project_key=ctx.project_key)
    try:
        storage_info = configure_storage(conn, ctx=ctx)
    except Exception:
        conn.close()
        raise

    return DuckDBSetupResult(
        conn=conn,
        db_path=Path(conn.sql("PRAGMA database_list").fetchall()[0][2]),
        provider=str(storage_info.get("provider")),
        credential_mode=storage_info.get("credential_mode"),
    )
