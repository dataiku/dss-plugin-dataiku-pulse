from __future__ import annotations

import duckdb


def list_table_names(conn: duckdb.DuckDBPyConnection) -> list[str]:
    return sorted(name for (name,) in conn.sql("SHOW TABLES").fetchall())
