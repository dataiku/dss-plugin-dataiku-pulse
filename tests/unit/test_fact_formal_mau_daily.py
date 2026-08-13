from __future__ import annotations

from datetime import date

import duckdb
import pytest


@pytest.fixture()
def conn():
    connection = duckdb.connect(database=":memory:")
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture()
def seeded_view(conn):
    conn.execute(
        """
        CREATE OR REPLACE VIEW v_users_formal_mau__formal_mau AS
        SELECT * FROM (
          VALUES
            (TIMESTAMP '2026-08-01 09:00:00', 'inst_a', 'Alice', '1'),
            (TIMESTAMP '2026-08-01 15:00:00', 'inst_a', ' alice ', '2'),
            (TIMESTAMP '2026-08-01 11:00:00', 'inst_b', 'Alice', '4'),
            (TIMESTAMP '2026-08-02 08:00:00', 'inst_a', 'Alice', '3'),
            (TIMESTAMP '2026-08-01 13:00:00', 'inst_a', '', '9'),
            (TIMESTAMP '2026-08-01 14:00:00', 'inst_a', NULL, '9'),
            (NULL, 'inst_a', 'Alice', '9')
        ) AS t(timestamp, instance_name, login, application_open_count)
        """.strip()
    )
    return conn



def _build_fact_formal_mau_daily_for_test(conn: duckdb.DuckDBPyConnection) -> str:
    conn.execute(
        """
        CREATE OR REPLACE TABLE fact_formal_mau_daily AS
        SELECT
          CAST(date_trunc('day', timestamp) AS DATE) AS day,
          instance_name,
          lower(trim(login)) AS login_norm,
          MIN(trim(login)) AS login,
          SUM(COALESCE(try_cast(application_open_count AS BIGINT), 0)) AS application_open_count,
          MAX(timestamp) AS last_application_open_at
        FROM v_users_formal_mau__formal_mau
        WHERE timestamp IS NOT NULL
          AND login IS NOT NULL
          AND length(trim(login)) > 0
        GROUP BY 1, 2, 3
        """.strip()
    )
    return "fact_formal_mau_daily"



def test_fact_formal_mau_daily_preserves_contract_and_columns(seeded_view):
    conn = seeded_view
    table_name = _build_fact_formal_mau_daily_for_test(conn)
    columns = conn.execute("DESCRIBE fact_formal_mau_daily").fetchall()

    assert table_name == "fact_formal_mau_daily"
    assert [row[0] for row in columns] == [
        "day",
        "instance_name",
        "login_norm",
        "login",
        "application_open_count",
        "last_application_open_at",
    ]



def test_multiple_hourly_rows_same_user_instance_day_are_summed(seeded_view):
    conn = seeded_view
    _build_fact_formal_mau_daily_for_test(conn)

    row = conn.execute(
        """
        SELECT application_open_count
        FROM fact_formal_mau_daily
        WHERE day = DATE '2026-08-01'
          AND instance_name = 'inst_a'
          AND login_norm = 'alice'
        """.strip()
    ).fetchone()

    assert row == (3,)



def test_latest_hourly_timestamp_becomes_last_application_open_at(seeded_view):
    conn = seeded_view
    _build_fact_formal_mau_daily_for_test(conn)

    row = conn.execute(
        """
        SELECT last_application_open_at
        FROM fact_formal_mau_daily
        WHERE day = DATE '2026-08-01'
          AND instance_name = 'inst_a'
          AND login_norm = 'alice'
        """.strip()
    ).fetchone()

    assert str(row[0]) == "2026-08-01 15:00:00"



def test_same_login_on_two_instances_creates_two_rows(seeded_view):
    conn = seeded_view
    _build_fact_formal_mau_daily_for_test(conn)

    rows = conn.execute(
        """
        SELECT day, instance_name, login_norm, application_open_count
        FROM fact_formal_mau_daily
        WHERE day = DATE '2026-08-01'
          AND login_norm = 'alice'
        ORDER BY instance_name
        """.strip()
    ).fetchall()

    assert rows == [
        (date(2026, 8, 1), "inst_a", "alice", 3),
        (date(2026, 8, 1), "inst_b", "alice", 4),
    ]



def test_login_normalization_merges_case_and_whitespace_variants(seeded_view):
    conn = seeded_view
    _build_fact_formal_mau_daily_for_test(conn)

    rows = conn.execute(
        """
        SELECT day, instance_name, login_norm, login, application_open_count
        FROM fact_formal_mau_daily
        WHERE instance_name = 'inst_a'
        ORDER BY day
        """.strip()
    ).fetchall()

    assert rows == [
        (date(2026, 8, 1), "inst_a", "alice", "Alice", 3),
        (date(2026, 8, 2), "inst_a", "alice", "Alice", 3),
    ]



def test_blank_or_null_logins_and_null_timestamps_are_excluded(seeded_view):
    conn = seeded_view
    _build_fact_formal_mau_daily_for_test(conn)

    row_count = conn.execute("SELECT COUNT(*) FROM fact_formal_mau_daily").fetchone()[0]
    excluded_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM fact_formal_mau_daily
        WHERE login_norm IS NULL OR length(trim(login_norm)) = 0
        """.strip()
    ).fetchone()[0]

    assert row_count == 3
    assert excluded_count == 0
