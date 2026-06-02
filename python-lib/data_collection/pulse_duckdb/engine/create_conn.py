from __future__ import annotations

import logging
import os
from pathlib import Path

import duckdb

from ..constants import db_path as resolve_db_path


logger = logging.getLogger(__name__)


def _connect_config() -> dict[str, str]:
    cpu_count = os.cpu_count() or 2
    duckdb_threads = max(1, cpu_count - 1)

    try:
        memory_limit_raw = Path("/sys/fs/cgroup/memory.max").read_text(encoding="utf-8").strip()
        memory_limit_bytes = 0 if memory_limit_raw == "max" else int(memory_limit_raw)
    except Exception:
        memory_limit_bytes = 0

    config: dict[str, str] = {
        "threads": str(duckdb_threads),
        "temp_directory": "/tmp/pulse",
    }
    if memory_limit_bytes > 0:
        memory_limit_gib = max(1, int((memory_limit_bytes * 0.8) / (1024**3)))
        config["memory_limit"] = f"{memory_limit_gib}GB"
    return config


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
    return duckdb.connect(str(path), read_only=read_only, config=_connect_config())
