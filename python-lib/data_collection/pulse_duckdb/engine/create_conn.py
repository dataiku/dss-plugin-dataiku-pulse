"""Compat shim: real implementation lives in `shared_duckdb.create_conn`.

Kept for import stability of existing callers; add new code to shared_duckdb.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from shared_duckdb.create_conn import create_connection as shared_create_connection
from shared_duckdb.create_conn import reset_duckdb as shared_reset_duckdb


def reset_duckdb(*, path: Path | None = None, project_key: str | None = None, purpose: str = "default") -> None:
    shared_reset_duckdb(path=path, project_key=project_key, purpose=purpose)


def create_connection(
    *,
    read_only: bool,
    path: Path | None = None,
    project_key: str | None = None,
    purpose: str = "default",
) -> duckdb.DuckDBPyConnection:
    return shared_create_connection(read_only=read_only, path=path, project_key=project_key, purpose=purpose)
