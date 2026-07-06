"""Query helpers for DuckDB."""

from __future__ import annotations

import duckdb
import pandas as pd

from .create_conn import create_connection

# The shared connection is writable (see create_conn), so read-only-ness must
# be enforced per statement here. SHOW/DESCRIBE/SUMMARIZE/PRAGMA parse as
# SELECT; EXPLAIN parses without executing the inner statement.
_READ_ONLY_STATEMENT_TYPES = (
    duckdb.StatementType.SELECT,
    duckdb.StatementType.EXPLAIN,
)


class ReadOnlySQLError(ValueError):
    """A write statement was passed to the read-only query path."""


def assert_read_only_sql(sql: str) -> None:
    for stmt in duckdb.extract_statements(sql):
        if stmt.type not in _READ_ONLY_STATEMENT_TYPES:
            raise ReadOnlySQLError(
                f"Statement type {stmt.type.name} is not allowed on the "
                "read-only query path; only SELECT/EXPLAIN are accepted"
            )


def query_df(sql: str, params: object | None = None) -> pd.DataFrame:
    assert_read_only_sql(sql)
    conn = create_connection(read_only=True)
    try:
        if params is None:
            return conn.execute(sql).df()
        return conn.execute(sql, params).df()
    finally:
        conn.close()
