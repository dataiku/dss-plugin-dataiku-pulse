"""Connection helpers for DuckDB.

All dashboard code shares ONE DuckDB database handle per process, handed out
as cursors. DuckDB refuses to open the same file twice in one process with a
different configuration (e.g. the read_only flag flipped), so per-request
``duckdb.connect`` calls with mixed read_only values raced each other into
intermittent "Can't open a connection to same database file with a different
configuration than existing connections" errors whenever a query overlapped a
gold refresh.
"""

from __future__ import annotations

import logging
import threading

import duckdb

from ...settings import DUCKDB_PATH, DUCKDB_READ_ONLY, ensure_duckdb_parent_dir
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
            ensure_duckdb_parent_dir()
            bootstrap = shared_prepare_duckdb(
                read_only=DUCKDB_READ_ONLY,
                db_path=DUCKDB_PATH,
                purpose="dashboard",
                configure_storage_access=False,
            )
            _master_conn = bootstrap.conn
        return _master_conn


def create_connection(read_only: bool | None = None) -> duckdb.DuckDBPyConnection:
    """Return a cursor on the process-wide shared DuckDB connection.

    ``read_only`` is kept for call-site compatibility but no longer selects a
    connection mode: the shared connection is writable unless the deployment
    sets ``PULSE_DUCKDB_READ_ONLY``, and read-only callers simply don't write.
    Requesting a writable handle on a read-only deployment raises.

    Callers may (and should) ``close()`` the returned cursor; the shared
    connection stays open for the life of the process.
    """
    if DUCKDB_READ_ONLY and read_only is False:
        raise RuntimeError(
            "This deployment is read-only (PULSE_DUCKDB_READ_ONLY is set); "
            "a writable DuckDB connection was requested"
        )
    return _master().cursor()
