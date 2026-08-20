from __future__ import annotations

import logging
from pathlib import Path

import duckdb
import yaml

from data_collection.data_normalizer.flatten_config import _slug
from data_collection.pulse_duckdb.sql_utils import canonical_norm_sql, log_table_stats


logger = logging.getLogger(__name__)


def _describe_view_columns(conn: duckdb.DuckDBPyConnection, view_name: str) -> list[str]:
    rows = conn.execute(f"DESCRIBE {view_name}").fetchall()  # nosec B608 (view_name is plugin-controlled)
    return [str(row[0]) for row in rows]


def _find_event_mapping_files_missing_extras(
    conn: duckdb.DuckDBPyConnection,
    *,
    glob: str,
) -> list[str]:
    rows = conn.execute(
        """
        SELECT file_name
        FROM parquet_schema(?)
        GROUP BY file_name
        HAVING SUM(CASE WHEN lower(name) = 'extras' THEN 1 ELSE 0 END) = 0
        ORDER BY file_name
        """.strip(),
        [glob],
    ).fetchall()
    return [str(row[0]) for row in rows]


def _raise_missing_event_mapping_extras(
    conn: duckdb.DuckDBPyConnection,
    *,
    module: str,
    glob: str,
    view_name: str,
) -> None:
    columns = _describe_view_columns(conn, view_name)
    message_lines = [
        "SILVER event_mapping schema missing 'extras'",
        f"module={module}",
        f"glob={glob}",
        f"columns={columns}",
    ]

    try:
        offending_files = _find_event_mapping_files_missing_extras(conn, glob=glob)
    except duckdb.Error as exc:
        logger.warning(
            "Failed failure-only parquet schema diagnostic for event_mapping module=%s glob=%s: %s",
            module,
            glob,
            exc,
        )
    else:
        if offending_files:
            message_lines.append(f"files_missing_extras={offending_files}")

    raise ValueError("\n".join(message_lines))


def load_object_activity_modules(base_dir: Path) -> list[str]:
    """Load object-activity modules from YAML.

    Expected file: gold_specs/object_activity/toolbox.yaml
    """

    path = base_dir / "object_activity" / "toolbox.yaml"
    if not path.exists():
        return []

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"Invalid object_activity toolbox.yaml (expected YAML list): {path}")

    out: list[str] = []
    seen: set[str] = set()
    for v in raw:
        if v is None:
            continue
        s = _slug(str(v))
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _create_event_mapping_module_view(
    conn: duckdb.DuckDBPyConnection,
    *,
    ctx,
    module: str,
    view_name: str,
) -> str:
    """Create a DuckDB view over SILVER event_mapping parquet for one module.

    Note: This intentionally does not rely on `create_silver_view()` because this
    helper also enables `union_by_name` for event_mapping parquet.
    """

    if not ctx.bucket_or_container:
        raise ValueError("Missing bucket/container")
    if not ctx.blob_header:
        raise ValueError(f"Unsupported connection type: {ctx.connection_type}")

    root = ctx.folder_root.strip("/")
    if root:
        root = f"{root}/"

    # Partitioned SILVER path:
    # silver/category=event_mapping/module=<module>/instance_name=*/year=*/month=*/day=*/*.parquet
    base = f"{ctx.blob_header}://{ctx.bucket_or_container}/{root}silver"

    glob = f"{base}/category=event_mapping/module={module}/instance_name=*/year=*/month=*/day=*/*.parquet"

    sql = f"""
    CREATE OR REPLACE VIEW {view_name} AS
    SELECT
      *,
      make_date(CAST(year AS INTEGER), CAST(month AS INTEGER), CAST(day AS INTEGER)) AS partition_date
    FROM read_parquet('{glob}', hive_partitioning = true, union_by_name = true);
    """.strip()  # nosec B608 (view_name is plugin-controlled; glob is derived from storage context)

    try:
        conn.execute(sql)
    except duckdb.IOException:
        # No parquet yet for this module; skip.
        return ""

    created_views = conn.execute(
        (
            "SELECT table_name FROM information_schema.views "
            "WHERE table_schema = 'main' AND table_name = ?"
        ),
        [view_name],
    ).fetchall()
    if not created_views:
        return ""

    columns = _describe_view_columns(conn, view_name)
    if "extras" not in {column.lower() for column in columns}:
        _raise_missing_event_mapping_extras(
            conn,
            module=module,
            glob=glob,
            view_name=view_name,
        )

    return view_name


def _view_columns(conn: duckdb.DuckDBPyConnection, table_name: str) -> set[str]:
    rows = conn.execute(
        (
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'main' AND table_name = ?"
        ),
        [table_name],
    ).fetchall()
    return {str(row[0]).lower() for row in rows}


def _object_activity_branch_sql(
    conn: duckdb.DuckDBPyConnection,
    *,
    module: str,
    view_name: str,
) -> str:
    view_columns = _view_columns(conn, view_name)
    ip_sources = [col for col in ("clientip", "originalip") if col in view_columns]
    ip_address_expr = (
        "COALESCE(" + ", ".join(f"CAST(e.{col} AS VARCHAR)" for col in ip_sources) + ")"
        if ip_sources
        else "CAST(NULL AS VARCHAR)"
    )
    extras_expr = "CAST(e.extras AS VARCHAR)" if "extras" in view_columns else "CAST(NULL AS VARCHAR)"

    def _has_column(name: str) -> bool:
        return name.lower() in view_columns

    def _null_if_empty(expr: str) -> str:
        return f"NULLIF(TRIM(CAST({expr} AS VARCHAR)), '')"

    def _path_segment(expr: str, marker: str) -> str:
        base_expr = f"CAST({expr} AS VARCHAR)"
        query_cut_expr = (
            f"CASE WHEN strpos({base_expr}, '?') > 0 "
            f"THEN left({base_expr}, strpos({base_expr}, '?') - 1) "
            f"ELSE {base_expr} END"
        )
        normalized_expr = (
            f"CASE WHEN strpos({query_cut_expr}, '#') > 0 "
            f"THEN left({query_cut_expr}, strpos({query_cut_expr}, '#') - 1) "
            f"ELSE {query_cut_expr} END"
        )
        extracted_expr = (
            f"CASE WHEN strpos({normalized_expr}, '{marker}') > 0 "
            f"THEN split_part(split_part({normalized_expr}, '{marker}', 2), '/', 1) "
            "ELSE NULL END"
        )
        return _null_if_empty(extracted_expr)

    def _clean_identifier(expr: str) -> str:
        return (
            "CASE "
            f"WHEN {expr} IS NULL THEN NULL "
            f"WHEN regexp_matches({expr}, '^\{{[^{{}}]+\}}$') THEN NULL "
            f"ELSE {expr} END"
        )

    def _json_text(path: str) -> str:
        return _null_if_empty(f"json_extract_string({extras_expr}, '{path}')")

    def _native_text(*names: str) -> str:
        exprs = [_null_if_empty(f"e.{name}") for name in names if _has_column(name)]
        if not exprs:
            return "CAST(NULL AS VARCHAR)"
        if len(exprs) == 1:
            return exprs[0]
        return "COALESCE(" + ", ".join(exprs) + ")"

    login_exprs = []
    if _has_column("authuser"):
        login_exprs.append(_null_if_empty("e.authuser"))
    if _has_column("user"):
        login_exprs.append(_null_if_empty("e.user"))
    login_expr = (
        "COALESCE(" + ", ".join(login_exprs) + ")"
        if login_exprs
        else "CAST(NULL AS VARCHAR)"
    )

    def _first_non_empty(*exprs: str) -> str:
        return "COALESCE(" + ", ".join(exprs) + ")"

    row_filter = f"{login_expr} IS NOT NULL"

    if module in {"datasets", "dataset"}:
        object_type_expr = repr("dataset")
        object_key_expr = _native_text("datasetname")
        row_filter = f"{row_filter} AND {object_key_expr} IS NOT NULL"
    elif module in {"visual_recipes", "misc_recipes", "prepare"}:
        object_type_expr = repr("recipe")
        object_key_expr = _native_text("recipename")
        row_filter = f"{row_filter} AND {object_key_expr} IS NOT NULL"
    elif module == "webapps":
        object_type_expr = repr("web_application")
        object_key_expr = _clean_identifier(
            _first_non_empty(
                _native_text("webappid", "webappid_source_call"),
                _json_text('$.webappid'),
            )
        )
        row_filter = f"{row_filter} AND {object_key_expr} IS NOT NULL"
    elif module == "charts_dashboard":
        dashboard_key_expr = _native_text("dashboardid")
        insight_key_expr = _first_non_empty(
            _native_text("insightid", "dashboardinsightid"),
            _json_text('$.insightId'),
            _json_text('$.insightid'),
            _json_text('$.dashboardInsightId'),
        )
        object_type_expr = (
            f"CASE WHEN {dashboard_key_expr} IS NOT NULL THEN 'dashboard' ELSE 'insight' END"
        )
        object_key_expr = _clean_identifier(_first_non_empty(dashboard_key_expr, insight_key_expr))
        row_filter = f"{row_filter} AND {object_key_expr} IS NOT NULL"
    elif module == "apis":
        object_type_expr = repr("api_service")
        object_key_expr = _clean_identifier(
            _first_non_empty(
                _native_text("serviceid", "apiserviceid"),
                _json_text('$.serviceId'),
                _json_text('$.apiServiceId'),
                _json_text('$.apiserviceid'),
                _json_text('$.serviceid'),
            )
        )
        row_filter = f"{row_filter} AND {object_key_expr} IS NOT NULL"
    elif module == "application_designer":
        object_type_expr = repr("dataiku_application")
        object_key_expr = _clean_identifier(
            _first_non_empty(
                _native_text("applicationid", "appid"),
                _json_text('$.applicationId'),
                _json_text('$.applicationid'),
                _json_text('$.appId'),
                _json_text('$.appid'),
            )
        )
        row_filter = (
            f"{row_filter} AND {object_key_expr} IS NOT NULL"
            " AND msgtype <> 'application-open'"
        )
    else:
        object_type_expr = repr(module)
        object_key_expr = "CAST(NULL AS VARCHAR)"

    project_key_expr = "project_key"

    return "".join(
        [
            "SELECT\n",
            "  try_cast(COALESCE(timestamp, date) AS TIMESTAMP) AS timestamp,\n",
            "  instance_name,\n",
            f"  {login_expr} AS login,\n",
            "  msgtype AS event_name,\n",
            "  e.dataiku_category AS event_category,\n",
            "  m.capability AS canonical_capability,\n",
            f"  {project_key_expr} AS project_key,\n",
            f"  {object_type_expr} AS object_type,\n",
            f"  {object_key_expr} AS object_key,\n",
            "  -- `object_name` is currently a fallback identifier; it is not guaranteed to be a resolved display name.\n",
            f"  {object_key_expr} AS object_name,\n",
            "  CAST(NULL AS VARCHAR) AS instance_url,\n",
            "  CAST(NULL AS VARCHAR) AS group_names,\n",
            "  CAST(NULL AS VARCHAR) AS session_id,\n",
            f"  {ip_address_expr} AS ip_address,\n",
            "  CAST(NULL AS VARCHAR) AS user_agent,\n",
            f"  {extras_expr} AS details_json,\n",
            "  try_cast(run_ts AS TIMESTAMP) AS run_timestamp,\n",
            "  CAST(year AS INTEGER) AS year,\n",
            "  CAST(month AS INTEGER) AS month,\n",
            "  CAST(day AS INTEGER) AS day\n",
            f"FROM {view_name} e\n",
            "LEFT JOIN dim_category_to_capability m\n",
            f"  ON {canonical_norm_sql('m.dataiku_category')} = {canonical_norm_sql('e.dataiku_category')}\n",
            f"WHERE {row_filter}",
        ]
    )


def build_fact_object_activity_events(
    conn: duckdb.DuckDBPyConnection,
    *,
    ctx,
    base_dir: Path,
) -> str:
    modules = load_object_activity_modules(base_dir)
    if not modules:
        return ""

    branches: list[str] = []
    for mod in modules:
        view_name = f"v_event_mapping__{_slug(mod)}"
        created = _create_event_mapping_module_view(conn, ctx=ctx, module=mod, view_name=view_name)
        if not created:
            continue
        branches.append(_object_activity_branch_sql(conn, module=_slug(mod), view_name=view_name))

    if not branches:
        return ""

    sql = (  # nosec B608 -- SQL is assembled only from trusted, recipe-generated branch SQL.
        "CREATE OR REPLACE TABLE fact_object_activity_events AS\n"
        "WITH unioned_events AS (\n"
        + "\nUNION ALL\n".join(branches)  # nosec B608 -- branches are generated internally from curated module configuration.
        + "\n), ranked_events AS (\n"
        "  SELECT\n"
        "    *,\n"
        "    ROW_NUMBER() OVER (\n"
        "      PARTITION BY\n"
        "        timestamp,\n"
        "        instance_name,\n"
        "        lower(trim(COALESCE(login, ''))),\n"
        "        event_name,\n"
        "        regexp_replace(replace(replace(lower(trim(COALESCE(event_category, ''))), ' ', '_'), '-', '_'), '_+', '_', 'g'),\n"
        "        project_key,\n"
        "        object_type,\n"
        "        object_key,\n"
        "        ip_address,\n"
        "        details_json\n"
        "      ORDER BY run_timestamp DESC NULLS LAST\n"
        "    ) AS rn\n"
        "  FROM unioned_events\n"
        ")\n"
        "SELECT\n"
        "  timestamp,\n"
        "  instance_name,\n"
        "  login,\n"
        "  event_name,\n"
        "  event_category,\n"
        "  canonical_capability,\n"
        "  project_key,\n"
        "  object_type,\n"
        "  object_key,\n"
        "  object_name,\n"
        "  instance_url,\n"
        "  group_names,\n"
        "  session_id,\n"
        "  ip_address,\n"
        "  user_agent,\n"
        "  details_json,\n"
        "  run_timestamp,\n"
        "  year,\n"
        "  month,\n"
        "  day\n"
        "FROM ranked_events\n"
        "WHERE rn = 1;"
    )
    conn.execute(sql)
    log_table_stats(conn, "fact_object_activity_events")

    # Backward compatibility: some downstream objects still reference the old base name.
    conn.execute(
        "CREATE OR REPLACE VIEW \"base_object_activity_events\" AS SELECT * FROM fact_object_activity_events;"
    )
    return "fact_object_activity_events"
