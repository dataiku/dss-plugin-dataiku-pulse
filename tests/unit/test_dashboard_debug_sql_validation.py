from __future__ import annotations

import pytest

from pulse_dashboard.webapp_backend.full_backend import (
    RequestValidationError,
    _validate_read_only_debug_sql,
)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT replace('a-b', '-', '_')",
        "WITH c AS (SELECT 1 AS n) SELECT * FROM c",
        "SHOW TABLES",
        "DESCRIBE (SELECT 1 AS x)",
        "EXPLAIN SELECT 1",
    ],
)
def test_validate_read_only_debug_sql_accepts_read_queries(sql):
    assert _validate_read_only_debug_sql(sql) == sql


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("WITH doomed AS (DELETE FROM t RETURNING 1) SELECT * FROM doomed", "Invalid SQL query:"),
        ("COPY t TO 'out.csv'", "Only read-only SELECT/SHOW/DESCRIBE/EXPLAIN queries are allowed"),
        ("SELECT 1; DROP TABLE t", "Only a single SQL statement is allowed"),
    ],
)
def test_validate_read_only_debug_sql_rejects_non_read_queries(sql, message):
    with pytest.raises(RequestValidationError, match=message):
        _validate_read_only_debug_sql(sql)


def test_validate_read_only_debug_sql_reports_invalid_sql():
    with pytest.raises(RequestValidationError, match="Invalid SQL query:"):
        _validate_read_only_debug_sql("SELECT FROM")
