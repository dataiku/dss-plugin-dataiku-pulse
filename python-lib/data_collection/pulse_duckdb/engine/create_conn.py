from __future__ import annotations

import logging
from pathlib import Path

import duckdb

from ..constants import db_path as resolve_db_path


logger = logging.getLogger(__name__)


def reset_duckdb(*, path: Path | None = None, project_key: str | None = None) -> None:
    path = path or resolve_db_path(project_key=project_key)
    if path.exists():
        path.unlink()

    path.parent.mkdir(parents=True, exist_ok=True)


def create_connection(
    *,
    read_only: bool,
    path: Path | None = None,
    project_key: str | None = None,
) -> duckdb.DuckDBPyConnection:
    path = path or resolve_db_path(project_key=project_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path), read_only=read_only)
