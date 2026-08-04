from __future__ import annotations

from datetime import datetime, timezone
import re

import duckdb
import pytest


def _sql_slug_expr(column: str) -> str:
    return (
        "regexp_replace("
        f"replace(replace(lower(trim({column})), ' ', '_'), '-', '_'),"
        " '_+', '_', 'g'"
        ")"
    )


@pytest.fixture()
def conn():
    connection = duckdb.connect(database=":memory:")
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture()
def seeded_dims(conn):
    conn.execute(
        """
        CREATE OR REPLACE TABLE dim_category_to_capability AS
        SELECT * FROM (
          VALUES
            ('webapps', 'applications_delivery', 1, 1, 'Applications & Delivery', 'Web Applications', FALSE),
            ('visual_recipes', 'data_engineering', 1, 2, 'Data Engineering', 'Visual Recipes', TRUE),
            ('charts_dashboard', 'applications_delivery', 1, 3, 'Applications & Delivery', 'Charts & Dashboards', FALSE),
            ('apis', 'apis_integration', 1, 4, 'APIs & Integration', 'APIs', FALSE),
            ('application_designer', 'applications_delivery', 1, 5, 'Applications & Delivery', 'Application Designer', FALSE),
            ('datasets', 'project_maintenance', 1, 6, 'Project Maintenance', 'Datasets', TRUE)
        ) AS t(dataiku_category, capability, capability_order, category_order, capability_display_name, category_display_name, is_dev_activity)
        """.strip()
    )
    return conn



def _build_fact_object_activity_events_for_test(conn: duckdb.DuckDBPyConnection) -> None:
    branches = [
        f"""
        SELECT
          timestamp,
          instance_name,
          COALESCE(NULLIF(TRIM(CAST(authuser AS VARCHAR)), ''), NULLIF(TRIM(CAST("user" AS VARCHAR)), '')) AS login,
          msgtype AS event_name,
          e.dataiku_category AS event_category,
          m.capability AS canonical_capability,
          project_key,
          'dataset' AS object_type,
          NULLIF(TRIM(CAST(datasetname AS VARCHAR)), '') AS object_key,
          NULLIF(TRIM(CAST(datasetname AS VARCHAR)), '') AS object_name,
          CAST(NULL AS VARCHAR) AS instance_url,
          CAST(NULL AS VARCHAR) AS group_names,
          CAST(NULL AS VARCHAR) AS session_id,
          COALESCE(CAST(clientip AS VARCHAR), CAST(originalip AS VARCHAR)) AS ip_address,
          CAST(NULL AS VARCHAR) AS user_agent,
          CAST(extras AS VARCHAR) AS details_json,
          run_timestamp,
          year,
          month,
          day
        FROM v_event_mapping__datasets e
        LEFT JOIN dim_category_to_capability m
          ON {_sql_slug_expr('m.dataiku_category')} = {_sql_slug_expr('e.dataiku_category')}
        WHERE COALESCE(NULLIF(TRIM(CAST(authuser AS VARCHAR)), ''), NULLIF(TRIM(CAST("user" AS VARCHAR)), '')) IS NOT NULL
          AND NULLIF(TRIM(CAST(datasetname AS VARCHAR)), '') IS NOT NULL
        """.strip(),
        f"""
        SELECT
          timestamp,
          instance_name,
          COALESCE(NULLIF(TRIM(CAST(authuser AS VARCHAR)), ''), NULLIF(TRIM(CAST("user" AS VARCHAR)), '')) AS login,
          msgtype AS event_name,
          e.dataiku_category AS event_category,
          m.capability AS canonical_capability,
          project_key,
          'recipe' AS object_type,
          NULLIF(TRIM(CAST(recipename AS VARCHAR)), '') AS object_key,
          NULLIF(TRIM(CAST(recipename AS VARCHAR)), '') AS object_name,
          CAST(NULL AS VARCHAR) AS instance_url,
          CAST(NULL AS VARCHAR) AS group_names,
          CAST(NULL AS VARCHAR) AS session_id,
          CAST(NULL AS VARCHAR) AS ip_address,
          CAST(NULL AS VARCHAR) AS user_agent,
          CAST(NULL AS VARCHAR) AS details_json,
          run_timestamp,
          year,
          month,
          day
        FROM v_event_mapping__visual_recipes e
        LEFT JOIN dim_category_to_capability m
          ON {_sql_slug_expr('m.dataiku_category')} = {_sql_slug_expr('e.dataiku_category')}
        WHERE COALESCE(NULLIF(TRIM(CAST(authuser AS VARCHAR)), ''), NULLIF(TRIM(CAST("user" AS VARCHAR)), '')) IS NOT NULL
          AND NULLIF(TRIM(CAST(recipename AS VARCHAR)), '') IS NOT NULL
        """.strip(),
        f"""
        SELECT
          timestamp,
          instance_name,
          COALESCE(NULLIF(TRIM(CAST(authuser AS VARCHAR)), ''), NULLIF(TRIM(CAST("user" AS VARCHAR)), '')) AS login,
          msgtype AS event_name,
          e.dataiku_category AS event_category,
          m.capability AS canonical_capability,
          project_key,
          'api_service' AS object_type,
          NULLIF(TRIM(CAST(serviceid AS VARCHAR)), '') AS object_key,
          NULLIF(TRIM(CAST(serviceid AS VARCHAR)), '') AS object_name,
          CAST(NULL AS VARCHAR) AS instance_url,
          CAST(NULL AS VARCHAR) AS group_names,
          CAST(NULL AS VARCHAR) AS session_id,
          CAST(NULL AS VARCHAR) AS ip_address,
          CAST(NULL AS VARCHAR) AS user_agent,
          CAST(NULL AS VARCHAR) AS details_json,
          run_timestamp,
          year,
          month,
          day
        FROM v_event_mapping__apis e
        LEFT JOIN dim_category_to_capability m
          ON {_sql_slug_expr('m.dataiku_category')} = {_sql_slug_expr('e.dataiku_category')}
        WHERE COALESCE(NULLIF(TRIM(CAST(authuser AS VARCHAR)), ''), NULLIF(TRIM(CAST("user" AS VARCHAR)), '')) IS NOT NULL
          AND NULLIF(TRIM(CAST(serviceid AS VARCHAR)), '') IS NOT NULL
        """.strip(),
        f"""
        SELECT
          timestamp,
          instance_name,
          COALESCE(NULLIF(TRIM(CAST(authuser AS VARCHAR)), ''), NULLIF(TRIM(CAST("user" AS VARCHAR)), '')) AS login,
          msgtype AS event_name,
          e.dataiku_category AS event_category,
          m.capability AS canonical_capability,
          project_key,
          'web_application' AS object_type,
          NULLIF(TRIM(CAST(webappid AS VARCHAR)), '') AS object_key,
          NULLIF(TRIM(CAST(webappid AS VARCHAR)), '') AS object_name,
          CAST(NULL AS VARCHAR) AS instance_url,
          CAST(NULL AS VARCHAR) AS group_names,
          CAST(NULL AS VARCHAR) AS session_id,
          CAST(NULL AS VARCHAR) AS ip_address,
          CAST(NULL AS VARCHAR) AS user_agent,
          CAST(NULL AS VARCHAR) AS details_json,
          run_timestamp,
          year,
          month,
          day
        FROM v_event_mapping__webapps e
        LEFT JOIN dim_category_to_capability m
          ON {_sql_slug_expr('m.dataiku_category')} = {_sql_slug_expr('e.dataiku_category')}
        WHERE COALESCE(NULLIF(TRIM(CAST(authuser AS VARCHAR)), ''), NULLIF(TRIM(CAST("user" AS VARCHAR)), '')) IS NOT NULL
          AND NULLIF(TRIM(CAST(webappid AS VARCHAR)), '') IS NOT NULL
        """.strip(),
        f"""
        SELECT
          timestamp,
          instance_name,
          COALESCE(NULLIF(TRIM(CAST(authuser AS VARCHAR)), ''), NULLIF(TRIM(CAST("user" AS VARCHAR)), '')) AS login,
          msgtype AS event_name,
          e.dataiku_category AS event_category,
          m.capability AS canonical_capability,
          project_key,
          CASE WHEN NULLIF(TRIM(CAST(dashboardid AS VARCHAR)), '') IS NOT NULL THEN 'dashboard' ELSE 'insight' END AS object_type,
          COALESCE(NULLIF(TRIM(CAST(dashboardid AS VARCHAR)), ''), NULLIF(TRIM(CAST(insightid AS VARCHAR)), '')) AS object_key,
          COALESCE(NULLIF(TRIM(CAST(dashboardid AS VARCHAR)), ''), NULLIF(TRIM(CAST(insightid AS VARCHAR)), '')) AS object_name,
          CAST(NULL AS VARCHAR) AS instance_url,
          CAST(NULL AS VARCHAR) AS group_names,
          CAST(NULL AS VARCHAR) AS session_id,
          CAST(NULL AS VARCHAR) AS ip_address,
          CAST(NULL AS VARCHAR) AS user_agent,
          CAST(NULL AS VARCHAR) AS details_json,
          run_timestamp,
          year,
          month,
          day
        FROM v_event_mapping__charts_dashboard e
        LEFT JOIN dim_category_to_capability m
          ON {_sql_slug_expr('m.dataiku_category')} = {_sql_slug_expr('e.dataiku_category')}
        WHERE COALESCE(NULLIF(TRIM(CAST(authuser AS VARCHAR)), ''), NULLIF(TRIM(CAST("user" AS VARCHAR)), '')) IS NOT NULL
          AND COALESCE(NULLIF(TRIM(CAST(dashboardid AS VARCHAR)), ''), NULLIF(TRIM(CAST(insightid AS VARCHAR)), '')) IS NOT NULL
        """.strip(),
        f"""
        SELECT
          timestamp,
          instance_name,
          COALESCE(NULLIF(TRIM(CAST(authuser AS VARCHAR)), ''), NULLIF(TRIM(CAST("user" AS VARCHAR)), '')) AS login,
          msgtype AS event_name,
          e.dataiku_category AS event_category,
          m.capability AS canonical_capability,
          project_key,
          'dataiku_application' AS object_type,
          NULLIF(TRIM(CAST(appid AS VARCHAR)), '') AS object_key,
          NULLIF(TRIM(CAST(appid AS VARCHAR)), '') AS object_name,
          CAST(NULL AS VARCHAR) AS instance_url,
          CAST(NULL AS VARCHAR) AS group_names,
          CAST(NULL AS VARCHAR) AS session_id,
          CAST(NULL AS VARCHAR) AS ip_address,
          CAST(NULL AS VARCHAR) AS user_agent,
          CAST(NULL AS VARCHAR) AS details_json,
          run_timestamp,
          year,
          month,
          day
        FROM v_event_mapping__application_designer e
        LEFT JOIN dim_category_to_capability m
          ON {_sql_slug_expr('m.dataiku_category')} = {_sql_slug_expr('e.dataiku_category')}
        WHERE COALESCE(NULLIF(TRIM(CAST(authuser AS VARCHAR)), ''), NULLIF(TRIM(CAST("user" AS VARCHAR)), '')) IS NOT NULL
          AND NULLIF(TRIM(CAST(appid AS VARCHAR)), '') IS NOT NULL
          AND msgtype <> 'application-open'
        """.strip(),
    ]

    sql = (
        "CREATE OR REPLACE TABLE fact_object_activity_events AS\n"
        "WITH unioned_events AS (\n"
        + "\nUNION ALL\n".join(branches)
        + "\n), ranked_events AS (\n"
        "  SELECT *, ROW_NUMBER() OVER (\n"
        f"    PARTITION BY timestamp, instance_name, lower(trim(COALESCE(login, ''))), event_name, {_sql_slug_expr('event_category')}, project_key, object_type, object_key, ip_address, details_json\n"
        "    ORDER BY run_timestamp DESC NULLS LAST\n"
        "  ) AS rn\n"
        "  FROM unioned_events\n"
        ")\n"
        "SELECT timestamp, instance_name, login, event_name, event_category, canonical_capability, project_key, object_type, object_key, object_name, instance_url, group_names, session_id, ip_address, user_agent, details_json, run_timestamp, year, month, day\n"
        "FROM ranked_events WHERE rn = 1"
    )
    conn.execute(sql)
    conn.execute(
        'CREATE OR REPLACE VIEW base_object_activity_events AS SELECT * FROM fact_object_activity_events'
    )


@pytest.fixture()
def seeded_views(seeded_dims):
    conn = seeded_dims
    conn.execute(
        """
        CREATE OR REPLACE VIEW v_event_mapping__datasets AS
        SELECT * FROM (
          VALUES
            (TIMESTAMP '2026-08-01 10:00:00', 'inst', 'alice', NULL, 'dataset-view', 'WEBAPPS', 'P1', 'ds1', '1.1.1.1', NULL, '{"a":1}', TIMESTAMP '2026-08-02 00:00:00', 2026, 8, 1),
            (TIMESTAMP '2026-08-01 10:00:00', 'inst', 'alice', NULL, 'dataset-view', 'webapps', 'P1', 'ds1', '1.1.1.1', NULL, '{"a":1}', TIMESTAMP '2026-08-03 00:00:00', 2026, 8, 2),
            (TIMESTAMP '2026-08-01 10:05:00', 'inst', 'alice', NULL, 'dataset-view', 'webapps', 'P1', 'ds1', '1.1.1.1', NULL, '{"a":1}', TIMESTAMP '2026-08-03 00:00:00', 2026, 8, 2),
            (TIMESTAMP '2026-08-01 10:00:00', 'inst', '', 'bob', 'dataset-view', 'webapps', 'P2', 'ds2', NULL, NULL, NULL, TIMESTAMP '2026-08-04 00:00:00', 2026, 8, 2),
            (TIMESTAMP '2026-08-01 10:00:00', 'inst', '   ', '   ', 'dataset-view', 'webapps', 'P3', 'ds3', NULL, NULL, NULL, TIMESTAMP '2026-08-04 00:00:00', 2026, 8, 2)
        ) AS t(timestamp, instance_name, authuser, "user", msgtype, dataiku_category, project_key, datasetname, clientip, originalip, extras, run_timestamp, year, month, day)
        """.strip()
    )
    conn.execute(
        """
        CREATE OR REPLACE VIEW v_event_mapping__visual_recipes AS
        SELECT * FROM (
          VALUES
            (TIMESTAMP '2026-08-01 11:00:00', 'inst', 'alice', NULL, 'recipe-save', 'Visual Recipes', 'P1', 'r1', TIMESTAMP '2026-08-02 00:00:00', 2026, 8, 1)
        ) AS t(timestamp, instance_name, authuser, "user", msgtype, dataiku_category, project_key, recipename, run_timestamp, year, month, day)
        """.strip()
    )
    conn.execute(
        """
        CREATE OR REPLACE VIEW v_event_mapping__apis AS
        SELECT * FROM (
          VALUES
            (TIMESTAMP '2026-08-01 12:00:00', 'inst', 'alice', NULL, 'api-call', 'apis', 'P1', 'svc1', TIMESTAMP '2026-08-02 00:00:00', 2026, 8, 1)
        ) AS t(timestamp, instance_name, authuser, "user", msgtype, dataiku_category, project_key, serviceid, run_timestamp, year, month, day)
        """.strip()
    )
    conn.execute(
        """
        CREATE OR REPLACE VIEW v_event_mapping__webapps AS
        SELECT * FROM (
          VALUES
            (TIMESTAMP '2026-08-01 13:00:00', 'inst', 'alice', NULL, 'webapp-view', 'visual-recipes', 'P1', 'w1', TIMESTAMP '2026-08-02 00:00:00', 2026, 8, 1)
        ) AS t(timestamp, instance_name, authuser, "user", msgtype, dataiku_category, project_key, webappid, run_timestamp, year, month, day)
        """.strip()
    )
    conn.execute(
        """
        CREATE OR REPLACE VIEW v_event_mapping__charts_dashboard AS
        SELECT * FROM (
          VALUES
            (TIMESTAMP '2026-08-01 14:00:00', 'inst', 'alice', NULL, 'dashboard-view', 'charts dashboard', 'P1', 'd1', NULL, TIMESTAMP '2026-08-02 00:00:00', 2026, 8, 1)
        ) AS t(timestamp, instance_name, authuser, "user", msgtype, dataiku_category, project_key, dashboardid, insightid, run_timestamp, year, month, day)
        """.strip()
    )
    conn.execute(
        """
        CREATE OR REPLACE VIEW v_event_mapping__application_designer AS
        SELECT * FROM (
          VALUES
            (TIMESTAMP '2026-08-01 15:00:00', 'inst', 'alice', NULL, 'app-edit', 'application_designer', 'P1', 'app1', TIMESTAMP '2026-08-02 00:00:00', 2026, 8, 1),
            (TIMESTAMP '2026-08-01 16:00:00', 'inst', 'alice', NULL, 'application-open', 'application_designer', 'P1', 'app2', TIMESTAMP '2026-08-02 00:00:00', 2026, 8, 1)
        ) AS t(timestamp, instance_name, authuser, "user", msgtype, dataiku_category, project_key, appid, run_timestamp, year, month, day)
        """.strip()
    )
    return conn


def test_repeated_copies_deduplicate_and_newest_run_timestamp_wins(seeded_views):
    conn = seeded_views
    _build_fact_object_activity_events_for_test(conn)
    rows = conn.execute(
        """
        SELECT COUNT(*), MAX(run_timestamp)
        FROM fact_object_activity_events
        WHERE object_type = 'dataset'
          AND object_key = 'ds1'
          AND event_name = 'dataset-view'
          AND timestamp = TIMESTAMP '2026-08-01 10:00:00'
        """.strip()
    ).fetchone()
    assert rows[0] == 1
    assert rows[1] == datetime(2026, 8, 3, 0, 0)



def test_different_event_timestamps_and_dimensions_remain_separate(seeded_views):
    conn = seeded_views
    _build_fact_object_activity_events_for_test(conn)
    rows = conn.execute(
        """
        SELECT timestamp, project_key, object_key
        FROM fact_object_activity_events
        WHERE object_type = 'dataset'
        ORDER BY timestamp, project_key, object_key
        """.strip()
    ).fetchall()
    assert rows == [
        (datetime(2026, 8, 1, 10, 0), 'P1', 'ds1'),
        (datetime(2026, 8, 1, 10, 0), 'P2', 'ds2'),
        (datetime(2026, 8, 1, 10, 5), 'P1', 'ds1'),
    ]



def test_category_normalization_populates_capability_and_variants_join(seeded_views):
    conn = seeded_views
    _build_fact_object_activity_events_for_test(conn)
    rows = conn.execute(
        """
        SELECT object_type, event_category, canonical_capability
        FROM fact_object_activity_events
        ORDER BY object_type, event_category
        """.strip()
    ).fetchall()
    assert ('api_service', 'apis', 'apis_integration') in rows
    assert ('dashboard', 'charts dashboard', 'applications_delivery') in rows
    assert ('dataset', 'webapps', 'applications_delivery') in rows
    assert ('recipe', 'Visual Recipes', 'data_engineering') in rows
    assert ('web_application', 'visual-recipes', 'data_engineering') in rows



def test_nullable_placeholder_columns_are_varchar(seeded_views):
    conn = seeded_views
    _build_fact_object_activity_events_for_test(conn)
    schema = {row[0]: row[1] for row in conn.execute('DESCRIBE fact_object_activity_events').fetchall()}
    for column in ['instance_url', 'group_names', 'session_id', 'ip_address', 'user_agent', 'details_json']:
        assert schema[column] == 'VARCHAR'



def test_login_selection_and_filtering_use_authuser_then_user_fallback(seeded_views):
    conn = seeded_views
    _build_fact_object_activity_events_for_test(conn)
    rows = conn.execute(
        """
        SELECT project_key, login
        FROM fact_object_activity_events
        WHERE object_type = 'dataset'
        ORDER BY project_key
        """.strip()
    ).fetchall()
    project_to_login = {project_key: login for project_key, login in rows}
    assert project_to_login["P2"] == "bob"
    assert "P3" not in project_to_login



def test_object_extraction_for_each_configured_module_and_alias_matches(seeded_views):
    conn = seeded_views
    _build_fact_object_activity_events_for_test(conn)
    object_types = [row[0] for row in conn.execute(
        "SELECT DISTINCT object_type FROM fact_object_activity_events ORDER BY 1"
    ).fetchall()]
    assert object_types == ['api_service', 'dashboard', 'dataiku_application', 'dataset', 'recipe', 'web_application']

    fact_rows = conn.execute('SELECT * FROM fact_object_activity_events ORDER BY timestamp, object_type').fetchall()
    alias_rows = conn.execute('SELECT * FROM base_object_activity_events ORDER BY timestamp, object_type').fetchall()
    fact_schema = conn.execute('DESCRIBE fact_object_activity_events').fetchall()
    alias_schema = conn.execute('DESCRIBE base_object_activity_events').fetchall()

    assert fact_rows == alias_rows
    assert fact_schema == alias_schema
