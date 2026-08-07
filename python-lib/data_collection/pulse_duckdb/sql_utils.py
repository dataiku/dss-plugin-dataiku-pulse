from __future__ import annotations

import logging

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
