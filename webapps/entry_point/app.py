import json
import os
import re
import threading

# Ensure plugin python-lib is importable before DuckDB init.
import pulse_plugin  # noqa: F401

from flask import Flask, jsonify, request, send_from_directory

import logging

import settings
from pulse_duckdb.engine.create_conn import create_connection
from pulse_duckdb.engine.init_db import ensure_database_ready
from pulse_duckdb.engine.query import query_df


# Resolve paths relative to this file so the app is relocatable
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
build_folder = os.path.join(BASE_DIR, "frontend", "build")

app = Flask(__name__, static_folder=build_folder)

# Captures the last DuckDB init result for debugging.
DUCKDB_INIT_REPORT: dict | None = None

_DUCKDB_INIT_GUARD = threading.Lock()
_DUCKDB_INIT_STARTED = False


def _run_duckdb_init() -> None:
    global DUCKDB_INIT_REPORT

    try:
        if settings.PULSE_USE_DUMMY_DATA:
            from pulse_duckdb.engine.dummy_db import rebuild_dummy_database

            logging.getLogger(__name__).info(
                "Rebuilding DuckDB with dummy data (path=%s)",
                settings.DUCKDB_PATH,
            )
            DUCKDB_INIT_REPORT = rebuild_dummy_database()
        else:
            logging.getLogger(__name__).info(
                "Ensuring DuckDB is ready (path=%s, auto_load=%s)",
                settings.DUCKDB_PATH,
                settings.PULSE_AUTO_LOAD_GOLD_TABLES,
            )
            DUCKDB_INIT_REPORT = ensure_database_ready()
    except Exception as e:
        logging.getLogger(__name__).exception("DuckDB init raised unexpectedly")
        DUCKDB_INIT_REPORT = {"ok": False, "initialized": False, "gold_loaded": False, "error": str(e)}


def _start_duckdb_init_async() -> None:
    """Kick off DuckDB init without blocking HTTP requests."""

    global _DUCKDB_INIT_STARTED

    if not settings.PULSE_AUTO_INIT_DUCKDB or settings.DUCKDB_READ_ONLY:
        return

    with _DUCKDB_INIT_GUARD:
        if _DUCKDB_INIT_STARTED:
            return
        _DUCKDB_INIT_STARTED = True

        logging.getLogger(__name__).info("Starting DuckDB init in background thread")
        t = threading.Thread(target=_run_duckdb_init, name="pulse_duckdb_init", daemon=True)
        t.start()


# Start initialization in background at process start.
_start_duckdb_init_async()


# ----------------------------------------------------------------------------
# Basic health endpoint
# ----------------------------------------------------------------------------
@app.route("/api/status")
def status():
    # Ensure init has been kicked off even if module import happened in a weird context.
    _start_duckdb_init_async()
    return jsonify(
        {
            "status": "Online",
            "msg": "Flask is talking to React!",
            "duckdb_init": DUCKDB_INIT_REPORT,
        }
    )


def _df_records(df):
    # Ensures timestamps/dates are JSON serializable.
    return json.loads(df.to_json(orient="records", date_format="iso"))


def _to_jsonable(value):
    # DuckDB array_agg returns numpy arrays; convert for jsonify().
    try:
        import numpy as np

        if isinstance(value, np.ndarray):
            return value.tolist()
    except Exception:
        pass

    return value


def _parse_csv_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [v for v in (x.strip() for x in value.split(",")) if v]


_MD5_RE = re.compile(r"^[a-fA-F0-9]{32}$")


def _is_md5(value: str | None) -> bool:
    if not value:
        return False
    return bool(_MD5_RE.match(str(value).strip()))


def _extract_description_from_extras(extras: str | None) -> str | None:
    if not extras:
        return None
    try:
        payload = json.loads(extras)
    except Exception:
        return None

    if isinstance(payload, dict):
        for key in ["description", "desc", "short_description", "shortDescription"]:
            v = payload.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()

        # Fallback: first non-empty key containing "description".
        for k, v in payload.items():
            if "description" in str(k).lower() and isinstance(v, str) and v.strip():
                return v.strip()

    return None


# Map catalog object types to their underlying metadata history tables (best-effort)
# and their primary key column name.
_OBJECT_EXTRAS_SOURCES: dict[str, dict[str, str | bool]] = {
    # Build → Assets
    "project": {"table": "base_projects_instance_metadata_history", "key_col": "project_key", "project_scoped": False},
    "dataset": {"table": "base_datasets_project_metadata_history", "key_col": "datasets_name", "project_scoped": True},
    "recipe": {"table": "base_recipes_project_metadata_history", "key_col": "recipes_name", "project_scoped": True},
    "scenario": {"table": "base_scenarios_project_metadata_history", "key_col": "scenarios_id", "project_scoped": True},
    # Build → Products
    "api_service": {"table": "base_api_services_project_metadata_history", "key_col": "api_services_id", "project_scoped": True},
    "agent_tool": {"table": "base_agent_tools_project_metadata_history", "key_col": "agent_tools_id", "project_scoped": True},
    "insight": {"table": "base_insights_project_metadata_history", "key_col": "insights_id", "project_scoped": True},
    "web_application": {"table": "base_webapps_project_metadata_history", "key_col": "webapps_id", "project_scoped": True},
    "dataiku_application": {"table": "base_apps_instance_metadata_history", "key_col": "apps_appid", "project_scoped": False},
}


# Map product types to the event-level object_type used by fact_object_activity_events.
_PRODUCT_TO_EVENT_OBJECT_TYPE = {
    "api_service": "api_endpoint",
    "insight": "dashboard",
    "agent_tool": "agent",
}


# ----------------------------------------------------------------------------
# Startup flags
# ----------------------------------------------------------------------------


def _get_default_project_handle():
    try:
        import dataiku

        get_default_project = getattr(dataiku, "get_default_project", None)
        if callable(get_default_project):
            project = get_default_project()
            if hasattr(project, "get_variables"):
                return project

        api_client = getattr(dataiku, "api_client", None)
        if callable(api_client):
            client = api_client()
            return getattr(client, "get_default_project")()
    except Exception:
        return None

    return None


def _read_standard_project_var(key: str):
    project = _get_default_project_handle()
    if project is None:
        return None

    try:
        vars_ = project.get_variables() or {}
        standard = vars_.get("standard") or {}
        return standard.get(key)
    except Exception:
        return None


@app.route("/api/startup/flags")
def startup_flags():
    """Feature flags exposed to the React SPA.

    Flags are read from DSS project variables so features can be enabled per-project.

    Currently supported flags:
    - `userActivity`: enabled when `standard.user_activity` is JSON boolean `true`.
    """

    user_activity_raw = _read_standard_project_var("user_activity")
    user_activity_enabled = user_activity_raw is True

    return jsonify({"ok": True, "flags": {"userActivity": user_activity_enabled}})


# ----------------------------------------------------------------------------
# DuckDB-backed application endpoints
# ----------------------------------------------------------------------------
@app.route("/api/build/assets")
def build_assets_list():
    q = (request.args.get("q") or "").strip()
    instances = _parse_csv_list(request.args.get("instances"))
    projects = _parse_csv_list(request.args.get("projects"))
    types = _parse_csv_list(request.args.get("types"))
    owner = (request.args.get("owner") or "").strip()
    sort = (request.args.get("sort") or "updated_desc").strip()

    limit = int(request.args.get("limit") or 25)
    offset = int(request.args.get("offset") or 0)

    where = []
    params: list[object] = []

    # Build the WHERE clause against a single derived table alias (`t`)
    # to avoid ambiguous column references after joins.
    if q:
        where.append(
            "(lower(t.object_name) LIKE ? OR lower(t.object_key) LIKE ? OR lower(t.project_key) LIKE ? OR lower(t.instance_name) LIKE ? OR lower(t.owner_login) LIKE ?)"
        )
        qlike = f"%{q.lower()}%"
        params.extend([qlike, qlike, qlike, qlike, qlike])

    if instances:
        where.append(f"t.instance_name IN ({','.join(['?'] * len(instances))})")
        params.extend(instances)

    if projects:
        where.append(f"t.project_key IN ({','.join(['?'] * len(projects))})")
        params.extend(projects)

    if types:
        where.append(f"t.object_type IN ({','.join(['?'] * len(types))})")
        params.extend(types)

    if owner:
        where.append("lower(t.owner_login) LIKE ?")
        params.append(f"%{owner.lower()}%")

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    order_by = "t.updated_at DESC NULLS LAST"
    if sort == "updated_asc":
        order_by = "t.updated_at ASC NULLS LAST"
    elif sort == "activity_desc":
        order_by = "t.activity_30d DESC NULLS LAST"
    elif sort == "name_asc":
        order_by = "t.object_name ASC NULLS LAST"

    asset_count_sql = "\n".join(
        [
            "SELECT COUNT(*) AS n",
            "FROM (",
            "  SELECT",
            "    idx.*, COALESCE(act.activity_30d, 0) AS activity_30d",
            "  FROM base_asset_index idx",
            "  LEFT JOIN asset_activity_30d act",
            "    ON act.instance_name = idx.instance_name",
            "   AND act.project_key = idx.project_key",
            "   AND act.object_type = idx.object_type",
            "   AND act.object_key = idx.object_key",
            ") t",
            where_sql + ";",
        ]
    )
    count_df = query_df(asset_count_sql, params)
    total = int(count_df.iloc[0]["n"]) if len(count_df.index) else 0

    asset_rows_sql = "\n".join(
        [
            "SELECT",
            "  md5(concat_ws('|', t.instance_name, t.project_key, t.object_type, t.object_key)) AS assetId,",
            "  t.instance_name AS instanceName,",
            "  t.project_key AS projectKey,",
            "  t.object_type AS objectType,",
            "  t.object_key AS objectKey,",
            "  t.object_name AS objectName,",
            "  t.owner_login AS ownerLogin,",
            "  t.updated_at AS updatedAt,",
            "  t.activity_30d AS activity30d",
            "FROM (",
            "  SELECT",
            "    idx.*, COALESCE(act.activity_30d, 0) AS activity_30d",
            "  FROM base_asset_index idx",
            "  LEFT JOIN asset_activity_30d act",
            "    ON act.instance_name = idx.instance_name",
            "   AND act.project_key = idx.project_key",
            "   AND act.object_type = idx.object_type",
            "   AND act.object_key = idx.object_key",
            ") t",
            where_sql,
            "ORDER BY " + order_by,
            "LIMIT ? OFFSET ?;",
        ]
    )
    rows_df = query_df(asset_rows_sql, params + [limit, offset])

    return jsonify({"ok": True, "total": total, "rows": _df_records(rows_df)})


@app.route("/api/build/assets/facets")
def build_assets_facets():
    df = query_df(
        """
        SELECT
          array_agg(DISTINCT instance_name ORDER BY instance_name) AS instances,
          array_agg(DISTINCT project_key ORDER BY project_key) AS projects,
          array_agg(DISTINCT object_type ORDER BY object_type) AS types,
          array_agg(DISTINCT owner_login ORDER BY owner_login) AS owners
        FROM base_asset_index;
        """
    )
    row = df.iloc[0].to_dict() if len(df.index) else {}
    row = {k: _to_jsonable(v) for k, v in row.items()}
    return jsonify({"ok": True, **row})




@app.route("/api/build/products/facets")
def build_products_facets():
    df = query_df(
        """
        SELECT
          array_agg(DISTINCT instance_name ORDER BY instance_name) AS instances,
          array_agg(DISTINCT project_key ORDER BY project_key) AS projects,
          array_agg(DISTINCT product_type ORDER BY product_type) AS types,
          array_agg(DISTINCT owner_login ORDER BY owner_login) AS owners
        FROM final_build_products_catalog;
        """
    )
    row = df.iloc[0].to_dict() if len(df.index) else {}
    row = {k: _to_jsonable(v) for k, v in row.items()}
    return jsonify({"ok": True, **row})


@app.route("/api/build/products")
def build_products_list():
    q = (request.args.get("q") or "").strip()
    instances = _parse_csv_list(request.args.get("instances"))
    projects = _parse_csv_list(request.args.get("projects"))
    types = _parse_csv_list(request.args.get("types"))
    owner = (request.args.get("owner") or "").strip()
    sort = (request.args.get("sort") or "updated_desc").strip()

    limit = int(request.args.get("limit") or 25)
    offset = int(request.args.get("offset") or 0)

    where = []
    params: list[object] = []

    if q:
        where.append(
            "(lower(product_name) LIKE ? OR lower(product_key) LIKE ? OR lower(project_key) LIKE ? OR lower(instance_name) LIKE ? OR lower(owner_login) LIKE ?)"
        )
        qlike = f"%{q.lower()}%"
        params.extend([qlike, qlike, qlike, qlike, qlike])

    if instances:
        where.append(f"instance_name IN ({','.join(['?'] * len(instances))})")
        params.extend(instances)

    if projects:
        where.append(f"project_key IN ({','.join(['?'] * len(projects))})")
        params.extend(projects)

    if types:
        where.append(f"product_type IN ({','.join(['?'] * len(types))})")
        params.extend(types)

    if owner:
        where.append("lower(owner_login) LIKE ?")
        params.append(f"%{owner.lower()}%")

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    order_by = "updated_at DESC NULLS LAST"
    if sort == "updated_asc":
        order_by = "updated_at ASC NULLS LAST"
    elif sort == "activity_desc":
        order_by = "activity_30d DESC NULLS LAST"
    elif sort == "name_asc":
        order_by = "product_name ASC NULLS LAST"

    product_count_sql = "\n".join(["SELECT COUNT(*) AS n FROM final_build_products_catalog", where_sql + ";"])
    count_df = query_df(product_count_sql, params)
    total = int(count_df.iloc[0]["n"]) if len(count_df.index) else 0

    product_rows_sql = "\n".join(
        [
            "SELECT",
            "  product_id AS assetId,",
            "  instance_name AS instanceName,",
            "  project_key AS projectKey,",
            "  product_type AS objectType,",
            "  product_key AS objectKey,",
            "  product_name AS objectName,",
            "  owner_login AS ownerLogin,",
            "  updated_at AS updatedAt,",
            "  activity_30d AS activity30d",
            "FROM final_build_products_catalog",
            where_sql,
            "ORDER BY " + order_by,
            "LIMIT ? OFFSET ?;",
        ]
    )
    rows_df = query_df(product_rows_sql, params + [limit, offset])

    return jsonify({"ok": True, "total": total, "rows": _df_records(rows_df)})


def _asset_id_expr_for_build_assets() -> str:
    # Must match `/api/build/assets` list endpoint.
    return "md5(concat_ws('|', instance_name, project_key, object_type, object_key))"


def _asset_id_expr_for_build_products() -> str:
    # Must match final_build_products_catalog.product_id.
    return "md5(concat_ws('|', instance_name, project_key, product_type, product_key))"


def _fetch_usage_and_related_assets(*, project_key: str | None, object_type: str, object_key: str) -> tuple[int, list[dict]]:
    # Usage summary is defined as all-time sum of events.
    # Related assets is the per-(instance_name, project_key) breakdown.
    params: list[object] = [object_type, object_key]
    where = "object_type = ? AND object_key = ?"
    if project_key is not None:
        where += " AND project_key = ?"
        params.append(project_key)

    usage_sql = "\n".join(["SELECT COUNT(*) AS n FROM v_object_activity_events WHERE", where + ";"])
    usage_df = query_df(usage_sql, params)
    usage = int(usage_df.iloc[0]["n"]) if len(usage_df.index) else 0

    related_sql = "\n".join(
        [
            "SELECT",
            "  instance_name AS instanceName,",
            "  project_key AS projectKey,",
            "  COUNT(*) AS eventCount",
            "FROM v_object_activity_events",
            "WHERE " + where,
            "GROUP BY 1, 2",
            "ORDER BY eventCount DESC, instanceName, projectKey;",
        ]
    )
    related_df = query_df(related_sql, params)

    return usage, _df_records(related_df)


def _fetch_description(*, instance_name: str, project_key: str | None, object_type: str, object_key: str) -> str | None:
    spec = _OBJECT_EXTRAS_SOURCES.get(object_type)
    if not spec:
        return None

    table = str(spec["table"])
    key_col = str(spec["key_col"])
    project_scoped = bool(spec.get("project_scoped", False))

    # Not all sources are guaranteed to have `extras`, and some may have it NULL.
    where = ["instance_name = ?", f"{key_col} = ?"]
    params: list[object] = [instance_name, object_key]

    if project_scoped:
        if not project_key:
            return None
        where.append("project_key = ?")
        params.append(project_key)

    description_sql = "\n".join(
        [
            "SELECT extras FROM",
            table,
            "WHERE " + " AND ".join(where),
            "LIMIT 1;",
        ]
    )
    df = query_df(description_sql, params)
    if not len(df.index):
        return None

    extras = df.iloc[0].get("extras")
    return _extract_description_from_extras(extras if isinstance(extras, str) else None)


@app.route("/api/build/assets/details")
def build_assets_details():
    asset_id = (request.args.get("assetId") or "").strip()
    if not _is_md5(asset_id):
        return jsonify({"ok": False, "error": "Invalid or missing assetId"}), 400

    # Ensure DuckDB is ready (lazy init is already started in background).
    _start_duckdb_init_async()

    # Find the selected asset keys.
    asset_lookup_sql = "\n".join(
        [
            "SELECT",
            "  instance_name AS instanceName,",
            "  project_key AS projectKey,",
            "  object_type AS objectType,",
            "  object_key AS objectKey,",
            "  object_name AS objectName,",
            "  owner_login AS ownerLogin,",
            "  updated_at AS updatedAt",
            "FROM base_asset_index",
            "WHERE " + _asset_id_expr_for_build_assets() + " = ?",
            "LIMIT 1;",
        ]
    )
    df = query_df(asset_lookup_sql, [asset_id])

    if not len(df.index):
        return jsonify({"ok": False, "error": "Asset not found"}), 404

    row = _df_records(df)[0]
    instance_name = str(row.get("instanceName") or "")
    project_key = str(row.get("projectKey") or "")
    object_type = str(row.get("objectType") or "")
    object_key = str(row.get("objectKey") or "")

    usage, related_assets = _fetch_usage_and_related_assets(
        project_key=project_key or None,
        object_type=object_type,
        object_key=object_key,
    )

    description = None
    try:
        description = _fetch_description(
            instance_name=instance_name,
            project_key=project_key or None,
            object_type=object_type,
            object_key=object_key,
        )
    except Exception:
        # Best-effort only
        description = None

    return jsonify(
        {
            "ok": True,
            "asset": row,
            "capturedInfo": {"description": description},
            "usageSummary": {"eventsAllTime": usage},
            "relatedAssets": related_assets,
        }
    )


@app.route("/api/build/products/details")
def build_products_details():
    asset_id = (request.args.get("assetId") or "").strip()
    if not _is_md5(asset_id):
        return jsonify({"ok": False, "error": "Invalid or missing assetId"}), 400

    _start_duckdb_init_async()

    product_lookup_sql = "\n".join(
        [
            "SELECT",
            "  instance_name AS instanceName,",
            "  project_key AS projectKey,",
            "  product_type AS objectType,",
            "  product_key AS objectKey,",
            "  product_name AS objectName,",
            "  owner_login AS ownerLogin,",
            "  updated_at AS updatedAt",
            "FROM final_build_products_catalog",
            "WHERE product_id = ?",
            "LIMIT 1;",
        ]
    )
    df = query_df(product_lookup_sql, [asset_id])

    if not len(df.index):
        return jsonify({"ok": False, "error": "Product not found"}), 404

    row = _df_records(df)[0]
    instance_name = str(row.get("instanceName") or "")
    project_key = str(row.get("projectKey") or "")
    product_type = str(row.get("objectType") or "")
    product_key = str(row.get("objectKey") or "")

    event_object_type = _PRODUCT_TO_EVENT_OBJECT_TYPE.get(product_type, product_type)

    usage, related_assets = _fetch_usage_and_related_assets(
        project_key=project_key or None,
        object_type=event_object_type,
        object_key=product_key,
    )

    description = None
    try:
        description = _fetch_description(
            instance_name=instance_name,
            project_key=project_key or None,
            object_type=product_type,
            object_key=product_key,
        )
    except Exception:
        description = None

    return jsonify(
        {
            "ok": True,
            "asset": row,
            "capturedInfo": {"description": description},
            "usageSummary": {"eventsAllTime": usage},
            "relatedAssets": related_assets,
        }
    )


@app.route("/api/build/products/metrics")
def build_products_metrics():
    df_by_type = query_df(
        """
        SELECT product_type AS type, product_type AS label, COUNT(*) AS count
        FROM final_build_products_catalog
        GROUP BY 1
        ORDER BY count DESC;
        """
    )

    df_by_project = query_df(
        """
        SELECT project_key AS label, COUNT(*) AS value
        FROM final_build_products_catalog
        GROUP BY 1
        ORDER BY value DESC;
        """
    )

    df_by_instance = query_df(
        """
        SELECT instance_name AS label, COUNT(*) AS value
        FROM final_build_products_catalog
        GROUP BY 1
        ORDER BY value DESC;
        """
    )

    df_over_time = query_df(
        """
        WITH buckets AS (
          SELECT
            strftime(date_trunc('month', created_at), '%Y-%m') AS bucket,
            COUNT(*) AS value
          FROM final_build_products_catalog
          GROUP BY 1
        )
        SELECT substr(bucket, 6, 2) AS label, value
        FROM buckets
        ORDER BY bucket DESC
        LIMIT 12;
        """
    )

    # Return in chronological order for chart
    over_time = list(reversed(_df_records(df_over_time)))

    return jsonify(
        {
            "ok": True,
            "summary": _df_records(df_by_type),
            "productsByType": [{"label": r["label"], "value": r["count"]} for r in _df_records(df_by_type)],
            "productsByProject": _df_records(df_by_project),
            "productsByInstance": _df_records(df_by_instance),
            "productsOverTime": over_time,
        }
    )


@app.route("/api/build/products/metrics/type/<product_type>")
def build_products_metrics_type(product_type: str):
    df_by_instance = query_df(
        """
        SELECT instance_name AS instanceName, COUNT(*) AS count
        FROM final_build_products_catalog
        WHERE product_type = ?
        GROUP BY 1
        ORDER BY count DESC;
        """,
        [product_type],
    )

    df_top_owners = query_df(
        """
        SELECT owner_login AS ownerLogin, COUNT(*) AS count
        FROM final_build_products_catalog
        WHERE product_type = ?
        GROUP BY 1
        ORDER BY count DESC
        LIMIT 20;
        """,
        [product_type],
    )

    return jsonify(
        {
            "ok": True,
            "breakdownByInstance": _df_records(df_by_instance),
            "topOwners": _df_records(df_top_owners),
        }
    )


@app.route("/api/build/development-activity")
def build_development_activity_summary():
    window_days = int(request.args.get("days") or 30)

    df_daily = query_df(
        """
        WITH daily AS (
          SELECT
            date_trunc('day', timestamp) AS day,
            COUNT(*) AS value
          FROM final_build_development_activity_events
          WHERE timestamp >= now() - (? * INTERVAL 1 DAY)
          GROUP BY 1
        )
        SELECT strftime(day, '%m-%d') AS label, value
        FROM daily
        ORDER BY day DESC
        LIMIT 12;
        """,
        [window_days],
    )

    df_by_cap = query_df(
        """
        SELECT capability AS label, COUNT(*) AS value
        FROM final_build_development_activity_events
        WHERE timestamp >= now() - (? * INTERVAL 1 DAY)
        GROUP BY 1
        ORDER BY value DESC;
        """,
        [window_days],
    )

    df_by_cap_cat = query_df(
        """
        SELECT
          concat(capability, ' / ', dataiku_category) AS label,
          COUNT(*) AS value
        FROM final_build_development_activity_events
        WHERE timestamp >= now() - (? * INTERVAL 1 DAY)
        GROUP BY 1
        ORDER BY value DESC
        LIMIT 20;
        """,
        [window_days],
    )

    df_top_users = query_df(
        """
        SELECT login AS label, COUNT(*) AS value
        FROM final_build_development_activity_events
        WHERE timestamp >= now() - (? * INTERVAL 1 DAY)
        GROUP BY 1
        ORDER BY value DESC
        LIMIT 15;
        """,
        [window_days],
    )

    return jsonify(
        {
            "ok": True,
            "activityDaily": list(reversed(_df_records(df_daily))),
            "byCapability": _df_records(df_by_cap),
            "byCategory": _df_records(df_by_cap_cat),
            "topUsers": _df_records(df_top_users),
        }
    )


@app.route("/api/build/development-activity/capability/<capability>")
def build_development_activity_capability(capability: str):
    window_days = int(request.args.get("days") or 30)

    df_summary = query_df(
        """
        SELECT
          COUNT(*) AS events,
          COUNT(DISTINCT login) AS users,
          COUNT(DISTINCT project_key) AS projects,
          COUNT(DISTINCT instance_name) AS instances
        FROM final_build_development_activity_events
        WHERE capability = ?
          AND timestamp >= now() - (? * INTERVAL 1 DAY);
        """,
        [capability, window_days],
    )

    df_daily = query_df(
        """
        WITH daily AS (
          SELECT date_trunc('day', timestamp) AS day, COUNT(*) AS value
          FROM final_build_development_activity_events
          WHERE capability = ?
            AND timestamp >= now() - (? * INTERVAL 1 DAY)
          GROUP BY 1
        )
        SELECT strftime(day, '%m-%d') AS label, value
        FROM daily
        ORDER BY day DESC
        LIMIT 12;
        """,
        [capability, window_days],
    )

    df_categories = query_df(
        """
        SELECT dataiku_category AS label, COUNT(*) AS value
        FROM final_build_development_activity_events
        WHERE capability = ?
          AND timestamp >= now() - (? * INTERVAL 1 DAY)
        GROUP BY 1
        ORDER BY value DESC
        LIMIT 20;
        """,
        [capability, window_days],
    )

    df_tags = query_df(
        """
        SELECT base_tag AS label, COUNT(*) AS value
        FROM final_build_development_activity_events
        WHERE capability = ?
          AND timestamp >= now() - (? * INTERVAL 1 DAY)
        GROUP BY 1
        ORDER BY value DESC
        LIMIT 20;
        """,
        [capability, window_days],
    )

    df_top_users = query_df(
        """
        SELECT login AS label, COUNT(*) AS value
        FROM final_build_development_activity_events
        WHERE capability = ?
          AND timestamp >= now() - (? * INTERVAL 1 DAY)
        GROUP BY 1
        ORDER BY value DESC
        LIMIT 15;
        """,
        [capability, window_days],
    )

    return jsonify(
        {
            "ok": True,
            "summary": _df_records(df_summary)[0] if len(df_summary.index) else {},
            "activityDaily": list(reversed(_df_records(df_daily))),
            "categories": _df_records(df_categories),
            "tags": _df_records(df_tags),
            "topUsers": _df_records(df_top_users),
        }
    )


@app.route("/api/build/development-activity/user/<login>")
def build_development_activity_user(login: str):
    window_days = int(request.args.get("days") or 30)

    df_summary = query_df(
        """
        SELECT
          COUNT(*) AS events,
          COUNT(DISTINCT project_key) AS projects,
          COUNT(DISTINCT instance_name) AS instances
        FROM final_build_development_activity_events
        WHERE login = ?
          AND timestamp >= now() - (? * INTERVAL 1 DAY);
        """,
        [login, window_days],
    )

    df_daily = query_df(
        """
        WITH daily AS (
          SELECT date_trunc('day', timestamp) AS day, COUNT(*) AS value
          FROM final_build_development_activity_events
          WHERE login = ?
            AND timestamp >= now() - (? * INTERVAL 1 DAY)
          GROUP BY 1
        )
        SELECT strftime(day, '%m-%d') AS label, value
        FROM daily
        ORDER BY day DESC
        LIMIT 12;
        """,
        [login, window_days],
    )

    df_capabilities = query_df(
        """
        SELECT capability AS label, COUNT(*) AS value
        FROM final_build_development_activity_events
        WHERE login = ?
          AND timestamp >= now() - (? * INTERVAL 1 DAY)
        GROUP BY 1
        ORDER BY value DESC;
        """,
        [login, window_days],
    )

    df_categories = query_df(
        """
        SELECT dataiku_category AS label, COUNT(*) AS value
        FROM final_build_development_activity_events
        WHERE login = ?
          AND timestamp >= now() - (? * INTERVAL 1 DAY)
        GROUP BY 1
        ORDER BY value DESC
        LIMIT 20;
        """,
        [login, window_days],
    )

    df_tags = query_df(
        """
        SELECT base_tag AS label, COUNT(*) AS value
        FROM final_build_development_activity_events
        WHERE login = ?
          AND timestamp >= now() - (? * INTERVAL 1 DAY)
        GROUP BY 1
        ORDER BY value DESC
        LIMIT 20;
        """,
        [login, window_days],
    )

    return jsonify(
        {
            "ok": True,
            "summary": _df_records(df_summary)[0] if len(df_summary.index) else {},
            "activityDaily": list(reversed(_df_records(df_daily))),
            "capabilities": _df_records(df_capabilities),
            "categories": _df_records(df_categories),
            "tags": _df_records(df_tags),
        }
    )


# ----------------------------------------------------------------------------
# Users (UI-only activity from audit logs)
# ----------------------------------------------------------------------------


@app.route("/api/build/users/facets")
def build_users_facets():
    df = query_df(
        """
        SELECT DISTINCT instance_name
        FROM final_build_user_activity_daily
        WHERE instance_name IS NOT NULL
        ORDER BY 1;
        """
    )
    instances = [r.get("instance_name") for r in _df_records(df) if r.get("instance_name")]
    return jsonify({"ok": True, "instances": instances})


@app.route("/api/build/users/leaderboard")
def build_users_leaderboard():
    window_days = int(request.args.get("days") or 30)
    window_days = max(1, min(365, window_days))

    instance_name = (request.args.get("instance_name") or "").strip() or None

    where = ["day >= CURRENT_DATE - (? * INTERVAL 1 DAY)"]
    params: list[object] = [window_days]

    if instance_name:
        where.append("instance_name = ?")
        params.append(instance_name)

    where_sql = "WHERE " + " AND ".join(where)

    viewing_sql = "\n".join(
        [
            "WITH agg AS (",
            "  SELECT",
            "    lower(trim(login)) AS login_norm,",
            "    MIN(trim(login)) AS login,",
            "    SUM(viewing_actions_count) AS viewing,",
            "    SUM(developing_actions_count) AS developing,",
            "    MAX(day) AS last_activity_day,",
            "    COUNT(DISTINCT instance_name) AS instances",
            "  FROM final_build_user_activity_daily",
            f"  {where_sql}",
            "  GROUP BY 1",
            ")",
            "SELECT",
            "  a.login_norm AS login,",
            "  u.display_name AS displayName,",
            "  u.email AS email,",
            "  u.user_profile AS userProfile,",
            "  u.enabled AS enabled,",
            "  a.viewing AS value,",
            "  a.developing AS developing,",
            "  a.instances AS instances,",
            "  a.last_activity_day AS lastActivityDay",
            "FROM agg a",
            "LEFT JOIN base_users u",
            "  ON lower(trim(u.login)) = a.login_norm",
            "ORDER BY value DESC NULLS LAST",
            "LIMIT 50;",
        ]
    )
    df_viewing = query_df(viewing_sql, params)

    developing_sql = "\n".join(
        [
            "WITH agg AS (",
            "  SELECT",
            "    lower(trim(login)) AS login_norm,",
            "    MIN(trim(login)) AS login,",
            "    SUM(viewing_actions_count) AS viewing,",
            "    SUM(developing_actions_count) AS developing,",
            "    MAX(day) AS last_activity_day,",
            "    COUNT(DISTINCT instance_name) AS instances",
            "  FROM final_build_user_activity_daily",
            f"  {where_sql}",
            "  GROUP BY 1",
            ")",
            "SELECT",
            "  a.login_norm AS login,",
            "  u.display_name AS displayName,",
            "  u.email AS email,",
            "  u.user_profile AS userProfile,",
            "  u.enabled AS enabled,",
            "  a.developing AS value,",
            "  a.viewing AS viewing,",
            "  a.instances AS instances,",
            "  a.last_activity_day AS lastActivityDay",
            "FROM agg a",
            "LEFT JOIN base_users u",
            "  ON lower(trim(u.login)) = a.login_norm",
            "ORDER BY value DESC NULLS LAST",
            "LIMIT 50;",
        ]
    )
    df_developing = query_df(developing_sql, params)

    return jsonify(
        {
            "ok": True,
            "windowDays": window_days,
            "instanceName": instance_name,
            "viewing": _df_records(df_viewing),
            "developing": _df_records(df_developing),
        }
    )


@app.route("/api/build/users/<login>")
def build_user_detail(login: str):
    window_days = int(request.args.get("days") or 30)
    window_days = max(1, min(365, window_days))

    instance_name = (request.args.get("instance_name") or "").strip() or None

    login_norm = (login or "").strip().lower()
    if not login_norm:
        return jsonify({"ok": False, "error": "Missing login"}), 400

    where = ["day >= CURRENT_DATE - (? * INTERVAL 1 DAY)", "lower(trim(login)) = ?"]
    params: list[object] = [window_days, login_norm]

    if instance_name:
        where.append("instance_name = ?")
        params.append(instance_name)

    where_sql = "WHERE " + " AND ".join(where)

    summary_sql = "\n".join(
        [
            "SELECT",
            "  SUM(viewing_actions_count) AS viewing,",
            "  SUM(developing_actions_count) AS developing,",
            "  COUNT(DISTINCT instance_name) AS instances",
            "FROM final_build_user_activity_daily",
            where_sql + ";",
        ]
    )
    df_summary = query_df(summary_sql, params)

    df_user = query_df(
        """
        SELECT
          instance_name,
          login,
          display_name,
          email,
          enabled,
          user_profile,
          group_names,
          last_activity_at
        FROM base_users
        WHERE lower(trim(login)) = ?
        ORDER BY last_activity_at DESC NULLS LAST
        LIMIT 1;
        """.strip(),
        [login_norm],
    )

    daily_sql = "\n".join(
        [
            "SELECT",
            "  CAST(day AS VARCHAR) AS label,",
            "  SUM(viewing_actions_count) AS viewing,",
            "  SUM(developing_actions_count) AS developing",
            "FROM final_build_user_activity_daily",
            where_sql,
            "GROUP BY 1",
            "ORDER BY 1;",
        ]
    )
    df_daily = query_df(daily_sql, params)

    return jsonify(
        {
            "ok": True,
            "login": login_norm,
            "windowDays": window_days,
            "user": _df_records(df_user)[0] if len(df_user.index) else None,
            "summary": _df_records(df_summary)[0] if len(df_summary.index) else {},
            "activityDaily": _df_records(df_daily),
        }
    )


@app.route("/api/build/users/<login>/top-projects")
def build_user_top_projects(login: str):
    window_days = int(request.args.get("days") or 30)
    window_days = max(1, min(365, window_days))

    limit = int(request.args.get("limit") or 10)
    limit = max(1, min(100, limit))

    instance_name = (request.args.get("instance_name") or "").strip() or None

    login_norm = (login or "").strip().lower()
    if not login_norm:
        return jsonify({"ok": False, "error": "Missing login"}), 400

    where = ["day >= CURRENT_DATE - (? * INTERVAL 1 DAY)", "lower(trim(login)) = ?"]
    params: list[object] = [window_days, login_norm]

    if instance_name:
        where.append("instance_name = ?")
        params.append(instance_name)

    where_sql = "WHERE " + " AND ".join(where)

    top_projects_sql = "\n".join(
        [
            "SELECT",
            "  instance_name AS instanceName,",
            "  project_key AS projectKey,",
            "  SUM(viewing_actions_count) AS viewing,",
            "  SUM(developing_actions_count) AS developing",
            "FROM fact_user_activity_project_daily",
            where_sql,
            "GROUP BY 1, 2",
            "ORDER BY developing DESC NULLS LAST, viewing DESC NULLS LAST",
            "LIMIT ?;",
        ]
    )
    df = query_df(top_projects_sql, [*params, limit])

    return jsonify({"ok": True, "rows": _df_records(df)})


# ----------------------------------------------------------------------------
# DuckDB debug endpoints
# ----------------------------------------------------------------------------
@app.route("/api/debug/duckdb/init_status")
def duckdb_init_status():
    return jsonify(
        {
            "ok": True,
            "db_path": str(settings.DUCKDB_PATH),
            "read_only": settings.DUCKDB_READ_ONLY,
            "auto_init": settings.PULSE_AUTO_INIT_DUCKDB,
            "auto_load_gold_tables": settings.PULSE_AUTO_LOAD_GOLD_TABLES,
            "auto_load_replace": settings.PULSE_AUTO_LOAD_REPLACE,
            "init_report": DUCKDB_INIT_REPORT,
        }
    )


@app.route("/api/debug/duckdb/reload", methods=["POST"])
def duckdb_reload():
    # Initialize/refresh the DuckDB and load curated GOLD tables.
    from pulse_duckdb.engine.init_db import ensure_database_ready

    report = ensure_database_ready(load_gold_tables=True, replace_gold_tables=True)
    return jsonify({"ok": True, "db_path": str(settings.DUCKDB_PATH), "load": report})


@app.route("/api/debug/duckdb/tables")
def duckdb_tables():
    """List DuckDB objects (tables + views)."""

    conn = create_connection(read_only=True)
    try:
        df = conn.execute(
            """
            SELECT table_name, table_type
            FROM information_schema.tables
            WHERE table_schema = 'main'
            ORDER BY table_type, table_name;
            """
        ).df()

        objects = df.to_dict(orient="records") if len(df.index) else []
        tables = [r["table_name"] for r in objects if r.get("table_type") == "BASE TABLE"]
        views = [r["table_name"] for r in objects if r.get("table_type") == "VIEW"]

        return jsonify({"ok": True, "tables": tables, "views": views, "objects": objects})
    finally:
        conn.close()


@app.route("/api/debug/duckdb/query")
def duckdb_query():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"ok": False, "error": "Missing query parameter 'q'"}), 400

    try:
        df = query_df(q)
        return jsonify({"ok": True, "rows": _df_records(df)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/debug/duckdb/table/<table_name>")
def duckdb_table_info(table_name: str):
    conn = create_connection(read_only=True)
    try:
        # minimal sanitization: allow only identifier-ish names
        if not table_name.replace("_", "").isalnum():
            return jsonify({"ok": False, "error": "Invalid table name"}), 400

        # Use information_schema so this works for both TABLEs and VIEWs.
        cols_df = conn.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'main' AND table_name = ?
            ORDER BY ordinal_position;
            """,
            [table_name],
        ).df()

        if cols_df.shape[0] == 0:
            return jsonify({"ok": False, "error": f"Object not found: {table_name}"}), 404

        ident = '"' + table_name.replace('"', '') + '"'

        sample_error = None
        sample_df = None
        try:
            sample_sql = "\n".join(["SELECT * FROM", ident, "LIMIT 10;"])
            sample_df = conn.execute(sample_sql).df()
        except Exception as e:
            # Views that depend on missing upstream tables can still exist but fail at query time.
            sample_error = str(e)

        return jsonify(
            {
                "ok": True,
                "table": table_name,
                "columns": _df_records(cols_df),
                "sample": _df_records(sample_df) if sample_df is not None else [],
                "sample_error": sample_error,
            }
        )
    finally:
        conn.close()


# ----------------------------------------------------------------------------
# SPA fallback
# ----------------------------------------------------------------------------
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve(path):
    # `app.static_folder` is typed as Optional[str] in Flask stubs.
    static_folder: str = app.static_folder or build_folder
    if path != "" and os.path.exists(os.path.join(static_folder, path)):
        return send_from_directory(static_folder, path)
    return send_from_directory(static_folder, "index.html")


if __name__ == "__main__":
    app.run(host=settings.HOST, port=settings.PORT)
