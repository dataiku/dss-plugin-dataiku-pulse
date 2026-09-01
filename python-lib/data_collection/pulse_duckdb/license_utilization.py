from __future__ import annotations

import duckdb

from data_collection.pulse_duckdb.views import create_silver_view


def _build_fact_license_utilization_daily_from_views(
    conn: duckdb.DuckDBPyConnection,
    *,
    max_licenses_view: str,
    users_view: str,
) -> str:
    conn.execute(
        f"""
        CREATE OR REPLACE TABLE fact_license_utilization_daily AS
        WITH entitlement_latest AS (
          SELECT
            CAST(date_trunc('day', try_cast(run_ts AS TIMESTAMP)) AS DATE) AS snapshot_date,
            instance_name,
            coalesce(nullif(trim(license_profile), ''), 'UNKNOWN') AS license_profile,
            try_cast(max_licenses AS BIGINT) AS entitled_count,
            try_cast(run_ts AS TIMESTAMP) AS run_ts,
            partition_date,
            ROW_NUMBER() OVER (
              PARTITION BY
                instance_name,
                CAST(date_trunc('day', try_cast(run_ts AS TIMESTAMP)) AS DATE),
                coalesce(nullif(trim(license_profile), ''), 'UNKNOWN')
              ORDER BY
                try_cast(run_ts AS TIMESTAMP) DESC NULLS LAST,
                partition_date DESC NULLS LAST,
                try_cast(max_licenses AS BIGINT) DESC NULLS LAST,
                coalesce(nullif(trim(license_profile), ''), 'UNKNOWN') ASC
            ) AS rn
          FROM {max_licenses_view}
          WHERE instance_name IS NOT NULL
            AND length(trim(instance_name)) > 0
            AND try_cast(run_ts AS TIMESTAMP) IS NOT NULL
        ),
        entitlements AS (
          SELECT
            snapshot_date,
            instance_name,
            license_profile,
            entitled_count,
            run_ts
          FROM entitlement_latest
          WHERE rn = 1
        ),
        users_latest AS (
          SELECT
            CAST(date_trunc('day', try_cast(run_ts AS TIMESTAMP)) AS DATE) AS snapshot_date,
            instance_name,
            lower(trim(users_login)) AS login_norm,
            coalesce(
              nullif(trim(users_resultinguserprofile), ''),
              nullif(trim(users_userprofile), ''),
              'UNKNOWN'
            ) AS license_profile,
            users_enabled,
            try_cast(run_ts AS TIMESTAMP) AS run_ts,
            partition_date,
            ROW_NUMBER() OVER (
              PARTITION BY
                instance_name,
                CAST(date_trunc('day', try_cast(run_ts AS TIMESTAMP)) AS DATE),
                lower(trim(users_login))
              ORDER BY
                try_cast(run_ts AS TIMESTAMP) DESC NULLS LAST,
                partition_date DESC NULLS LAST,
                coalesce(
                  nullif(trim(users_resultinguserprofile), ''),
                  nullif(trim(users_userprofile), ''),
                  'UNKNOWN'
                ) ASC,
                coalesce(trim(users_enabled), '') DESC
            ) AS rn
          FROM {users_view}
          WHERE instance_name IS NOT NULL
            AND length(trim(instance_name)) > 0
            AND users_login IS NOT NULL
            AND length(trim(users_login)) > 0
            AND try_cast(run_ts AS TIMESTAMP) IS NOT NULL
        ),
        assignments AS (
          SELECT
            snapshot_date,
            instance_name,
            license_profile,
            COUNT(DISTINCT login_norm) FILTER (WHERE users_enabled = 'True')::BIGINT AS assigned_count,
            MAX(run_ts) AS run_ts
          FROM users_latest
          WHERE rn = 1
          GROUP BY 1, 2, 3
        )
        SELECT
          coalesce(e.snapshot_date, a.snapshot_date) AS snapshot_date,
          coalesce(e.instance_name, a.instance_name) AS instance_name,
          coalesce(e.license_profile, a.license_profile) AS license_profile,
          e.entitled_count AS entitled_count,
          CASE
            WHEN a.assigned_count IS NOT NULL THEN a.assigned_count
            WHEN e.snapshot_date IS NOT NULL THEN 0::BIGINT
            ELSE NULL
          END AS assigned_count,
          CASE
            WHEN e.entitled_count IS NULL THEN NULL
            ELSE e.entitled_count - coalesce(a.assigned_count, 0)
          END AS available_count,
          CASE
            WHEN e.entitled_count IS NULL OR e.entitled_count = 0 THEN NULL
            ELSE (coalesce(a.assigned_count, 0)::DOUBLE / e.entitled_count::DOUBLE) * 100.0
          END AS utilization_pct,
          coalesce(greatest(e.run_ts, a.run_ts), e.run_ts, a.run_ts) AS run_ts
        FROM entitlements e
        FULL OUTER JOIN assignments a
          ON e.snapshot_date = a.snapshot_date
         AND e.instance_name = a.instance_name
         AND e.license_profile = a.license_profile
        ORDER BY 1, 2, 3;
        """.strip()  # nosec B608
    )
    return "fact_license_utilization_daily"


def build_fact_license_utilization_daily(
    conn: duckdb.DuckDBPyConnection,
    *,
    ctx,
) -> str:
    max_licenses_view, _skip_reason = create_silver_view(
        conn=conn,
        ctx=ctx,
        category="license",
        module="max_licenses",
    )
    if not max_licenses_view:
        return ""

    users_view, _skip_reason = create_silver_view(
        conn=conn,
        ctx=ctx,
        category="users",
        module="instance_metadata",
    )
    if not users_view:
        return ""

    return _build_fact_license_utilization_daily_from_views(
        conn,
        max_licenses_view=max_licenses_view,
        users_view=users_view,
    )


def collect_license_utilization_quality_report(conn: duckdb.DuckDBPyConnection) -> dict:
    report: dict[str, object] = {
        "present": False,
        "rows": 0,
        "distinct_keys": 0,
        "duplicate_keys": 0,
        "negative_available_rows": 0,
        "zero_or_null_entitlement_with_non_null_utilization": 0,
        "non_numeric_entitled_rows": 0,
        "non_numeric_assigned_rows": 0,
        "min_snapshot_date": None,
        "max_snapshot_date": None,
    }

    tables = {name for (name,) in conn.sql("SHOW TABLES").fetchall()}
    if "fact_license_utilization_daily" not in tables:
        return report

    row = conn.execute(
        """
        SELECT
          COUNT(*) AS rows_count,
          COUNT(DISTINCT concat(instance_name, '::', CAST(snapshot_date AS VARCHAR), '::', license_profile)) AS distinct_keys,
          COUNT(*) - COUNT(DISTINCT concat(instance_name, '::', CAST(snapshot_date AS VARCHAR), '::', license_profile)) AS duplicate_keys,
          COUNT(*) FILTER (WHERE entitled_count IS NOT NULL AND available_count < 0) AS negative_available_rows,
          COUNT(*) FILTER (WHERE (entitled_count IS NULL OR entitled_count = 0) AND utilization_pct IS NOT NULL) AS invalid_null_utilization_rows,
          COUNT(*) FILTER (WHERE entitled_count IS NOT NULL AND try_cast(entitled_count AS BIGINT) IS NULL) AS non_numeric_entitled_rows,
          COUNT(*) FILTER (WHERE assigned_count IS NOT NULL AND try_cast(assigned_count AS BIGINT) IS NULL) AS non_numeric_assigned_rows,
          MIN(snapshot_date) AS min_snapshot_date,
          MAX(snapshot_date) AS max_snapshot_date
        FROM fact_license_utilization_daily;
        """.strip()
    ).fetchone()

    report["present"] = True
    report["rows"] = int(row[0] or 0)
    report["distinct_keys"] = int(row[1] or 0)
    report["duplicate_keys"] = int(row[2] or 0)
    report["negative_available_rows"] = int(row[3] or 0)
    report["zero_or_null_entitlement_with_non_null_utilization"] = int(row[4] or 0)
    report["non_numeric_entitled_rows"] = int(row[5] or 0)
    report["non_numeric_assigned_rows"] = int(row[6] or 0)
    report["min_snapshot_date"] = str(row[7]) if row[7] is not None else None
    report["max_snapshot_date"] = str(row[8]) if row[8] is not None else None
    return report
