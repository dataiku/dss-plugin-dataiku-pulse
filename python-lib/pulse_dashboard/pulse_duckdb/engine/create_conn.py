"""Connection helpers for DuckDB.

All dashboard code shares ONE DuckDB handle per process, handed out as
cursors: DuckDB refuses to open the same file twice in one process with
mixed read_only flags, so per-request connects raced into intermittent
"different configuration" errors. Because the shared handle is writable,
read-only callers must go through ``query.query_df`` / ``assert_read_only_sql``,
which reject non-read statements.
"""

from __future__ import annotations

import logging
import threading

import duckdb

from ... import settings
from shared_duckdb.bootstrap import prepare_duckdb as shared_prepare_duckdb

logger = logging.getLogger(__name__)

_master_lock = threading.Lock()
_master_conn: duckdb.DuckDBPyConnection | None = None


def _master() -> duckdb.DuckDBPyConnection:
    global _master_conn
    with _master_lock:
        if _master_conn is not None:
            try:
                _master_conn.execute("SELECT 1")
            except Exception:
                logger.warning("Shared DuckDB connection unusable; reopening", exc_info=True)
                try:
                    _master_conn.close()
                except Exception:
                    pass
                _master_conn = None
        if _master_conn is None:
            settings.DUCKDB_PATH = settings.resolve_duckdb_path()
            settings.DUCKDB_METADATA_PATH = settings.DUCKDB_PATH.with_suffix(f"{settings.DUCKDB_PATH.suffix}.meta.json")
            settings.ensure_duckdb_parent_dir()
            bootstrap = shared_prepare_duckdb(
                project_key=settings.resolve_source_project_key(),
                folder_lookup=settings.PULSE_GOLD_TABLES_FOLDER_ID or settings.PULSE_GOLD_TABLES_FOLDER_NAME,
                read_only=settings.DUCKDB_READ_ONLY,
                db_path=settings.DUCKDB_PATH,
                purpose="dashboard",
                configure_storage_access=True,
            )
            _master_conn = bootstrap.conn
        return _master_conn


def create_connection(read_only: bool | None = None) -> duckdb.DuckDBPyConnection:
    """Return a cursor on the process-wide shared DuckDB connection.

    ``read_only`` does NOT select a connection mode — the cursor is writable
    unless the deployment sets ``PULSE_DUCKDB_READ_ONLY`` (in which case
    requesting a writable handle raises). Callers needing enforced read-only
    execution must use ``query.query_df``. Callers should ``close()`` the
    cursor; the shared connection stays open for the life of the process.
    """
    if settings.DUCKDB_READ_ONLY and read_only is False:
        raise RuntimeError(
            "This deployment is read-only (PULSE_DUCKDB_READ_ONLY is set); "
            "a writable DuckDB connection was requested"
        )
    return _master().cursor()
