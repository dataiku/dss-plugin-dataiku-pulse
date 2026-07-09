from __future__ import annotations

import io
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
import uuid
from datetime import datetime, timedelta, timezone
import getpass

import duckdb
import dataiku
import yaml
from dataiku.customrecipe import get_output_names_for_role, get_recipe_config

from data_collection.helper.dss_folder_writer import ensure_managed_folder
from data_collection.pulse_duckdb.context import build_storage_context
from data_collection.pulse_duckdb.duckdb_manager import prepare_duckdb
from data_collection.pulse_duckdb.gold_builder import apply_gold_spec, load_gold_spec
from data_collection.pulse_duckdb.views import create_silver_view


def _resolve_gold_folder_lookup() -> str:
    """Resolve GOLD output folder lookup.

    In normal DSS recipe runs, this comes from the output role `gold_tables_folder`.

    For local/debug runs (outside the DSS recipe harness), set
    `PULSE_GOLD_DEBUG_LOOKUP` to a managed folder name (ex: `gold_data`) and the
    recipe will use that value.
    """

    debug_lookup = os.environ.get("PULSE_GOLD_DEBUG_LOOKUP")
    if debug_lookup:
        return debug_lookup

    try:
        out_names = get_output_names_for_role("gold_tables_folder")
    except RuntimeError:
        out_names = []

    if out_names:
        return out_names[0]

    raise ValueError(
        "Missing output managed folder for role 'gold_tables_folder' "
        "(or set PULSE_GOLD_DEBUG_LOOKUP for local runs)"
    )


logger = logging.getLogger(__name__)


MANIFEST_PATH = "gold/_state/manifest.json"


def _sql_identifier(name: str) -> str:
    escaped = str(name).replace('"', '""')
    return f'"{escaped}"'


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _time_query(conn: duckdb.DuckDBPyConnection, sql: str, *, label: str) -> None:
    started = time.monotonic()
    logger.info("Starting step: %s", label)
    conn.execute(sql)
    logger.info("Completed step: %s in %.1fs", label, time.monotonic() - started)


def _log_table_stats(conn: duckdb.DuckDBPyConnection, table_name: str) -> None:
    try:
        row_count = conn.execute(f'SELECT COUNT(*) FROM {_sql_identifier(table_name)};').fetchone()[0]  # nosec B608 (table name is code-generated)
        logger.info("Table %s row_count=%s", table_name, row_count)
    except Exception:
        logger.exception("Failed to collect row count for %s", table_name)


def _read_manifest(folder_lookup: str) -> dict[str, object]:
    folder = dataiku.Folder(folder_lookup)
    try:
        with folder.get_download_stream(MANIFEST_PATH) as stream:
            payload = json.loads(stream.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else {}
    except Exception:
        logger.info("No existing GOLD manifest found at %s", MANIFEST_PATH)
        return {}


def _write_manifest(folder_lookup: str, manifest: dict[str, object]) -> None:
    folder = dataiku.Folder(folder_lookup)
    content = json.dumps(manifest, sort_keys=True, indent=2).encode("utf-8")
    folder.upload_stream(MANIFEST_PATH, io.BytesIO(content))


def _manifest_watermark(manifest: dict[str, object], key: str) -> str | None:
    watermarks = manifest.get("watermarks")
    if not isinstance(watermarks, dict):
        return None
    value = watermarks.get(key)
    return str(value) if value else None


def _set_manifest_watermark(manifest: dict[str, object], key: str, value: str | None) -> None:
    if not value:
        return
    watermarks = manifest.setdefault("watermarks", {})
    if isinstance(watermarks, dict):
        watermarks[key] = value


def _lookback_adjusted_watermark(watermark: str | None, lookback_days: int) -> str | None:
    if not watermark or lookback_days <= 0:
        return watermark
    try:
        normalized = watermark.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        adjusted = dt - timedelta(days=lookback_days)
        return adjusted.isoformat()
    except Exception:
        logger.warning("Failed to parse watermark %s for lookback adjustment", watermark)
        return watermark


def _list_table_names(conn: duckdb.DuckDBPyConnection) -> set[str]:
    """Return the current DuckDB table names."""

    return {name for (name,) in conn.sql("SHOW TABLES").fetchall()}


def _load_license_profiles(base_dir: Path) -> list[str]:
    """Load known license profiles for wide latest license columns."""

    path = base_dir / "license_profiles.yaml"
    if not path.exists():
        return []

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"Invalid license_profiles.yaml (expected YAML list): {path}")

    out: list[str] = []
    seen: set[str] = set()
    for value in raw:
        if value is None:
            continue
        token = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _inject_wide_license_sql(spec_path: Path, *, base_dir: Path) -> None:
    """Render wide license columns into the wide latest license spec."""

    profiles = _load_license_profiles(base_dir)
    if not profiles:
        raise ValueError("license_profiles.yaml must define at least one known license profile")

    column_lines: list[str] = []
    for profile in profiles:
        upper_profile = profile.upper()
        column_lines.append(
            "      MAX(CASE WHEN max_licenses.license_profile = '{profile}' THEN max_licenses.max_licenses END) AS max_licenses_{column},".format(
                profile=upper_profile,
                column=profile,
            )
        )
        column_lines.append(
            "      MAX(CASE WHEN max_licenses.license_profile = 'SUBLICENSE_{profile}' THEN max_licenses.max_licenses END) AS sublicense_{column},".format(
                profile=upper_profile,
                column=profile,
            )
        )

    if column_lines:
        column_lines[-1] = column_lines[-1].rstrip(",")

    wide_columns = ",\n" + "\n".join(column_lines)
    text = spec_path.read_text(encoding="utf-8")
    if "{wide_columns}" not in text:
        return
    spec_path.write_text(text.replace("{wide_columns}", wide_columns), encoding="utf-8")


def _has_required_tables(conn: duckdb.DuckDBPyConnection, table_names: list[str]) -> bool:
    """Return True when all required DuckDB tables currently exist."""

    existing_tables = _list_table_names(conn)
    return all(table_name in existing_tables for table_name in table_names)


def _collect_user_activity_quality_report(conn: duckdb.DuckDBPyConnection) -> dict:
    report: dict[str, object] = {
        "daily_present": False,
        "project_present": False,
        "daily": {},
        "project": {},
    }

    tables = _list_table_names(conn)

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


def _cleanup_stale_duckdb_files(*, base_dir: Path, max_age_hours: float = 24.0) -> None:
    """Remove old per-run DuckDB files (best-effort)."""

    try:
        if not base_dir.exists():
            return
        cutoff = datetime.now(timezone.utc).timestamp() - (max_age_hours * 3600)
        for p in base_dir.glob("pulse_*.duckdb"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except Exception:
                continue
    except Exception:
        return


def _unique_duckdb_path(*, project_key: str) -> Path:
    """Build a unique DuckDB path per run/user.

    Uses `PULSE_DUCKDB_DIR` as the parent directory if set, otherwise defaults to
    `/tmp/duckdb`.
    """

    base_dir = Path(os.environ.get("PULSE_DUCKDB_DIR", str(Path(tempfile.gettempdir()) / "duckdb")))
    base_dir.mkdir(parents=True, exist_ok=True)

    # Cleanup old runs so /tmp doesn't accumulate forever.
    _cleanup_stale_duckdb_files(base_dir=base_dir)

    user = os.environ.get("DKU_CURRENT_USER") or getpass.getuser()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    token = uuid.uuid4().hex[:8]
    filename = f"pulse_{project_key}_{user}_{ts}_{token}.duckdb"
    filename = filename.replace("/", "_")
    return base_dir / filename



def _slug(value: str) -> str:
    s = str(value or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def _load_dev_toolbox_modules(base_dir: Path) -> list[str]:
    """Load development-activity modules from YAML.

    Expected file: gold_specs/dataiku_dev_tools/toolbox.yaml
    """

    path = base_dir / "dataiku_dev_tools" / "toolbox.yaml"
    if not path.exists():
        return []

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"Invalid toolbox.yaml (expected YAML list): {path}")

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


def _load_category_to_capability(base_dir: Path) -> list[dict]:
    """Load mapping rows for `dim_category_to_capability`.

    Expected file: gold_specs/dataiku_dev_tools/category_to_capability.yaml
    """

    path = base_dir / "dataiku_dev_tools" / "category_to_capability.yaml"
    if not path.exists():
        return []

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"Invalid category_to_capability.yaml (expected YAML list): {path}")

    rows: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if not item.get("dataiku_category") or not item.get("capability"):
            continue
        row = dict(item)
        row["dataiku_category"] = _slug(str(row["dataiku_category"]))
        row["capability"] = _slug(str(row["capability"]))
        # Defaults
        row.setdefault("capability_order", 1)
        row.setdefault("category_order", 1)
        row.setdefault("capability_display_name", str(item.get("capability_display_name") or row["capability"]))
        row.setdefault("category_display_name", str(item.get("category_display_name") or row["dataiku_category"]))
        row.setdefault("is_dev_activity", True)
        rows.append(row)

    return rows


def _build_dim_category_to_capability(conn: duckdb.DuckDBPyConnection, *, base_dir: Path) -> str:
    """Build `dim_category_to_capability` from YAML mapping."""

    rows = [r for r in _load_category_to_capability(base_dir) if _slug(str(r.get("capability") or "")) != "uncategorized"]

    conn.execute(
        """
        CREATE OR REPLACE TABLE dim_category_to_capability AS
        SELECT
          CAST(NULL AS VARCHAR) AS dataiku_category,
          CAST(NULL AS VARCHAR) AS capability,
          CAST(NULL AS INTEGER) AS capability_order,
          CAST(NULL AS INTEGER) AS category_order,
          CAST(NULL AS VARCHAR) AS capability_display_name,
          CAST(NULL AS VARCHAR) AS category_display_name,
          CAST(NULL AS BOOLEAN) AS is_dev_activity
        WHERE 1=0;
        """.strip()
    )

    if not rows:
        return "dim_category_to_capability"

    insert_rows = [
        (
            r.get("dataiku_category"),
            r.get("capability"),
            int(r.get("capability_order") or 1),
            int(r.get("category_order") or 1),
            r.get("capability_display_name"),
            r.get("category_display_name"),
            bool(r.get("is_dev_activity")),
        )
        for r in rows
    ]

    conn.executemany(
        """
        INSERT INTO dim_category_to_capability (
          dataiku_category,
          capability,
          capability_order,
          category_order,
          capability_display_name,
          category_display_name,
          is_dev_activity
        ) VALUES (?, ?, ?, ?, ?, ?, ?);
        """.strip(),
        insert_rows,
    )

    return "dim_category_to_capability"


def _build_dim_addon_feature_flags(conn: duckdb.DuckDBPyConnection) -> str:
    """Build global addon availability flags from latest instance addon rows."""

    conn.execute(
        """
        CREATE OR REPLACE TABLE dim_addon_feature_flags AS
        SELECT
          addon_key,
          BOOL_OR(CASE WHEN try_cast(addon_enabled AS BOOLEAN) IS TRUE THEN TRUE ELSE FALSE END) AS enabled_any_instance
        FROM base_license_addon_licenses_latest
        GROUP BY addon_key
        ORDER BY addon_key;
        """.strip()
    )
    return "dim_addon_feature_flags"


def _load_object_activity_modules(base_dir: Path) -> list[str]:
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
    ip_address_expr = "COALESCE(" + ", ".join(ip_sources) + ")" if ip_sources else "NULL"

    def _has_column(name: str) -> bool:
        return name.lower() in view_columns

    def _null_if_empty(expr: str) -> str:
        return f"NULLIF(TRIM(CAST({expr} AS VARCHAR)), '')"

    def _path_segment(expr: str, marker: str) -> str:
        normalized_expr = f"split_part(split_part(CAST({expr} AS VARCHAR), '?', 1), '#', 1)"
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
        return _null_if_empty(f"json_extract_string(e.extras, '{path}')")

    def _first_non_empty(*exprs: str) -> str:
        return "COALESCE(" + ", ".join(exprs) + ")"

    row_filter = "authuser IS NOT NULL"

    if module in {"datasets", "dataset"}:
        object_type = "dataset"
        object_key_expr = _path_segment("callpath", "/datasets/")
    elif module in {"visual_recipes", "misc_recipes", "prepare"}:
        object_type = "recipe"
        object_key_expr = _path_segment("callpath", "/recipes/")
    elif module == "webapps":
        object_type = "web_application"
        normalized_webapp_expr = _null_if_empty("e.webappid") if _has_column("webappid") else "NULL"
        callpath_webapp_expr = _path_segment("e.callpath", "/webapps/")
        authvia_webapp_expr = _null_if_empty(
            "CASE WHEN strpos(CAST(e.authvia AS VARCHAR), 'ticket:Standard webapp backend: ') > 0 THEN split_part(split_part(split_part(CAST(e.authvia AS VARCHAR), 'ticket:Standard webapp backend: ', 2), ' ', 2), ',', 1) ELSE NULL END"
        )
        extras_webapp_expr = _json_text('$.webappid')
        object_key_expr = _clean_identifier(
            _first_non_empty(
                normalized_webapp_expr,
                authvia_webapp_expr,
                extras_webapp_expr,
                callpath_webapp_expr,
            )
        )
    elif module == "charts_dashboard":
        dashboard_key_expr = _path_segment("callpath", "/dashboards/")
        insight_key_expr = _first_non_empty(
            _path_segment("callpath", "/insights/"),
            _json_text('$.insightId'),
            _json_text('$.insightid'),
            _json_text('$.dashboardInsightId'),
        )
        object_type = (
            f"CASE WHEN {dashboard_key_expr} IS NOT NULL THEN 'dashboard' ELSE 'insight' END"
        )
        object_key_expr = _clean_identifier(_first_non_empty(dashboard_key_expr, insight_key_expr))
    elif module == "apis":
        object_type = "api_service"
        object_key_expr = _clean_identifier(
            _first_non_empty(
                _first_non_empty(_path_segment("callpath", "/api-services/"), _path_segment("callpath", "/api_services/")),
                _first_non_empty(_path_segment("callpath", "/api-endpoints/"), _path_segment("callpath", "/api_endpoints/")),
                _json_text('$.serviceId'),
                _json_text('$.apiServiceId'),
                _json_text('$.apiserviceid'),
                _json_text('$.serviceid'),
            )
        )
        row_filter = f"{row_filter} AND {object_key_expr} IS NOT NULL"
    elif module == "application_designer":
        object_type = "dataiku_application"
        object_key_expr = _clean_identifier(
            _first_non_empty(
                _path_segment("callpath", "/applications/"),
                _json_text('$.applicationId'),
                _json_text('$.applicationid'),
                _json_text('$.appId'),
                _json_text('$.appid'),
                _json_text('$.projectKey'),
                _json_text('$.projectkey'),
                _null_if_empty("project_key")
            )
        )
        row_filter = (
            f"{row_filter} AND {object_key_expr} IS NOT NULL"
            " AND msgtype <> 'application-open'"
        )
    else:
        object_type = module
        object_key_expr = "NULL"

    project_key_expr = (
        f"COALESCE(project_key, {_path_segment('callpath', '/projects/')}, "
        "json_extract_string(extras, '$.projectKey'), json_extract_string(extras, '$.projectkey'))"
        if object_type == "dataiku_application"
        else "project_key"
    )

    return "".join(
        [
            "SELECT\n",
            "  try_cast(COALESCE(timestamp, date) AS TIMESTAMP) AS timestamp,\n",
            "  instance_name,\n",
            "  COALESCE(authuser, user) AS login,\n",
            "  msgtype AS event_name,\n",
            "  e.dataiku_category AS event_category,\n",
            "  m.capability AS canonical_capability,\n",
            f"  {project_key_expr} AS project_key,\n",
            f"  '{object_type}' AS object_type,\n",
            f"  {object_key_expr} AS object_key,\n",
            f"  {object_key_expr} AS object_name,\n",
            "  NULL AS instance_url,\n",
            "  NULL AS group_names,\n",
            "  NULL AS session_id,\n",
            f"  {ip_address_expr} AS ip_address,\n",
            "  NULL AS user_agent,\n",
            "  extras AS details_json,\n",
            "  try_cast(run_ts AS TIMESTAMP) AS run_timestamp,\n",
            "  CAST(year AS INTEGER) AS year,\n",
            "  CAST(month AS INTEGER) AS month,\n",
            "  CAST(day AS INTEGER) AS day\n",
            f"FROM {view_name} e\n",
            "LEFT JOIN dim_category_to_capability m\n",
            "  ON m.dataiku_category = e.dataiku_category\n",
            f"WHERE {row_filter}",
        ]
    )


def _build_fact_object_activity_events(
    conn: duckdb.DuckDBPyConnection,
    *,
    ctx,
    base_dir: Path,
) -> str:
    modules = _load_object_activity_modules(base_dir)
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

    sql = "CREATE OR REPLACE TABLE fact_object_activity_events AS\n" + "\nUNION ALL\n".join(branches) + ";"
    conn.execute(sql)
    _log_table_stats(conn, "fact_object_activity_events")

    # Backward compatibility: some downstream objects still reference the old base name.
    conn.execute(
        "CREATE OR REPLACE VIEW \"base_object_activity_events\" AS SELECT * FROM fact_object_activity_events;"
    )
    return "fact_object_activity_events"


def _build_fact_dev_activity_events(
    conn: duckdb.DuckDBPyConnection,
    *,
    ctx,
    base_dir: Path,
) -> str:
    """Build `fact_dev_activity_events` from selected event_mapping modules."""

    modules = _load_dev_toolbox_modules(base_dir)
    if not modules:
        return ""

    branches: list[str] = []

    for mod in modules:
        view_name = f"v_event_mapping__{_slug(mod)}"
        created = _create_event_mapping_module_view(conn, ctx=ctx, module=mod, view_name=view_name)
        if not created:
            continue

        branches.append(
            f"""
            SELECT
              try_cast(COALESCE(timestamp, date) AS TIMESTAMP) AS timestamp,
              instance_name,
              authuser AS login,
              msgtype,
              msgtypebase,
              dataiku_category,
              project_key,
              callpath,
              extras,
              try_cast(run_ts AS TIMESTAMP) AS run_timestamp,
              CAST(year AS INTEGER) AS year,
              CAST(month AS INTEGER) AS month,
              CAST(day AS INTEGER) AS day
            FROM {view_name}
            """.strip()  # nosec B608 (view_name is generated by this recipe)
        )

    if not branches:
        return ""

    sql = (
        "CREATE OR REPLACE TABLE fact_dev_activity_events AS\n"
        + "\nUNION ALL\n".join(branches)
        + ";"
    )

    conn.execute(sql)
    _log_table_stats(conn, "fact_dev_activity_events")
    return "fact_dev_activity_events"


def _build_fact_user_activity_daily(
    conn: duckdb.DuckDBPyConnection,
    *,
    ctx,
) -> str:
    """Build `fact_user_activity_daily` from hourly SILVER parquet.

    This aggregates activity across all projects.

    Notes:
    - We normalize logins to lower-case (`login_norm`) for case-insensitive identity.
    - A separate table `fact_user_activity_project_daily` keeps project grain.
    """

    view_name, _skip_reason = create_silver_view(conn=conn, ctx=ctx, category="users", module="user_activity")
    if not view_name:
        return ""
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
        """.strip()  # nosec B608 (view_name is generated by create_silver_view)
    )
    _log_table_stats(conn, "fact_user_activity_daily")

    return "fact_user_activity_daily"


def _build_fact_user_activity_project_daily(
    conn: duckdb.DuckDBPyConnection,
    *,
    ctx,
) -> str:
    """Build `fact_user_activity_project_daily` from hourly SILVER parquet.

    This preserves project grain to support "top projects" drilldowns.

    Notes:
    - Identity is case-insensitive using `login_norm`.
    - We include `last_activity_at` to help UI sort/filter.
    """

    view_name, _skip_reason = create_silver_view(conn=conn, ctx=ctx, category="users", module="user_activity")
    if not view_name:
        return ""
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
        """.strip()  # nosec B608 (view_name is generated by create_silver_view)
    )
    _log_table_stats(conn, "fact_user_activity_project_daily")

    return "fact_user_activity_project_daily"


def _fact_dev_activity_events_select(*, base_dir: Path) -> str:
    modules = _load_dev_toolbox_modules(base_dir)
    if not modules:
        return ""

    branches: list[str] = []
    for mod in modules:
        view_name = f"v_event_mapping__{_slug(mod)}"
        branches.append(
            f"""
            SELECT
              try_cast(COALESCE(timestamp, date) AS TIMESTAMP) AS timestamp,
              instance_name,
              authuser AS login,
              msgtype,
              msgtypebase,
              dataiku_category,
              project_key,
              callpath,
              extras,
              try_cast(run_ts AS TIMESTAMP) AS run_timestamp,
              CAST(year AS INTEGER) AS year,
              CAST(month AS INTEGER) AS month,
              CAST(day AS INTEGER) AS day
            FROM {_sql_identifier(view_name)}
            """.strip()  # nosec B608 (view name is generated by this recipe)
        )
    return "\nUNION ALL\n".join(branches)


def _incremental_where_sql(*, watermark: str | None, timestamp_expr: str) -> str:
    if not watermark:
        return ""
    escaped = watermark.replace("'", "''")
    return f" WHERE {timestamp_expr} >= TIMESTAMP '{escaped}'"


def _fact_dev_activity_events_select_incremental(*, base_dir: Path, watermark: str | None) -> str:
    modules = _load_dev_toolbox_modules(base_dir)
    if not modules:
        return ""

    branches: list[str] = []
    where_sql = _incremental_where_sql(
        watermark=watermark,
        timestamp_expr="try_cast(COALESCE(timestamp, date) AS TIMESTAMP)",
    )
    for mod in modules:
        view_name = f"v_event_mapping__{_slug(mod)}"
        branches.append(
            f"""
            SELECT
              try_cast(COALESCE(timestamp, date) AS TIMESTAMP) AS timestamp,
              instance_name,
              authuser AS login,
              msgtype,
              msgtypebase,
              dataiku_category,
              project_key,
              callpath,
              extras,
              try_cast(run_ts AS TIMESTAMP) AS run_timestamp,
              CAST(year AS INTEGER) AS year,
              CAST(month AS INTEGER) AS month,
              CAST(day AS INTEGER) AS day
            FROM {_sql_identifier(view_name)}{where_sql}
            """.strip()  # nosec B608 (view name is generated by this recipe; watermark clause is escaped)
        )
    return "\nUNION ALL\n".join(branches)


def _fact_object_activity_events_select(
    conn: duckdb.DuckDBPyConnection,
    *,
    base_dir: Path,
) -> str:
    modules = _load_object_activity_modules(base_dir)
    if not modules:
        return ""

    branches: list[str] = []
    for mod in modules:
        view_name = f"v_event_mapping__{_slug(mod)}"
        branches.append(_object_activity_branch_sql(conn, module=_slug(mod), view_name=view_name))
    return "\nUNION ALL\n".join(branches)


def _fact_object_activity_events_select_incremental(
    conn: duckdb.DuckDBPyConnection,
    *,
    base_dir: Path,
    watermark: str | None,
) -> str:
    sql = _fact_object_activity_events_select(conn, base_dir=base_dir)
    if not sql or not watermark:
        return sql
    escaped = watermark.replace("'", "''")
    return f"SELECT * FROM ({sql}) incremental_source WHERE timestamp >= TIMESTAMP '{escaped}'"  # nosec B608 (sql is recipe-generated; watermark is escaped)


def _read_gold_table_view(
    conn: duckdb.DuckDBPyConnection,
    *,
    gold_ctx,
    table_name: str,
    view_name: str,
) -> bool:
    root = gold_ctx.folder_root.strip("/")
    if root:
        root = f"{root}/"
    if not gold_ctx.bucket_or_container:
        return False

    if table_name in {"fact_dev_activity_events", "fact_object_activity_events"}:
        path = f"{gold_ctx.blob_header}://{gold_ctx.bucket_or_container}/{root}gold/{table_name}/**/*.parquet"
    else:
        path = f"{gold_ctx.blob_header}://{gold_ctx.bucket_or_container}/{root}gold/{table_name}.parquet"

    try:
        conn.execute(
            f"CREATE OR REPLACE VIEW {_sql_identifier(view_name)} AS SELECT * FROM read_parquet('{path}', hive_partitioning = true, union_by_name = true);"  # nosec B608 (view name and parquet path are recipe-controlled)
        )
        return True
    except duckdb.IOException:
        return False


def _merge_latest_table_incrementally(
    conn: duckdb.DuckDBPyConnection,
    *,
    table_name: str,
    source_view_name: str,
    previous_view_name: str,
    partition_keys: list[str],
    watermark: str | None,
) -> bool:
    if not watermark:
        return False

    escaped = watermark.replace("'", "''")
    partition_clause = ", ".join(partition_keys)
    sql = f"""
    CREATE OR REPLACE TABLE {_sql_identifier(table_name)} AS
    WITH incremental_source AS (
      SELECT *
      FROM {_sql_identifier(source_view_name)}
      WHERE run_ts >= TIMESTAMP '{escaped}'
    ),
    combined AS (
      SELECT * FROM {_sql_identifier(previous_view_name)}
      UNION ALL
      SELECT * FROM incremental_source
    ),
    ranked AS (
      SELECT
        *,
        ROW_NUMBER() OVER (
          PARTITION BY {partition_clause}
          ORDER BY run_ts DESC, partition_date DESC
        ) AS rn
      FROM combined
    )
    SELECT * EXCLUDE (rn)
    FROM ranked
    WHERE rn = 1;
    """.strip()  # nosec B608 (identifiers are code-generated; watermark is escaped)
    _time_query(conn, sql, label=f"merge:{table_name}")
    return True


def _copy_partitioned_query_to_gold(
    conn: duckdb.DuckDBPyConnection,
    *,
    select_sql: str,
    path: str,
    label: str,
) -> None:
    query = (
        "COPY (\n"
        f"{select_sql}\n"
        ") TO '{path}' (\n"
        "  FORMAT 'PARQUET',\n"
        "  OVERWRITE TRUE,\n"
        "  PARTITION_BY (instance_name, year, month, day)\n"
        ");"
    )
    _time_query(conn, query, label=label)


def run() -> dict:
    # Recipe output folder is used as the GOLD destination.
    # (Later steps will unload parquet here.)
    gold_folder_lookup = _resolve_gold_folder_lookup()

    # Resolve source storage from the project running the recipe by default.
    # Allow overrides (ex: cross-project hub/worker layouts) via env var.
    project_key = os.environ.get("PULSE_SOURCE_PROJECT_KEY") or dataiku.default_project_key()
    ensure_managed_folder(
        project_key=project_key,
        folder_lookup="partitioned_data",
    )
    ctx = build_storage_context(project_key=project_key, folder_lookup="partitioned_data")

    # 4. Custom edits
    recipe_config = get_recipe_config() or {}

    unload_behavior = recipe_config.get("unload_behavior", "duckdb")
    build_dev_activity = bool(recipe_config.get("build_dev_activity", True))
    build_object_activity = bool(recipe_config.get("build_object_activity", True))
    manifest_enabled = bool(recipe_config.get("incremental_enabled", True))
    lookback_days = int(recipe_config.get("lookback_days", 3) or 3)

    # The output managed folder is the GOLD destination.
    # Dataiku recipe helpers usually return a managed folder id.
    ensure_managed_folder(
        project_key=dataiku.default_project_key(),
        folder_lookup=gold_folder_lookup,
    )
    gold_ctx = build_storage_context(project_key=dataiku.default_project_key(), folder_lookup=gold_folder_lookup)

    # For now: always reset before build (keeps it deterministic).
    # Use a unique per-run DuckDB file to avoid cross-user permission issues.
    setup = prepare_duckdb(
        ctx=ctx,
        read_only=False,
        reset=True,
        db_path=_unique_duckdb_path(project_key=ctx.project_key),
    )

    failed_tables: list[str] = []
    user_activity_quality: dict[str, object] = {
        "daily_present": False,
        "project_present": False,
        "daily": {},
        "project": {},
    }
    base_tables: list[str] = []
    storage_info: dict = {}
    manifest: dict[str, object] = {}

    blob_header = ctx.blob_header
    if not blob_header:
        raise ValueError(f"Unsupported connection type: {ctx.connection_type}")

    # This recipe assumes GOLD and `partitioned_data` share the same connection.
    if gold_ctx.connection_name != ctx.connection_name or gold_ctx.connection_type != ctx.connection_type:
        raise ValueError(
            "GOLD output folder must share the same backing connection as `partitioned_data` "
            f"(silver: {ctx.connection_name}/{ctx.connection_type}, gold: {gold_ctx.connection_name}/{gold_ctx.connection_type})"
        )

    try:
        storage_info = {
            "provider": setup.provider,
            "credential_mode": setup.credential_mode,
            "db_path": str(setup.db_path),
        }
        manifest = _read_manifest(gold_folder_lookup) if manifest_enabled else {}

        # Build GOLD tables from spec files.
        #
        # Current scope: project/instance metadata specs.
        # (Audit tables follow a different pattern and will be handled separately.)
        # Locate gold specs from the installed python-lib package.
        #
        # In DSS, custom recipe code is executed from a job folder (as an inlined script),
        # so `__file__` does not point to the plugin checkout. Deriving paths from the
        # imported package is stable.
        import data_collection.pulse_duckdb.gold_builder as gold_builder_module

        gold_builder_path = Path(gold_builder_module.__file__).resolve()
        base_dir = gold_builder_path.parent / "gold_specs"
        spec_paths = sorted(
            list((base_dir / "project").glob("base_*.yaml"))
            + list((base_dir / "instance").glob("base_*.yaml"))
        )

        skipped_tables: list[dict[str, str]] = []
        step_timings: list[dict[str, object]] = []

        def run_timed(label: str, fn):
            started = time.monotonic()
            logger.info("Starting step: %s", label)
            result = fn()
            elapsed = time.monotonic() - started
            logger.info("Completed step: %s in %.1fs", label, elapsed)
            step_timings.append({"step": label, "seconds": round(elapsed, 1)})
            return result

        for spec_path in spec_paths:
            if spec_path.name == "base_license_limits_wide_latest.yaml":
                if not _has_required_tables(
                    setup.conn,
                    ["base_license_status_latest", "base_license_max_licenses_latest"],
                ):
                    continue
                profiles = _load_license_profiles(base_dir / "instance")
                if not profiles:
                    raise ValueError(
                        "license_profiles.yaml must define at least one known license profile"
                    )

                column_lines: list[str] = []
                for profile in profiles:
                    upper_profile = profile.upper()
                    column_lines.append(
                        "      MAX(CASE WHEN max_licenses.license_profile = '{profile}' THEN max_licenses.max_licenses END) AS max_licenses_{column},".format(
                            profile=upper_profile,
                            column=profile,
                        )
                    )
                    column_lines.append(
                        "      MAX(CASE WHEN max_licenses.license_profile = 'SUBLICENSE_{profile}' THEN max_licenses.max_licenses END) AS sublicense_{column},".format(
                            profile=upper_profile,
                            column=profile,
                        )
                    )

                if column_lines:
                    column_lines[-1] = column_lines[-1].rstrip(",")

                sql_params = {"wide_columns": ",\n" + "\n".join(column_lines)}
            else:
                sql_params = None

            spec = load_gold_spec(spec_path, sql_params=sql_params)

            # Ensure the upstream SILVER view exists.
            if spec.category and spec.module:
                view_name = spec.view_table_name or f"v_{spec.category}__{spec.module}"
                created_view, skip_reason = create_silver_view(
                    conn=setup.conn,
                    ctx=ctx,
                    category=spec.category,
                    module=spec.module,
                    view_name=view_name,
                )
                if not created_view:
                    skipped_tables.append({"table": spec.name, "reason": skip_reason or "no data"})
                    continue

            apply_gold_spec(setup.conn, spec)

        current_tables = _list_table_names(setup.conn)

        if "base_license_addon_licenses_latest" in current_tables:
            _build_dim_addon_feature_flags(setup.conn)

        # Build development activity dimension + event fact table.
        #
        # Both are configured in `gold_specs/dataiku_dev_tools/*`.
        _build_dim_category_to_capability(setup.conn, base_dir=base_dir)
        _build_fact_dev_activity_events(setup.conn, ctx=ctx, base_dir=base_dir)

        # Build hourly → daily rollups for user activity.
        _build_fact_user_activity_daily(setup.conn, ctx=ctx)
        _build_fact_user_activity_project_daily(setup.conn, ctx=ctx)
        user_activity_quality = _collect_user_activity_quality_report(setup.conn)
        logger.info("User activity quality report: %s", user_activity_quality)

        # Build object-level activity events (used for asset/product activity rollups).
        _build_fact_object_activity_events(setup.conn, ctx=ctx, base_dir=base_dir)

        # Build the products registry mapping table.
        #
        # This is a static mapping (YAML -> table) shipped with the plugin. It does
        # not depend on whether the customer has actually collected a given product
        # type yet.
        products_specs_dir = base_dir / "dataiku_products"
        registry_columns = [
            "product_type",
            "source_table",
            "instance_name_col",
            "project_key_col",
            "key_col",
            "name_col",
            "subtype_col",
            "owner_col",
            "last_modified_by_col",
            "created_at_col",
            "updated_at_col",
            "where_sql",
            "spec_file",
        ]

        setup.conn.execute(
            """
            CREATE OR REPLACE TABLE base_dataiku_products_registry AS
            SELECT
              CAST(NULL AS VARCHAR) AS product_type,
              CAST(NULL AS VARCHAR) AS source_table,
              CAST(NULL AS VARCHAR) AS instance_name_col,
              CAST(NULL AS VARCHAR) AS project_key_col,
              CAST(NULL AS VARCHAR) AS key_col,
              CAST(NULL AS VARCHAR) AS name_col,
              CAST(NULL AS VARCHAR) AS subtype_col,
              CAST(NULL AS VARCHAR) AS owner_col,
              CAST(NULL AS VARCHAR) AS last_modified_by_col,
              CAST(NULL AS VARCHAR) AS created_at_col,
              CAST(NULL AS VARCHAR) AS updated_at_col,
              CAST(NULL AS VARCHAR) AS where_sql,
              CAST(NULL AS VARCHAR) AS spec_file
            WHERE 1=0;
            """.strip()
        )

        if products_specs_dir.exists():
            import yaml

            seen_product_types: set[str] = set()
            registry_rows: list[tuple] = []

            for path in sorted(products_specs_dir.glob("*.yaml")):
                payload = yaml.safe_load(path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError(f"Invalid product registry YAML (expected mapping): {path}")

                product_type = str(payload.get("product_type") or "").strip()
                if not product_type:
                    raise ValueError(f"Missing product_type in {path}")

                if product_type in seen_product_types:
                    raise ValueError(f"Duplicate product_type={product_type!r} in registry YAMLs")
                seen_product_types.add(product_type)

                row = tuple(
                    (payload.get(c) if c != "spec_file" else path.name)
                    for c in registry_columns
                )
                registry_rows.append(row)

            if registry_rows:
                placeholders = ",".join(["?"] * len(registry_columns))
                setup.conn.executemany(
                    f"INSERT INTO base_dataiku_products_registry ({', '.join(registry_columns)}) VALUES ({placeholders});",  # nosec B608 (table is fixed; columns are fixed by code)
                    registry_rows,
                )


        # Unload curated GOLD tables.
        #
        # - `base_*`: existing contract
        # - `fact_*`: event-level facts (may be partitioned)
        current_tables = _list_table_names(setup.conn)
        base_tables = sorted(name for name in current_tables if name.startswith("base_"))
        dim_tables = sorted(name for name in current_tables if name.startswith("dim_"))
        fact_tables = sorted(name for name in current_tables if name.startswith("fact_"))

        unloaded_tables = (
            base_tables
            + [t for t in dim_tables if t not in base_tables]
            + [t for t in fact_tables if t not in base_tables and t not in dim_tables]
        )

        streamed_event_tables: set[str] = set()

        if unload_behavior == "duckdb":
            root = gold_ctx.folder_root.strip("/")
            if root:
                root = f"{root}/"

            if not gold_ctx.bucket_or_container:
                raise ValueError("Could not resolve GOLD bucket/container")

            for module_name in set(_load_dev_toolbox_modules(base_dir) + _load_object_activity_modules(base_dir)):
                _create_event_mapping_module_view(
                    setup.conn,
                    ctx=ctx,
                    module=module_name,
                    view_name=f"v_event_mapping__{_slug(module_name)}",
                )

            if build_dev_activity and _load_dev_toolbox_modules(base_dir):
                path = f"{blob_header}://{gold_ctx.bucket_or_container}/{root}gold/fact_dev_activity_events"
                dev_watermark = _lookback_adjusted_watermark(
                    _manifest_watermark(manifest, "fact_dev_activity_events") if manifest_enabled else None,
                    lookback_days,
                )
                select_sql = _fact_dev_activity_events_select_incremental(base_dir=base_dir, watermark=dev_watermark)
                if not select_sql:
                    select_sql = _fact_dev_activity_events_select(base_dir=base_dir)
                if select_sql:
                    run_timed(
                        "unload:fact_dev_activity_events_streamed",
                        lambda select_sql=select_sql, path=path: _copy_partitioned_query_to_gold(
                            setup.conn,
                            select_sql=select_sql,
                            path=path,
                            label="copy:fact_dev_activity_events",
                        ),
                    )
                    streamed_event_tables.add("fact_dev_activity_events")
                    max_ts = setup.conn.execute("SELECT CAST(MAX(run_timestamp) AS VARCHAR) FROM fact_dev_activity_events;").fetchone()[0] if "fact_dev_activity_events" in fact_tables else None
                    _set_manifest_watermark(manifest, "fact_dev_activity_events", max_ts)

            if build_object_activity and _load_object_activity_modules(base_dir):
                path = f"{blob_header}://{gold_ctx.bucket_or_container}/{root}gold/fact_object_activity_events"
                object_watermark = _lookback_adjusted_watermark(
                    _manifest_watermark(manifest, "fact_object_activity_events") if manifest_enabled else None,
                    lookback_days,
                )
                select_sql = _fact_object_activity_events_select_incremental(
                    setup.conn,
                    base_dir=base_dir,
                    watermark=object_watermark,
                )
                if not select_sql:
                    select_sql = _fact_object_activity_events_select(setup.conn, base_dir=base_dir)
                if select_sql:
                    run_timed(
                        "unload:fact_object_activity_events_streamed",
                        lambda select_sql=select_sql, path=path: _copy_partitioned_query_to_gold(
                            setup.conn,
                            select_sql=select_sql,
                            path=path,
                            label="copy:fact_object_activity_events",
                        ),
                    )
                    streamed_event_tables.add("fact_object_activity_events")
                    max_ts = setup.conn.execute("SELECT CAST(MAX(run_timestamp) AS VARCHAR) FROM fact_object_activity_events;").fetchone()[0] if "fact_object_activity_events" in fact_tables else None
                    _set_manifest_watermark(manifest, "fact_object_activity_events", max_ts)

        for table_name in unloaded_tables:
            if table_name in streamed_event_tables:
                logger.info("Skipping table unload for %s because it was already streamed directly", table_name)
                continue
            # Partition large event tables by instance+day to keep queries fast.
            if table_name in {"fact_dev_activity_events", "fact_object_activity_events"}:
                destination = f"gold/{table_name}"
            else:
                destination = f"gold/{table_name}.parquet"

            logger.info("Unloading %s to %s...", table_name, destination)

            if unload_behavior == "duckdb":
                try:
                    # Build blob URL to write into the GOLD managed folder location.
                    # This mirrors the legacy `settings.py` approach.
                    root = gold_ctx.folder_root.strip("/")
                    if root:
                        root = f"{root}/"

                    if not gold_ctx.bucket_or_container:
                        raise ValueError("Could not resolve GOLD bucket/container")

                    path = f"{blob_header}://{gold_ctx.bucket_or_container}/{root}{destination}"

                    if table_name == "fact_dev_activity_events":
                        # Write partitioned parquet for efficient downstream reads.
                        query = (
                            "COPY (\n"
                            "  SELECT\n"
                            "    timestamp,\n"
                            "    instance_name,\n"
                            "    login,\n"
                            "    msgtype,\n"
                            "    msgtypebase,\n"
                            "    dataiku_category,\n"
                            "    project_key,\n"
                            "    callpath,\n"
                            "    extras,\n"
                            "    run_timestamp,\n"
                            "    year,\n"
                            "    month,\n"
                            "    day\n"
                            "  FROM fact_dev_activity_events\n"
                            ") TO '{path}' (\n"
                            "  FORMAT 'PARQUET',\n"
                            "  OVERWRITE TRUE,\n"
                            "  PARTITION_BY (instance_name, year, month, day)\n"
                            ");"
                        ).format(path=path)  # nosec B608 (path is derived from managed folder context)

                    elif table_name == "fact_object_activity_events":
                        query = (
                            "COPY (\n"
                            "  SELECT\n"
                            "    instance_name,\n"
                            "    timestamp,\n"
                            "    login,\n"
                            "    event_name,\n"
                            "    event_category,\n"
                            "    canonical_capability,\n"
                            "    project_key,\n"
                            "    object_type,\n"
                            "    object_key,\n"
                            "    object_name,\n"
                            "    instance_url,\n"
                            "    group_names,\n"
                            "    session_id,\n"
                            "    ip_address,\n"
                            "    user_agent,\n"
                            "    details_json,\n"
                            "    run_timestamp,\n"
                            "    year,\n"
                            "    month,\n"
                            "    day\n"
                            "  FROM fact_object_activity_events\n"
                            ") TO '{path}' (\n"

                            "  FORMAT 'PARQUET',\n"
                            "  OVERWRITE TRUE,\n"
                            "  PARTITION_BY (instance_name, year, month, day)\n"
                            ");"
                        ).format(path=path)  # nosec B608 (path is derived from managed folder context)

                    else:
                        query = f"COPY {table_name} TO '{path}' (FORMAT 'PARQUET', OVERWRITE TRUE);"  # nosec B608 (table_name comes from SHOW TABLES; path is derived)

                    logger.debug(query)
                    setup.conn.execute(query)
                except Exception as e:
                    logger.error("Failed to unload %s: %s", table_name, e)
                    failed_tables.append(table_name)

            elif unload_behavior == "dataiku":
                try:
                    unload_df = setup.conn.execute(f"SELECT * FROM {table_name};").df()  # nosec B608 (table_name comes from SHOW TABLES)
                    buf = io.BytesIO()
                    unload_df.to_parquet(buf, compression="gzip", engine="pyarrow", index=False)
                    buf.seek(0)
                    content = buf.read()

                    folder = dataiku.Folder(gold_folder_lookup)
                    if table_name in {"fact_dev_activity_events", "fact_object_activity_events"}:
                        raise ValueError(
                            "unload_behavior='dataiku' is not supported for partitioned event tables; "
                            "use unload_behavior='duckdb'"
                        )
                    folder.upload_stream(destination, content)

                except Exception as e:
                    logger.error("Dataiku unload failed for %s: %s", table_name, e)
                    failed_tables.append(table_name)

            else:
                raise ValueError(f"Unknown unload behavior: {unload_behavior!r}")

        if manifest_enabled:
            manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
            _write_manifest(gold_folder_lookup, manifest)
    finally:
        setup.conn.close()

    if skipped_tables:
        logger.warning("Gold tables skipped (no silver data): %s", skipped_tables)

    return {
        "ok": len(failed_tables) == 0,
        "failed_tables": failed_tables,
        "skipped_tables": skipped_tables,
        "source_project_key": project_key,
        "connection_type": ctx.connection_type,
        "connection_name": ctx.connection_name,
        **storage_info,
        "gold_output_folder": gold_folder_lookup,
        "unload_behavior": unload_behavior,
        "build_dev_activity": build_dev_activity,
        "build_object_activity": build_object_activity,
        "manifest_enabled": manifest_enabled,
        "lookback_days": lookback_days,
        "unloaded_tables": unloaded_tables,
        "step_timings": step_timings,
        "manifest": manifest,
        "user_activity_quality": user_activity_quality,
    }


run()
