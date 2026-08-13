from __future__ import annotations

import contextlib
import logging
import time
from pathlib import Path

import duckdb


logger = logging.getLogger(__name__)


def canonical_norm_sql(expr: str) -> str:
    return (
        "regexp_replace(" 
        f"replace(replace(lower(trim({expr})), ' ', '_'), '-', '_'),"
        " '_+', '_', 'g')"
    )


def log_table_stats(conn: duckdb.DuckDBPyConnection, table_name: str) -> None:
    row = conn.execute(f'SELECT COUNT(*) FROM "{table_name}";').fetchone()  # nosec B608 (table_name is internal)
    logger.info("Table %s rows=%s", table_name, int(row[0] or 0) if row else 0)


def current_rss_mb() -> float | None:
    status_path = Path("/proc/self/status")
    try:
        for line in status_path.read_text(encoding="utf-8").splitlines():
            if not line.startswith("VmRSS:"):
                continue
            parts = line.split()
            if len(parts) < 2:
                return None
            return round(int(parts[1]) / 1024.0, 1)
    except Exception:
        return None
    return None


def duckdb_runtime_setting(conn: duckdb.DuckDBPyConnection, setting_name: str) -> str | None:
    try:
        row = conn.execute(f"SELECT current_setting('{setting_name}')").fetchone()
    except Exception:
        return None
    if not row or row[0] is None:
        return None
    return str(row[0])


def log_phase_snapshot(conn: duckdb.DuckDBPyConnection, *, label: str, phase: str, elapsed_seconds: float | None = None) -> None:
    logger.info(
        "GOLD phase %s: label=%s elapsed_seconds=%s rss_mb=%s duckdb_memory_limit=%s duckdb_threads=%s",
        phase,
        label,
        None if elapsed_seconds is None else round(elapsed_seconds, 3),
        current_rss_mb(),
        duckdb_runtime_setting(conn, "memory_limit"),
        duckdb_runtime_setting(conn, "threads"),
    )


@contextlib.contextmanager
def log_timed_phase(conn: duckdb.DuckDBPyConnection, *, label: str):
    started = time.monotonic()
    log_phase_snapshot(conn, label=label, phase="start")
    try:
        yield
    finally:
        log_phase_snapshot(conn, label=label, phase="end", elapsed_seconds=time.monotonic() - started)
