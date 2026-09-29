from __future__ import annotations

import duckdb
import pytest

from data_collection.pulse_duckdb import user_activity


@pytest.fixture()
def conn(monkeypatch):
    connection = duckdb.connect(database=":memory:")
    connection.execute(
        """
        CREATE TABLE source_user_activity (
          timestamp TIMESTAMP,
          instance_name VARCHAR,
          login VARCHAR,
          project_key VARCHAR,
          viewing_actions_count BIGINT,
          developing_actions_count BIGINT
        );
        """
    )
    connection.execute(
        """
        INSERT INTO source_user_activity VALUES
          ('2026-09-09 23:59:59', 'inst', ' Alice ', 'P1', 100, 100),
          ('2026-09-10 00:00:00', 'inst', ' Alice ', 'P1', 1, 2),
          ('2026-09-10 12:00:00', 'inst', 'alice', 'P1', 3, 4),
          ('2026-09-11 09:00:00', 'inst', 'Bob', '', 5, 6),
          ('2026-09-11 10:00:00', 'inst', 'Bob', 'P2', 7, 8);
        """
    )
    monkeypatch.setattr(
        user_activity,
        "create_silver_view",
        lambda **_kwargs: ("source_user_activity", None),
    )
    try:
        yield connection
    finally:
        connection.close()


def _daily_rows(connection: duckdb.DuckDBPyConnection) -> list[tuple]:
    return connection.execute(
        """
        SELECT day, instance_name, login_norm, login, viewing_actions_count, developing_actions_count, last_activity_at
        FROM fact_user_activity_daily
        ORDER BY day, login_norm;
        """
    ).fetchall()


def _project_rows(connection: duckdb.DuckDBPyConnection) -> list[tuple]:
    return connection.execute(
        """
        SELECT day, instance_name, login_norm, login, project_key, viewing_actions_count, developing_actions_count, last_activity_at
        FROM fact_user_activity_project_daily
        ORDER BY day, login_norm, project_key;
        """
    ).fetchall()


def test_bounded_daily_build_excludes_rows_before_timestamp_boundary(conn):
    user_activity.build_fact_user_activity_daily(
        conn,
        ctx=object(),
        adjusted_watermark="2026-09-10T00:00:00",
    )

    assert _daily_rows(conn) == [
        (duckdb.execute("SELECT DATE '2026-09-10'").fetchone()[0], "inst", "alice", "Alice", 4, 6, duckdb.execute("SELECT TIMESTAMP '2026-09-10 12:00:00'").fetchone()[0]),
        (duckdb.execute("SELECT DATE '2026-09-11'").fetchone()[0], "inst", "bob", "Bob", 12, 14, duckdb.execute("SELECT TIMESTAMP '2026-09-11 10:00:00'").fetchone()[0]),
    ]


def test_bounded_project_build_excludes_old_rows_and_empty_projects(conn):
    user_activity.build_fact_user_activity_project_daily(
        conn,
        ctx=object(),
        adjusted_watermark="2026-09-10T00:00:00",
    )

    assert _project_rows(conn) == [
        (duckdb.execute("SELECT DATE '2026-09-10'").fetchone()[0], "inst", "alice", "Alice", "P1", 4, 6, duckdb.execute("SELECT TIMESTAMP '2026-09-10 12:00:00'").fetchone()[0]),
        (duckdb.execute("SELECT DATE '2026-09-11'").fetchone()[0], "inst", "bob", "Bob", "P2", 7, 8, duckdb.execute("SELECT TIMESTAMP '2026-09-11 10:00:00'").fetchone()[0]),
    ]


def test_bounded_builds_match_full_build_for_selected_window(conn):
    user_activity.build_fact_user_activity_daily(conn, ctx=object())
    full_daily_slice = conn.execute(
        """
        SELECT * FROM fact_user_activity_daily
        WHERE day >= DATE '2026-09-10'
        ORDER BY day, login_norm;
        """
    ).fetchall()
    user_activity.build_fact_user_activity_project_daily(conn, ctx=object())
    full_project_slice = conn.execute(
        """
        SELECT * FROM fact_user_activity_project_daily
        WHERE day >= DATE '2026-09-10'
        ORDER BY day, login_norm, project_key;
        """
    ).fetchall()

    user_activity.build_fact_user_activity_daily(
        conn,
        ctx=object(),
        adjusted_watermark="2026-09-10T00:00:00",
    )
    user_activity.build_fact_user_activity_project_daily(
        conn,
        ctx=object(),
        adjusted_watermark="2026-09-10T00:00:00",
    )

    assert _daily_rows(conn) == full_daily_slice
    assert _project_rows(conn) == full_project_slice


def test_no_boundary_preserves_full_build_result(conn):
    user_activity.build_fact_user_activity_daily(conn, ctx=object())
    user_activity.build_fact_user_activity_project_daily(conn, ctx=object())

    assert len(_daily_rows(conn)) == 3
    assert len(_project_rows(conn)) == 3
