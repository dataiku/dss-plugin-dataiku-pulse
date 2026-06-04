"""Connection helpers for DuckDB."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import duckdb

from ...settings import DUCKDB_PATH, DUCKDB_READ_ONLY, ensure_duckdb_parent_dir
from .init_state import is_initialization_in_progress


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


def create_connection(read_only: bool | None = None) -> duckdb.DuckDBPyConnection:
    """Create a DuckDB connection.

    Default read-only behavior is controlled by `settings.DUCKDB_READ_ONLY`.
    """
    ensure_duckdb_parent_dir()

    if read_only is None:
        read_only = DUCKDB_READ_ONLY

    effective_read_only = bool(read_only)
    if effective_read_only and is_initialization_in_progress():
        effective_read_only = False

    if DUCKDB_PATH.exists() and not effective_read_only:
        try:
            with duckdb.connect(str(DUCKDB_PATH), read_only=True, config=_connect_config()) as probe_conn:
                probe_conn.execute("SELECT 1")
            effective_read_only = True
        except duckdb.ConnectionException:
            pass

    return duckdb.connect(str(DUCKDB_PATH), read_only=effective_read_only, config=_connect_config())
