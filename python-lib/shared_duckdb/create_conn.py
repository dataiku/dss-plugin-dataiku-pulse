from __future__ import annotations

import os
import tempfile
from pathlib import Path

import duckdb

from .pathing import resolve_db_path


def _resolve_temp_directory() -> str:
    temp_dir = Path(tempfile.gettempdir()) / "pulse"
    temp_dir.mkdir(parents=True, exist_ok=True)
    return str(temp_dir)


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
        "temp_directory": _resolve_temp_directory(),
    }
    if memory_limit_bytes > 0:
        memory_limit_gib = max(1, int((memory_limit_bytes * 0.8) / (1024**3)))
        config["memory_limit"] = f"{memory_limit_gib}GB"
    return config


def reset_duckdb(*, path: Path | None = None, project_key: str | None = None, purpose: str = "default") -> None:
    resolved = path or resolve_db_path(project_key=project_key, purpose=purpose)
    if resolved.exists():
        resolved.unlink()
    resolved.parent.mkdir(parents=True, exist_ok=True)


def create_connection(
    *,
    read_only: bool,
    path: Path | None = None,
    project_key: str | None = None,
    purpose: str = "default",
) -> duckdb.DuckDBPyConnection:
    resolved = path or resolve_db_path(project_key=project_key, purpose=purpose)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(resolved), read_only=read_only, config=_connect_config())
