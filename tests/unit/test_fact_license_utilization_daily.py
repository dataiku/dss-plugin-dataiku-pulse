from __future__ import annotations

from datetime import date

import duckdb
import pytest

from data_collection.pulse_duckdb.license_utilization import _build_fact_license_utilization_daily_from_views


@pytest.fixture()
def conn():
    connection = duckdb.connect(database=":memory:")
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture()
def seeded_views(conn):
    conn.execute(
        """
        CREATE OR REPLACE VIEW v_license__max_licenses AS
        SELECT * FROM (
          VALUES
            ('inst_a', 'FULL_DESIGNER', '100', TIMESTAMPTZ '2026-08-01 09:00:00+00:00', NULL, 2026, 8, 1, DATE '2026-08-01'),
            ('inst_a', 'FULL_DESIGNER', '120', TIMESTAMPTZ '2026-08-01 18:00:00+00:00', NULL, 2026, 8, 1, DATE '2026-08-01'),
            ('inst_a', 'DESIGNER', '0', TIMESTAMPTZ '2026-08-01 12:00:00+00:00', NULL, 2026, 8, 1, DATE '2026-08-01'),
            ('inst_a', 'FULL_DESIGNER', '100', TIMESTAMPTZ '2026-08-02 09:00:00+00:00', NULL, 2026, 8, 2, DATE '2026-08-02'),
            ('inst_b', 'FULL_DESIGNER', '50', TIMESTAMPTZ '2026-08-01 10:00:00+00:00', NULL, 2026, 8, 1, DATE '2026-08-01'),
            ('inst_b', 'DESIGNER', NULL, TIMESTAMPTZ '2026-08-01 10:30:00+00:00', NULL, 2026, 8, 1, DATE '2026-08-01')
        ) AS t(instance_name, license_profile, max_licenses, run_ts, extras, year, month, day, partition_date)
        """.strip()
    )
    conn.execute(
        """
        CREATE OR REPLACE VIEW v_users__instance_metadata AS
        SELECT * FROM (
          VALUES
            ('inst_a', 'alice', 'True', 'FULL_DESIGNER', TIMESTAMPTZ '2026-08-01 08:00:00+00:00', 2026, 8, 1, DATE '2026-08-01'),
            ('inst_a', 'alice', 'True', 'FULL_DESIGNER', TIMESTAMPTZ '2026-08-01 17:30:00+00:00', 2026, 8, 1, DATE '2026-08-01'),
            ('inst_a', 'bob',   'True', 'FULL_DESIGNER', TIMESTAMPTZ '2026-08-01 17:45:00+00:00', 2026, 8, 1, DATE '2026-08-01'),
            ('inst_a', 'carl',  'False','FULL_DESIGNER', TIMESTAMPTZ '2026-08-01 17:50:00+00:00', 2026, 8, 1, DATE '2026-08-01'),
            ('inst_a', 'dana',  'True', 'DESIGNER',      TIMESTAMPTZ '2026-08-01 17:55:00+00:00', 2026, 8, 1, DATE '2026-08-01'),
            ('inst_a', 'erin',  'True', 'FULL_DESIGNER', TIMESTAMPTZ '2026-08-02 09:15:00+00:00', 2026, 8, 2, DATE '2026-08-02'),
            ('inst_b', 'alice', 'True', 'FULL_DESIGNER', TIMESTAMPTZ '2026-08-01 09:00:00+00:00', 2026, 8, 1, DATE '2026-08-01'),
            ('inst_b', 'zoe',   'True', 'DESIGNER',      TIMESTAMPTZ '2026-08-01 09:30:00+00:00', 2026, 8, 1, DATE '2026-08-01')
        ) AS t(instance_name, users_login, users_enabled, users_userprofile, run_ts, year, month, day, partition_date)
        """.strip()
    )
    return conn


def _build(conn: duckdb.DuckDBPyConnection) -> str:
    return _build_fact_license_utilization_daily_from_views(
        conn,
        max_licenses_view='v_license__max_licenses',
        users_view='v_users__instance_metadata',
    )


def test_fact_license_utilization_daily_preserves_contract_and_columns(seeded_views):
    conn = seeded_views
    table_name = _build(conn)
    columns = conn.execute('DESCRIBE fact_license_utilization_daily').fetchall()

    assert table_name == 'fact_license_utilization_daily'
    assert [row[0] for row in columns] == [
        'snapshot_date',
        'instance_name',
        'license_profile',
        'entitled_count',
        'assigned_count',
        'available_count',
        'utilization_pct',
        'run_ts',
    ]


def test_latest_run_per_instance_day_profile_is_authoritative(seeded_views):
    conn = seeded_views
    _build(conn)

    row = conn.execute(
        """
        SELECT entitled_count, assigned_count, available_count, CAST(run_ts AS VARCHAR)
        FROM fact_license_utilization_daily
        WHERE snapshot_date = DATE '2026-08-01'
          AND instance_name = 'inst_a'
          AND license_profile = 'FULL_DESIGNER'
        """.strip()
    ).fetchone()

    assert row == (120, 2, 118, '2026-08-01 18:00:00')


def test_designer_and_full_designer_remain_distinct(seeded_views):
    conn = seeded_views
    _build(conn)

    rows = conn.execute(
        """
        SELECT license_profile, entitled_count, assigned_count, available_count, utilization_pct
        FROM fact_license_utilization_daily
        WHERE snapshot_date = DATE '2026-08-01'
          AND instance_name = 'inst_a'
        ORDER BY license_profile
        """.strip()
    ).fetchall()

    assert rows == [
        ('DESIGNER', 0, 1, -1, None),
        ('FULL_DESIGNER', 120, 2, 118, pytest.approx(1.6666666667)),
    ]


def test_profiles_without_entitlement_cap_remain_visible(seeded_views):
    conn = seeded_views
    _build(conn)

    row = conn.execute(
        """
        SELECT entitled_count, assigned_count, available_count, utilization_pct
        FROM fact_license_utilization_daily
        WHERE snapshot_date = DATE '2026-08-01'
          AND instance_name = 'inst_b'
          AND license_profile = 'DESIGNER'
        """.strip()
    ).fetchone()

    assert row == (None, 1, None, None)


def test_multiple_instances_remain_isolated(seeded_views):
    conn = seeded_views
    _build(conn)

    rows = conn.execute(
        """
        SELECT instance_name, license_profile, assigned_count
        FROM fact_license_utilization_daily
        WHERE snapshot_date = DATE '2026-08-01'
          AND license_profile = 'FULL_DESIGNER'
        ORDER BY instance_name
        """.strip()
    ).fetchall()

    assert rows == [
        ('inst_a', 'FULL_DESIGNER', 2),
        ('inst_b', 'FULL_DESIGNER', 1),
    ]


def test_snapshot_date_comes_from_run_ts_calendar_date(seeded_views):
    conn = seeded_views
    _build(conn)

    rows = conn.execute(
        """
        SELECT DISTINCT snapshot_date
        FROM fact_license_utilization_daily
        ORDER BY snapshot_date
        """.strip()
    ).fetchall()

    assert rows == [
        (date(2026, 8, 1),),
        (date(2026, 8, 2),),
    ]


def test_no_duplicate_instance_snapshot_profile_keys(seeded_views):
    conn = seeded_views
    _build(conn)

    row = conn.execute(
        """
        SELECT
          COUNT(*),
          COUNT(DISTINCT concat(instance_name, '::', CAST(snapshot_date AS VARCHAR), '::', license_profile))
        FROM fact_license_utilization_daily
        """.strip()
    ).fetchone()

    assert row == (5, 5)


def test_zero_or_null_entitlement_yields_null_utilization(seeded_views):
    conn = seeded_views
    _build(conn)

    rows = conn.execute(
        """
        SELECT license_profile, utilization_pct
        FROM fact_license_utilization_daily
        WHERE snapshot_date = DATE '2026-08-01'
          AND instance_name = 'inst_a'
          AND license_profile = 'DESIGNER'
        UNION ALL
        SELECT license_profile, utilization_pct
        FROM fact_license_utilization_daily
        WHERE snapshot_date = DATE '2026-08-01'
          AND instance_name = 'inst_b'
          AND license_profile = 'DESIGNER'
        ORDER BY license_profile
        """.strip()
    ).fetchall()

    assert rows == [
        ('DESIGNER', None),
        ('DESIGNER', None),
    ]


def test_day_two_assignment_without_duplicate_day_one_users(seeded_views):
    conn = seeded_views
    _build(conn)

    row = conn.execute(
        """
        SELECT entitled_count, assigned_count, available_count, utilization_pct
        FROM fact_license_utilization_daily
        WHERE snapshot_date = DATE '2026-08-02'
          AND instance_name = 'inst_a'
          AND license_profile = 'FULL_DESIGNER'
        """.strip()
    ).fetchone()

    assert row == (100, 1, 99, pytest.approx(1.0))
