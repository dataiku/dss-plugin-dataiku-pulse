from __future__ import annotations

from pathlib import Path

from shared_duckdb.pathing import resolve_db_path

BASE_DIR = Path(__file__).resolve().parent


def db_path(*, project_key: str | None = None, purpose: str = "default") -> Path:
    return resolve_db_path(project_key=project_key, purpose=purpose)
