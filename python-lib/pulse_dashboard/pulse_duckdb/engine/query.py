"""Query helpers for DuckDB."""

from __future__ import annotations

import pandas as pd

from .create_conn import create_connection


def query_df(sql: str, params: object | None = None) -> pd.DataFrame:
    conn = create_connection(read_only=True)
    try:
        if params is None:
            return conn.execute(sql).df()
        return conn.execute(sql, params).df()
    finally:
        conn.close()
