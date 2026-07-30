"""Connection helpers for DuckDB.

This webapp intentionally shares the blob-storage configuration logic from the
Pulse plugin (`data_collection.pulse_duckdb.*`) to avoid drift.

Connection creation remains local (persistent DB file for the webapp), but we
configure blob access (S3/Azure/GCS secrets/modules) on every connection.
"""

from __future__ import annotations

import duckdb

from pulse_duckdb.engine.plugin_storage import configure_connection_for_gold
from settings import DUCKDB_PATH, DUCKDB_READ_ONLY, ensure_duckdb_parent_dir


def create_connection(read_only: bool | None = None) -> duckdb.DuckDBPyConnection:
    """Create a DuckDB connection.

    Default read-only behavior is controlled by `settings.DUCKDB_READ_ONLY`.
    """

    ensure_duckdb_parent_dir()

    if read_only is None:
        read_only = DUCKDB_READ_ONLY

    conn = duckdb.connect(str(DUCKDB_PATH), read_only=read_only)

    # Configure access to the backing store of the GOLD managed folder.
    # This is a no-op for local/demo workflows but required in real deployments.
    configure_connection_for_gold(conn)

    return conn
