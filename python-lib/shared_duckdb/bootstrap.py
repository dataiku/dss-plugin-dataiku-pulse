from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from .context import StorageContext, build_storage_context
from .create_conn import create_connection, reset_duckdb
from .storage_config import configure_storage


@dataclass(frozen=True)
class DuckDBBootstrapResult:
    conn: duckdb.DuckDBPyConnection
    db_path: Path
    provider: str | None
    credential_mode: str | None
    storage_context: StorageContext | None


def prepare_duckdb(
    *,
    project_key: str | None = None,
    folder_lookup: str | None = None,
    read_only: bool = False,
    reset: bool = False,
    db_path: Path | None = None,
    purpose: str = "default",
    configure_storage_access: bool = False,
) -> DuckDBBootstrapResult:
    storage_context: StorageContext | None = None
    provider: str | None = None
    credential_mode: str | None = None

    if configure_storage_access:
        if not project_key or not folder_lookup:
            raise ValueError("project_key and folder_lookup are required when configure_storage_access=True")
        storage_context = build_storage_context(project_key=project_key, folder_lookup=folder_lookup)

    if reset:
        reset_duckdb(path=db_path, project_key=project_key, purpose=purpose)

    conn = create_connection(read_only=read_only, path=db_path, project_key=project_key, purpose=purpose)
    try:
        if storage_context is not None:
            storage_info = configure_storage(conn, ctx=storage_context)
            provider = str(storage_info.get("provider"))
            credential_mode = storage_info.get("credential_mode")
    except Exception:
        conn.close()
        raise

    return DuckDBBootstrapResult(
        conn=conn,
        db_path=Path(conn.sql("PRAGMA database_list").fetchall()[0][2]),
        provider=provider,
        credential_mode=credential_mode,
        storage_context=storage_context,
    )
