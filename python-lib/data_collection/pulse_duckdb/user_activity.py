from __future__ import annotations

import duckdb

from data_collection.views import create_silver_view


def build_fact_user_activity_daily(
    conn: duckdb.DuckDBPyConnection,
    *,
    ctx,
) -> str:
    view_name, _skip_reason = create_silver_view(conn=conn, ctx=ctx, category="users", module="user_activity")
    if not view_name:
        return ""

    conn.execute(
        f"""
        CREATE OR REPLACE TABLE fact_user_activity_daily AS
        SELECT
          CAST(date_trunc('day', timestamp) AS DATE) AS day,
          instance_name,
          lower(trim(login)) AS login_norm,
          MIN(trim(login)) AS login,
          SUM(COALESCE(viewing_actions_count, 0)) AS viewing_actions_count,
          SUM(COALESCE(developing_actions_count, 0)) AS developing_actions_count,
          MAX(timestamp) AS last_activity_at
        FROM {view_name}
        WHERE timestamp IS NOT NULL
          AND login IS NOT NULL
          AND length(trim(login)) > 0
        GROUP BY 1, 2, 3;
        """.strip()
    )
    return "fact_user_activity_daily"


def build_fact_user_activity_project_daily(
    conn: duckdb.DuckDBPyConnection,
    *,
    ctx,
) -> str:
    view_name, _skip_reason = create_silver_view(conn=conn, ctx=ctx, category="users", module="user_activity")
    if not view_name:
        return ""

    conn.execute(
        f"""
        CREATE OR REPLACE TABLE fact_user_activity_project_daily AS
        SELECT
          CAST(date_trunc('day', timestamp) AS DATE) AS day,
          instance_name,
          lower(trim(login)) AS login_norm,
          MIN(trim(login)) AS login,
          project_key,
          SUM(COALESCE(viewing_actions_count, 0)) AS viewing_actions_count,
          SUM(COALESCE(developing_actions_count, 0)) AS developing_actions_count,
          MAX(timestamp) AS last_activity_at
        FROM {view_name}
        WHERE timestamp IS NOT NULL
          AND login IS NOT NULL
          AND length(trim(login)) > 0
          AND project_key IS NOT NULL
          AND length(trim(project_key)) > 0
        GROUP BY 1, 2, 3, 5;
        """.strip()
    )
    return "fact_user_activity_project_daily"


def build_fact_formal_mau_daily(
    conn: duckdb.DuckDBPyConnection,
    *,
    ctx,
) -> str:
    view_name, _skip_reason = create_silver_view(
        conn=conn,
        ctx=ctx,
        category="users_formal_mau",
        module="formal_mau",
    )
    if not view_name:
        return ""

    conn.execute(
        f"""
        CREATE OR REPLACE TABLE fact_formal_mau_daily AS
        SELECT
          CAST(date_trunc('day', timestamp) AS DATE) AS day,
          instance_name,
          lower(trim(login)) AS login_norm,
          MIN(trim(login)) AS login,
          SUM(COALESCE(try_cast(application_open_count AS BIGINT), 0)) AS application_open_count,
          MAX(timestamp) AS last_application_open_at
        FROM {view_name}
        WHERE timestamp IS NOT NULL
          AND login IS NOT NULL
          AND length(trim(login)) > 0
        GROUP BY 1, 2, 3;
        """.strip()
    )
    return "fact_formal_mau_daily"



def collect_user_activity_quality_report(conn: duckdb.DuckDBPyConnection) -> dict:
    report: dict[str, object] = {
        "daily_present": False,
        "project_present": False,
        "daily": {},
        "project": {},
    }

    tables = {name for (name,) in conn.sql("SHOW TABLES").fetchall()}

    if "fact_user_activity_daily" in tables:
        row = conn.execute(
            """
            SELECT
              COUNT(*) AS rows_count,
              COUNT(DISTINCT login_norm) AS distinct_users,
              SUM(COALESCE(viewing_actions_count, 0)) AS viewing_actions,
              SUM(COALESCE(developing_actions_count, 0)) AS developing_actions,
              COUNT(*) FILTER (WHERE COALESCE(developing_actions_count, 0) > 0) AS rows_with_developing,
              MIN(day) AS min_day,
              MAX(day) AS max_day
            FROM fact_user_activity_daily;
            """.strip()
        ).fetchone()
        report["daily_present"] = True
        report["daily"] = {
            "rows_count": int(row[0] or 0),
            "distinct_users": int(row[1] or 0),
            "viewing_actions": int(row[2] or 0),
            "developing_actions": int(row[3] or 0),
            "rows_with_developing": int(row[4] or 0),
            "min_day": str(row[5]) if row[5] is not None else None,
            "max_day": str(row[6]) if row[6] is not None else None,
        }

    if "fact_user_activity_project_daily" in tables:
        row = conn.execute(
            """
            SELECT
              COUNT(*) AS rows_count,
              COUNT(DISTINCT login_norm) AS distinct_users,
              COUNT(DISTINCT project_key) AS distinct_projects,
              SUM(COALESCE(viewing_actions_count, 0)) AS viewing_actions,
              SUM(COALESCE(developing_actions_count, 0)) AS developing_actions
            FROM fact_user_activity_project_daily;
            """.strip()
        ).fetchone()
        report["project_present"] = True
        report["project"] = {
            "rows_count": int(row[0] or 0),
            "distinct_users": int(row[1] or 0),
            "distinct_projects": int(row[2] or 0),
            "viewing_actions": int(row[3] or 0),
            "developing_actions": int(row[4] or 0),
        }

    return report
