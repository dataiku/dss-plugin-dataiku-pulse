from __future__ import annotations

from datetime import datetime

import duckdb
import pytest


@pytest.fixture()
def conn():
    connection = duckdb.connect(database=":memory:")
    try:
        yield connection
    finally:
        connection.close()



def _category_norm_sql(column: str) -> str:
    return (
        "regexp_replace("
        f"replace(replace(lower(trim(COALESCE({column}, ''))), ' ', '_'), '-', '_'),"
        " '_+', '_', 'g'"
        ")"
    )



def _build_fact_object_activity_events_for_test(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        CREATE OR REPLACE TABLE unioned_events AS
        SELECT * FROM (
          VALUES
            (TIMESTAMP '2026-08-01 10:00:00', 'inst', 'alice', 'dataset-view', 'WEBAPPS', 'applications_delivery', 'P1', 'dataset', 'ds1', 'ds1', NULL, NULL, NULL, '1.1.1.1', NULL, '{"a":1}', TIMESTAMP '2026-08-02 00:00:00', 2026, 8, 1),
            (TIMESTAMP '2026-08-01 10:00:00', 'inst', 'alice', 'dataset-view', 'webapps', 'applications_delivery', 'P1', 'dataset', 'ds1', 'ds1', NULL, NULL, NULL, '1.1.1.1', NULL, '{"a":1}', TIMESTAMP '2026-08-03 00:00:00', 2026, 8, 2),
            (TIMESTAMP '2026-08-01 10:00:00', 'inst', 'bob', 'dataset-view', 'web apps', 'applications_delivery', 'P2', 'dataset', 'ds2', 'ds2', NULL, NULL, NULL, NULL, NULL, NULL, TIMESTAMP '2026-08-04 00:00:00', 2026, 8, 2),
            (TIMESTAMP '2026-08-01 10:05:00', 'inst', 'alice', 'dataset-view', 'webapps', 'applications_delivery', 'P1', 'dataset', 'ds1', 'ds1', NULL, NULL, NULL, '1.1.1.1', NULL, '{"a":1}', TIMESTAMP '2026-08-03 00:00:00', 2026, 8, 2),
            (TIMESTAMP '2026-08-01 11:00:00', 'inst', 'alice', 'recipe-save', 'Visual Recipes', 'data_engineering', 'P1', 'recipe', 'r1', 'r1', NULL, NULL, NULL, NULL, NULL, NULL, TIMESTAMP '2026-08-02 00:00:00', 2026, 8, 1),
            (TIMESTAMP '2026-08-01 12:00:00', 'inst', 'alice', 'api-call', 'apis', 'apis_integration', 'P1', 'api_service', 'svc1', 'svc1', NULL, NULL, NULL, NULL, NULL, NULL, TIMESTAMP '2026-08-02 00:00:00', 2026, 8, 1),
            (TIMESTAMP '2026-08-01 13:00:00', 'inst', 'alice', 'webapp-view', 'visual-recipes', 'data_engineering', 'P1', 'web_application', 'w1', 'w1', NULL, NULL, NULL, NULL, NULL, NULL, TIMESTAMP '2026-08-02 00:00:00', 2026, 8, 1),
            (TIMESTAMP '2026-08-01 14:00:00', 'inst', 'alice', 'dashboard-view', 'charts dashboard', 'applications_delivery', 'P1', 'dashboard', 'd1', 'd1', NULL, NULL, NULL, NULL, NULL, NULL, TIMESTAMP '2026-08-02 00:00:00', 2026, 8, 1),
            (TIMESTAMP '2026-08-01 15:00:00', 'inst', 'alice', 'app-edit', 'application_designer', 'applications_delivery', 'P1', 'dataiku_application', 'app1', 'app1', NULL, NULL, NULL, NULL, NULL, NULL, TIMESTAMP '2026-08-02 00:00:00', 2026, 8, 1)
        ) AS t(
          timestamp, instance_name, login, event_name, event_category, canonical_capability,
          project_key, object_type, object_key, object_name, instance_url, group_names,
          session_id, ip_address, user_agent, details_json, run_timestamp, year, month, day
        )
        """.strip()
    )

    conn.execute(
        """
        CREATE OR REPLACE TABLE unioned_events_typed AS
        SELECT
          timestamp,
          instance_name,
          login,
          event_name,
          event_category,
          canonical_capability,
          project_key,
          object_type,
          object_key,
          object_name,
          CAST(instance_url AS VARCHAR) AS instance_url,
          CAST(group_names AS VARCHAR) AS group_names,
          CAST(session_id AS VARCHAR) AS session_id,
          CAST(ip_address AS VARCHAR) AS ip_address,
          CAST(user_agent AS VARCHAR) AS user_agent,
          CAST(details_json AS VARCHAR) AS details_json,
          run_timestamp,
          year,
          month,
          day
        FROM unioned_events
        """.strip()
    )

    category_norm_sql = _category_norm_sql("event_category")
    sql = (
        "CREATE OR REPLACE TABLE fact_object_activity_events AS\n"  # nosec B608 -- test-only SQL built from a fixed column expression.
        "WITH ranked_events AS (\n"
        "  SELECT\n"
        "    *,\n"
        "    ROW_NUMBER() OVER (\n"
        "      PARTITION BY\n"
        "        timestamp,\n"
        "        instance_name,\n"
        "        lower(trim(COALESCE(login, ''))),\n"
        "        event_name,\n"
        f"        {category_norm_sql},\n"
        "        project_key,\n"
        "        object_type,\n"
        "        object_key,\n"
        "        ip_address,\n"
        "        details_json\n"
        "      ORDER BY run_timestamp DESC NULLS LAST\n"
        "    ) AS rn\n"
        "  FROM unioned_events_typed\n"
        ")\n"
        "SELECT\n"
        "  timestamp, instance_name, login, event_name, event_category, canonical_capability,\n"
        "  project_key, object_type, object_key, object_name, instance_url, group_names,\n"
        "  session_id, ip_address, user_agent, details_json, run_timestamp, year, month, day\n"
        "FROM ranked_events\n"
        "WHERE rn = 1"
    )
    conn.execute(sql)
    conn.execute(
        "CREATE OR REPLACE VIEW base_object_activity_events AS SELECT * FROM fact_object_activity_events"
    )



def test_dedup_and_newest_run_timestamp_win(conn):
    _build_fact_object_activity_events_for_test(conn)
    row = conn.execute(
        """
        SELECT COUNT(*), MAX(run_timestamp)
        FROM fact_object_activity_events
        WHERE object_type = 'dataset'
          AND object_key = 'ds1'
          AND event_name = 'dataset-view'
          AND timestamp = TIMESTAMP '2026-08-01 10:00:00'
        """.strip()
    ).fetchone()
    assert row == (1, datetime(2026, 8, 3, 0, 0))



def test_category_variants_collapse_but_distinct_identity_fields_do_not(conn):
    _build_fact_object_activity_events_for_test(conn)
    rows = conn.execute(
        """
        SELECT timestamp, project_key, object_key, event_category, canonical_capability
        FROM fact_object_activity_events
        WHERE object_type = 'dataset'
        ORDER BY timestamp, project_key, object_key
        """.strip()
    ).fetchall()
    assert rows == [
        (datetime(2026, 8, 1, 10, 0), 'P1', 'ds1', 'webapps', 'applications_delivery'),
        (datetime(2026, 8, 1, 10, 0), 'P2', 'ds2', 'web apps', 'applications_delivery'),
        (datetime(2026, 8, 1, 10, 5), 'P1', 'ds1', 'webapps', 'applications_delivery'),
    ]



def test_placeholder_columns_are_varchar_and_alias_matches(conn):
    _build_fact_object_activity_events_for_test(conn)
    schema = {row[0]: row[1] for row in conn.execute('DESCRIBE fact_object_activity_events').fetchall()}
    for column in ['instance_url', 'group_names', 'session_id', 'ip_address', 'user_agent', 'details_json']:
        assert schema[column] == 'VARCHAR'

    fact_rows = conn.execute('SELECT * FROM fact_object_activity_events ORDER BY timestamp, object_type').fetchall()
    alias_rows = conn.execute('SELECT * FROM base_object_activity_events ORDER BY timestamp, object_type').fetchall()
    assert fact_rows == alias_rows
