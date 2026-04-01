from __future__ import annotations

import os
import re
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DB_PATH_DEFAULT = Path("/tmp/duckdb/pulse.duckdb")

DB_PATH_ENV = "PULSE_DUCKDB_PATH"
DB_DIR_ENV = "PULSE_DUCKDB_DIR"

_SAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_filename(value: str) -> str:
    cleaned = _SAFE_CHARS_RE.sub("_", value).strip("_")
    return cleaned or "default"


def db_path(*, project_key: str | None = None) -> Path:
    """Resolve the DuckDB file path.

    Priority:
    1) `PULSE_DUCKDB_PATH` explicit file path
    2) `PULSE_DUCKDB_DIR` directory + default filename
    3) fallback to `/tmp/duckdb/pulse.duckdb`

    If `project_key` is provided and no explicit path is set, it is appended to
    the default filename stem for isolation across runs/projects.
    """

    explicit = os.environ.get(DB_PATH_ENV)
    if explicit:
        return Path(explicit)

    base_dir = Path(os.environ.get(DB_DIR_ENV, str(DB_PATH_DEFAULT.parent)))
    default_name = DB_PATH_DEFAULT.name

    if project_key:
        stem = Path(default_name).stem
        suffix = Path(default_name).suffix
        default_name = f"{stem}_{_safe_filename(project_key)}{suffix}"

    return base_dir / default_name
