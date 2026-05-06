from __future__ import annotations

# Dataiku Pulse Dashboard webapp backend.
#
# This backend serves API endpoints and (optionally) serves the React build as
# static assets. The frontend build is stored under the plugin `resource/`
# folder and `webapps/pulse-dashboard/body.html` points to the build's
# `index.html`.

import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, cast

from flask import Flask, jsonify, request, send_file, send_from_directory

# Resolve repo paths for local dev static serving.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_BUILD_DIR = _REPO_ROOT / "resource" / "pulse-dashboard" / "build"

# In DSS, `app` is injected into the module globals by the webapp runner.
# For local dev runs, this will be missing and we create an app below.
app = cast(Flask | None, globals().get("app"))
_IS_LOCAL_DEV = app is None


logger = logging.getLogger(__name__)

# This backend runs in two contexts:
# - In Dataiku DSS: the Standard webapp backend runner injects a Flask `app`.
#   In that case, DO NOT create a new Flask app here, just register routes.
# - In local development: you may run this module directly and we create a Flask app.

# Shared dashboard backend logic lives under python-lib to keep the webapp folder small.
# In DSS, python-lib is automatically available; in local dev we add it to sys.path.
try:
    from pulse_dashboard import settings as pulse_settings  # type: ignore
    from pulse_dashboard.pulse_duckdb.engine import create_connection, ensure_database_ready, query_df  # type: ignore
except Exception:
    try:
        repo_root = Path(__file__).resolve().parents[2]
        python_lib = repo_root / "python-lib"
        if python_lib.is_dir():
            sys.path.insert(0, str(python_lib))

        from pulse_dashboard import settings as pulse_settings  # type: ignore
        from pulse_dashboard.pulse_duckdb.engine import create_connection, ensure_database_ready, query_df  # type: ignore
    except Exception:
        logger.exception("Failed to import Pulse dashboard libraries")
        pulse_settings = None
        create_connection = None
        ensure_database_ready = None
        query_df = None


if app is None:  # pragma: no cover
    # Local/dev run: serve the packaged React build assets.
    # CRA builds reference assets under `/static/...`.
    static_dir = _BUILD_DIR / "static"
    if static_dir.is_dir():
        app = Flask(__name__, static_folder=str(static_dir), static_url_path="/static")
    else:
        app = Flask(__name__)

# From here on, `app` is always a Flask instance.
app = cast(Flask, app)


@app.route("/__ping", endpoint="pulse_dashboard_ping")
def pulse_dashboard_ping():
    return "OK"


@app.route("/api/status")
def status():
    return jsonify({"status": "Online", "msg": "Backend is running"})


def _read_standard_project_variables() -> dict[str, Any]:
    """Best-effort read of DSS project `standard` variables.

    In local dev runs (outside DSS), this returns an empty dict.
    """

    try:
        import dataiku

        client = dataiku.api_client()
        project = client.get_project(dataiku.default_project_key())
        vars_ = project.get_variables() or {}
        standard = vars_.get("standard") or {}
        return standard if isinstance(standard, dict) else {}
    except Exception:
        return {}


def _read_user_profile_exclude_consumer(standard_vars: dict[str, Any]) -> list[str]:
    """Profiles excluded by the "no consumer" license filter.

    Configured in DSS project variables:
    - `standard.user_profile_exclude_consumer`: JSON list of strings

    Fallback default if unset/invalid:
    - ["READER", "AI_CONSUMER"]
    """

    default = ["READER", "AI_CONSUMER"]
    raw = standard_vars.get("user_profile_exclude_consumer")

    if isinstance(raw, list):
        items = [str(v).strip() for v in raw if v is not None and str(v).strip()]
        if items:
            return [s.upper() for s in items]
        return default

    if isinstance(raw, str):
        parts = [p.strip() for p in raw.split(",")]
        parts = [p for p in parts if p]
        if parts:
            return [p.upper() for p in parts]

    return default


_WINDOW_TO_MONTHS = {
    "this_month": 1,
    "last_3_months": 3,
    "last_12_months": 12,
}


def _parse_window_months(value: str | None) -> int | None:
    if not value:
        return None
    value = str(value).strip().lower()
    months = _WINDOW_TO_MONTHS.get(value)
    return int(months) if months else None


def _parse_license_filter(value: str | None) -> str:
    value = (value or "").strip().lower()
    if value in {"no_consumer", "exclude_consumer", "exclude-consumer"}:
        return "no_consumer"
    return "all_enabled"


@app.route("/api/startup/flags")
def startup_flags():
    """Feature flags + small config exposed to the React SPA.

    Flags are read from DSS project standard variables so features can be enabled
    per project.

    Currently supported flags:
    - `userActivity`: enabled when `standard.user_activity` is JSON boolean `true`.

    Config values:
    - `userProfileExcludeConsumer`: resolved exclusion list for the "no_consumer" toggle,
      read from `standard.user_profile_exclude_consumer`.

    Note:
    - If we cannot resolve the default project / variables, we default to disabled.
    """

    try:
        standard = _read_standard_project_variables()
        user_activity_enabled = standard.get("user_activity") is True
        excluded_profiles = _read_user_profile_exclude_consumer(standard)
        return _ok(
            {
                "flags": {"userActivity": user_activity_enabled},
                "config": {
                    "userProfileExcludeConsumer": excluded_profiles,
                },
            }
        )
    except Exception:
        logger.exception("Failed reading startup flags")
        return _ok(
            {
                "flags": {"userActivity": False},
                "config": {"userProfileExcludeConsumer": ["READER", "AI_CONSUMER"]},
            }
        )


@app.route("/api/startup/duckdb")
def startup_duckdb():
    """Blocking startup initializer.

    This is meant to be called by `webapps/pulse-dashboard/body.html` before the
    React bundle is injected. It forces an eager GOLD load so the dashboard is
    usable immediately after initial render.
    """

    if ensure_database_ready is None:
        return _err("pulse_duckdb not available", status=500)

    started = time.time()
    try:
        report = cast(
            dict[str, Any],
            ensure_database_ready(
                load_gold_tables=True,
                replace_gold_tables=getattr(pulse_settings, "PULSE_AUTO_LOAD_REPLACE", False)
                if pulse_settings is not None
                else False,
            ),
        )
        duration_sec = round(time.time() - started, 3)
        if not bool(report.get("ok", False)):
            return _err(
                "DuckDB initialization failed",
                status=500,
                hint=json.dumps({"durationSec": duration_sec, "load": report}),
            )
        return _ok({"load": report, "durationSec": duration_sec})
    except Exception as e:
        logger.exception("DuckDB startup init failed")
        return _err(str(e), status=500)


@app.route("/api/startup/status")
def startup_status():
    """Read-only health snapshot for the DuckDB-backed dashboard.

    Important: this endpoint should not *create* the DB. It only inspects the
    DuckDB file if it already exists.
    """

    duckdb_path = None
    if pulse_settings is not None:
        duckdb_path = str(getattr(pulse_settings, "DUCKDB_PATH", "") or "")

    exists = False
    size_bytes = None
    if duckdb_path:
        try:
            p = Path(duckdb_path)
            exists = p.exists()
            if exists:
                size_bytes = p.stat().st_size
        except Exception:
            exists = False

    expected_objects = [
        "final_build_catalog",
        "final_build_products_catalog",
        "dev_activity_capability_daily",
        "final_build_development_activity_events",
    ]

    tables: list[str] = []
    present_expected: list[str] = []
    missing_expected: list[str] = []

    if exists and create_connection is not None:
        try:
            conn = create_connection(read_only=True)
            try:
                rows = conn.execute("PRAGMA show_tables;").fetchall()
                tables = sorted([str(r[0]) for r in rows])
            finally:
                conn.close()

            present_expected = [t for t in expected_objects if t in set(tables)]
            missing_expected = [t for t in expected_objects if t not in set(tables)]
        except Exception as e:
            # If we can't open the DB read-only, treat as not-ready.
            missing_expected = list(expected_objects)
            return _ok(
                {
                    "duckdb": {
                        "path": duckdb_path,
                        "exists": exists,
                        "sizeBytes": size_bytes,
                        "openError": str(e),
                    },
                    "ready": False,
                    "expected": {"present": present_expected, "missing": missing_expected},
                    "tables": tables,
                }
            )

    ready = bool(exists and not missing_expected)

    return _ok(
        {
            "duckdb": {"path": duckdb_path, "exists": exists, "sizeBytes": size_bytes},
            "ready": ready,
            "expected": {"present": present_expected, "missing": missing_expected},
            "tables": tables,
        }
    )


# ----------------------------------------------------------------------------
# Static frontend (local dev convenience)
# ----------------------------------------------------------------------------
# In DSS, the HTML/JS are handled by `body.html` + `app.js`.
# For local runs (gunicorn/flask), we serve `body.html` too so local dev mirrors
# the DSS loader behavior (warmup + manifest-driven asset injection).


@app.route("/resource/pulse-dashboard/build/<path:filename>")
def serve_packaged_build(filename: str):  # pragma: no cover
    if not _BUILD_DIR.is_dir():
        return (
            "Pulse dashboard build not found. Expected: "
            f"{_BUILD_DIR}. Run scripts/sync_pulse_dashboard_build.sh to populate it.",
            404,
        )

    filename = (filename or "").lstrip("/")
    candidate = (_BUILD_DIR / filename).resolve()

    try:
        candidate.relative_to(_BUILD_DIR)
    except ValueError:
        return _err("Invalid path", status=400)

    if not candidate.exists() or not candidate.is_file():
        return _err("Not found", status=404)

    return send_from_directory(_BUILD_DIR, filename)


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path: str):  # pragma: no cover
    # Mirror DSS by serving the same loader HTML.
    if _IS_LOCAL_DEV:
        body_path = Path(__file__).resolve().parent / "body.html"
        return send_file(body_path)

    # Fallback: if someone hits this backend outside DSS, try serving the
    # packaged React build directly.
    if not _BUILD_DIR.is_dir():
        return (
            "Pulse dashboard build not found. Expected: "
            f"{_BUILD_DIR}. Run scripts/sync_pulse_dashboard_build.sh to populate it.",
            404,
        )

    path = (path or "").lstrip("/")
    candidate = (_BUILD_DIR / path).resolve()

    # Avoid path traversal outside build dir.
    try:
        candidate.relative_to(_BUILD_DIR)
    except ValueError:
        return _err("Invalid path", status=400)

    if path and candidate.exists() and candidate.is_file():
        return send_from_directory(_BUILD_DIR, path)

    index_path = _BUILD_DIR / "index.html"
    if index_path.exists():
        return send_file(index_path)

    return (
        "Pulse dashboard index.html not found. Build looks incomplete at: "
        f"{_BUILD_DIR}",
        404,
    )


def _df_records(df):
    return json.loads(df.to_json(orient="records", date_format="iso"))


def _ok(payload: dict[str, Any] | None = None, *, status: int = 200):
    data = {"ok": True}
    if payload:
        data.update(payload)
    return jsonify(data), status


def _err(message: str, *, status: int = 400, hint: str | None = None):
    payload: dict[str, Any] = {"ok": False, "error": message}
    if hint:
        payload["hint"] = hint
    return jsonify(payload), status


def _parse_csv_list(value: str | None) -> list[str]:
    if not value:
        return []
    parts = [p.strip() for p in value.split(",")]
    return [p for p in parts if p]


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_ident(name: str) -> str:
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"Unsafe identifier: {name}")
    return name


def _require_duckdb_engine():
    if query_df is None or create_connection is None or ensure_database_ready is None:
        raise RuntimeError("pulse_duckdb not available")

    return (
        cast(Any, query_df),
        cast(Any, create_connection),
        cast(Any, ensure_database_ready),
    )


def _ensure_ready_if_enabled() -> dict[str, Any] | None:
    """Best-effort auto-init for endpoints needing DuckDB."""

    if pulse_settings is None or ensure_database_ready is None:
        return None

    if not getattr(pulse_settings, "PULSE_AUTO_INIT_DUCKDB", False):
        return None

    try:
        return cast(dict[str, Any], ensure_database_ready())
    except Exception:
        logger.exception("DuckDB auto-init failed")
        return {"ok": False, "error": "auto-init failed"}


@app.route("/api/duckdb/query")
def duckdb_query():
    if query_df is None:
        return _err("pulse_duckdb not available", status=500)

    q = (request.args.get("q") or "").strip()
    if not q:
        return _err("Missing query parameter 'q'", status=400)

    _ensure_ready_if_enabled()

    try:
        df = query_df(q)
        return _ok({"rows": _df_records(df)})
    except Exception as e:
        msg = str(e)
        if "database does not exist" in msg:
            return _err(
                msg,
                status=503,
                hint="DuckDB not initialized yet. Load GOLD tables or enable auto-init.",
            )

        logger.exception("duckdb query failed")
        return _err(msg, status=500)


@app.route("/api/debug/duckdb/reload", methods=["POST"])
def debug_duckdb_reload():
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()

        # Force a full reload (even if auto-init is enabled) so this endpoint is
        # a reliable "refresh" button during development and troubleshooting.
        load_report = cast(
            dict[str, Any],
            _ensure_database_ready(load_gold_tables=True, replace_gold_tables=True),
        )

        return _ok({"load": load_report})
    except Exception as e:
        logger.exception("duckdb reload failed")
        return _err(str(e), status=500)


@app.route("/api/debug/duckdb/tables")
def debug_duckdb_tables():
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()
        conn = _create_connection(read_only=True)
        try:
            rows = conn.execute("PRAGMA show_tables;").fetchall()
        finally:
            conn.close()

        tables = sorted([str(r[0]) for r in rows])
        return _ok({"tables": tables})
    except Exception as e:
        logger.exception("duckdb tables failed")
        return _err(str(e), status=500)


@app.route("/api/debug/duckdb/table/<table_name>")
def debug_duckdb_table(table_name: str):
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()
        table_name = _safe_ident(table_name)

        conn = _create_connection(read_only=True)
        try:
            cols_df = conn.execute(f'PRAGMA table_info("{table_name}");').df()
            sample_df = conn.execute(f'SELECT * FROM "{table_name}" LIMIT 10;').df()
        finally:
            conn.close()

        return _ok({"columns": _df_records(cols_df), "sample": _df_records(sample_df)})
    except Exception as e:
        logger.exception("duckdb table info failed")
        return _err(str(e), status=500)


@app.route("/api/build/assets/facets")
def build_assets_facets():
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        instances = (
            _query_df("SELECT DISTINCT instance_name FROM final_build_catalog ORDER BY 1;")["instance_name"]
            .dropna()
            .astype(str)
            .tolist()
        )
        projects = (
            _query_df("SELECT DISTINCT project_key FROM final_build_catalog ORDER BY 1;")["project_key"]
            .dropna()
            .astype(str)
            .tolist()
        )
        types = (
            _query_df("SELECT DISTINCT object_type FROM final_build_catalog ORDER BY 1;")["object_type"]
            .dropna()
            .astype(str)
            .tolist()
        )
        owners = (
            _query_df("SELECT DISTINCT owner_login FROM final_build_catalog ORDER BY 1;")["owner_login"]
            .dropna()
            .astype(str)
            .tolist()
        )

        return _ok({"instances": instances, "projects": projects, "types": types, "owners": owners})
    except Exception as e:
        logger.exception("assets facets failed")
        return _err(str(e), status=500)


@app.route("/api/build/assets")
def build_assets():
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        q = (request.args.get("q") or "").strip()
        owner = (request.args.get("owner") or "").strip()
        instances = _parse_csv_list(request.args.get("instances"))
        projects = _parse_csv_list(request.args.get("projects"))
        types = _parse_csv_list(request.args.get("types"))

        sort = (request.args.get("sort") or "updated_desc").strip()
        limit = int(request.args.get("limit") or 25)
        offset = int(request.args.get("offset") or 0)

        # Guardrails
        limit = max(1, min(5000, limit))
        offset = max(0, offset)

        where = []
        params: list[Any] = []

        if q:
            where.append("(lower(object_name) LIKE ? OR lower(object_key) LIKE ?)")
            qq = f"%{q.lower()}%"
            params.extend([qq, qq])

        if owner:
            where.append("owner_login = ?")
            params.append(owner)

        if instances:
            where.append(f"instance_name IN ({','.join(['?'] * len(instances))})")
            params.extend(instances)

        if projects:
            where.append(f"project_key IN ({','.join(['?'] * len(projects))})")
            params.extend(projects)

        if types:
            where.append(f"object_type IN ({','.join(['?'] * len(types))})")
            params.extend(types)

        where_sql = (" WHERE " + " AND ".join(where)) if where else ""

        order_by = {
            "updated_desc": "updated_at DESC NULLS LAST",
            "updated_asc": "updated_at ASC NULLS LAST",
            "activity_desc": "activity_30d DESC NULLS LAST, updated_at DESC NULLS LAST",
            "name_asc": "object_name ASC NULLS LAST",
        }.get(sort)
        if order_by is None:
            return _err(f"Invalid sort: {sort}")

        count_sql = f"SELECT COUNT(*) AS n FROM final_build_catalog{where_sql};"
        total = int(_query_df(count_sql, params).iloc[0]["n"])

        sql = (
            "SELECT\n"
            "  asset_id AS assetId,\n"
            "  object_name AS objectName,\n"
            "  object_key AS objectKey,\n"
            "  object_type AS objectType,\n"
            "  instance_name AS instanceName,\n"
            "  project_key AS projectKey,\n"
            "  owner_login AS ownerLogin,\n"
            "  updated_at AS updatedAt,\n"
            "  activity_30d AS activity30d\n"
            f"FROM final_build_catalog{where_sql}\n"
            f"ORDER BY {order_by}\n"
            "LIMIT ? OFFSET ?;"
        )
        rows = _query_df(sql, [*params, limit, offset])

        return _ok({"rows": _df_records(rows), "total": total})

    except Exception as e:
        logger.exception("assets query failed")
        return _err(str(e), status=500)


@app.route("/api/build/products/facets")
def build_products_facets():
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        types = (
            _query_df("SELECT DISTINCT product_type FROM final_build_products_catalog ORDER BY 1;")["product_type"]
            .dropna()
            .astype(str)
            .tolist()
        )
        return _ok({"types": types})
    except Exception as e:
        logger.exception("products facets failed")
        return _err(str(e), status=500)


@app.route("/api/build/products")
def build_products():
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        limit = int(request.args.get("limit") or 25)
        offset = int(request.args.get("offset") or 0)
        limit = max(1, min(5000, limit))
        offset = max(0, offset)

        total = int(_query_df("SELECT COUNT(*) AS n FROM final_build_products_catalog;").iloc[0]["n"])

        sql = (
            "SELECT\n"
            "  product_id AS assetId,\n"
            "  product_name AS objectName,\n"
            "  product_key AS objectKey,\n"
            "  product_type AS objectType,\n"
            "  instance_name AS instanceName,\n"
            "  project_key AS projectKey,\n"
            "  owner_login AS ownerLogin,\n"
            "  updated_at AS updatedAt,\n"
            "  activity_30d AS activity30d\n"
            "FROM final_build_products_catalog\n"
            "ORDER BY updated_at DESC NULLS LAST\n"
            "LIMIT ? OFFSET ?;"
        )
        rows = _query_df(sql, [limit, offset])

        return _ok({"rows": _df_records(rows), "total": total})
    except Exception as e:
        logger.exception("products query failed")
        return _err(str(e), status=500)


@app.route("/api/build/development-activity")
def build_development_activity():
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        days = int(request.args.get("days") or 30)
        days = max(1, min(365, days))

        activity_daily_df = _query_df(
            """
            SELECT
              CAST(day AS VARCHAR) AS label,
              SUM(event_count) AS value
            FROM dev_activity_capability_daily
            WHERE day >= current_date - ?::INTEGER
            GROUP BY 1
            ORDER BY 1;
            """.strip(),
            [days],
        )

        by_capability_df = _query_df(
            """
            SELECT
              capability AS label,
              event_count_30d AS value
            FROM dev_activity_capability_30d
            ORDER BY value DESC;
            """.strip()
        )

        by_category_df = _query_df(
            """
            SELECT
              concat_ws(' / ', capability, dataiku_category) AS label,
              event_count_30d AS value
            FROM dev_activity_category_30d
            ORDER BY value DESC;
            """.strip()
        )

        top_users_df = _query_df(
            """
            SELECT
              login AS label,
              event_count_30d AS value
            FROM dev_activity_top_users_30d
            ORDER BY value DESC
            LIMIT 50;
            """.strip()
        )

        activity_daily_rows = []
        for row in _df_records(activity_daily_df):
            label = row.get("label") or row.get("day")
            if label is None:
                continue
            activity_daily_rows.append({"label": label, "value": row.get("value")})

        return _ok(
            {
                "activityDaily": activity_daily_rows,
                "byCapability": _df_records(by_capability_df),
                "byCategory": _df_records(by_category_df),
                "topUsers": _df_records(top_users_df),
            }
        )

    except Exception as e:
        logger.exception("development activity failed")
        return _err(str(e), status=500)


@app.route("/api/build/development-activity/capability/<capability>")
def build_development_activity_capability(capability: str):
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        capability = capability.strip()
        if not capability:
            return _err("Missing capability")

        days = int(request.args.get("days") or 30)
        days = max(1, min(365, days))

        summary_df = _query_df(
            """
            SELECT
              COUNT(*) AS events,
              COUNT(DISTINCT login) AS users,
              COUNT(DISTINCT project_key) AS projects,
              COUNT(DISTINCT instance_name) AS instances
            FROM final_build_development_activity_events
            WHERE capability = ? AND timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY;
            """.strip(),
            [capability, days],
        )
        summary = _df_records(summary_df)[0] if len(summary_df) else None

        activity_daily_df = _query_df(
            """
            SELECT
              CAST(CAST(date_trunc('day', timestamp) AS DATE) AS VARCHAR) AS label,
              COUNT(*) AS value
            FROM final_build_development_activity_events
            WHERE capability = ? AND timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY
            GROUP BY 1
            ORDER BY 1;
            """.strip(),
            [capability, days],
        )

        categories_df = _query_df(
            """
            SELECT
              dataiku_category AS label,
              COUNT(*) AS value
            FROM final_build_development_activity_events
            WHERE capability = ? AND timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY
            GROUP BY 1
            ORDER BY value DESC;
            """.strip(),
            [capability, days],
        )

        tags_df = _query_df(
            """
            SELECT
              base_tag AS label,
              COUNT(*) AS value
            FROM final_build_development_activity_events
            WHERE capability = ? AND timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY
            GROUP BY 1
            ORDER BY value DESC;
            """.strip(),
            [capability, days],
        )

        top_users_df = _query_df(
            """
            SELECT
              login AS label,
              COUNT(*) AS value
            FROM final_build_development_activity_events
            WHERE capability = ? AND timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY
            GROUP BY 1
            ORDER BY value DESC
            LIMIT 50;
            """.strip(),
            [capability, days],
        )

        activity_daily_rows = []
        for row in _df_records(activity_daily_df):
            label = row.get("label") or row.get("day")
            if label is None:
                continue
            activity_daily_rows.append({"label": label, "value": row.get("value")})

        return _ok(
            {
                "summary": summary,
                "activityDaily": activity_daily_rows,
                "categories": _df_records(categories_df),
                "tags": _df_records(tags_df),
                "topUsers": _df_records(top_users_df),
            }
        )

    except Exception as e:
        logger.exception("capability drilldown failed")
        return _err(str(e), status=500)


@app.route("/api/build/development-activity/user/<login>")
def build_development_activity_user(login: str):
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        login = login.strip()
        if not login:
            return _err("Missing login")

        days = int(request.args.get("days") or 30)
        days = max(1, min(365, days))

        summary_df = _query_df(
            """
            SELECT
              COUNT(*) AS events,
              COUNT(DISTINCT project_key) AS projects,
              COUNT(DISTINCT instance_name) AS instances
            FROM final_build_development_activity_events
            WHERE login = ? AND timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY;
            """.strip(),
            [login, days],
        )
        summary = _df_records(summary_df)[0] if len(summary_df) else None

        activity_daily_df = _query_df(
            """
            SELECT
              CAST(CAST(date_trunc('day', timestamp) AS DATE) AS VARCHAR) AS label,
              COUNT(*) AS value
            FROM final_build_development_activity_events
            WHERE login = ? AND timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY
            GROUP BY 1
            ORDER BY 1;
            """.strip(),
            [login, days],
        )

        capabilities_df = _query_df(
            """
            SELECT
              capability AS label,
              COUNT(*) AS value
            FROM final_build_development_activity_events
            WHERE login = ? AND timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY
            GROUP BY 1
            ORDER BY value DESC;
            """.strip(),
            [login, days],
        )

        categories_df = _query_df(
            """
            SELECT
              dataiku_category AS label,
              COUNT(*) AS value
            FROM final_build_development_activity_events
            WHERE login = ? AND timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY
            GROUP BY 1
            ORDER BY value DESC;
            """.strip(),
            [login, days],
        )

        tags_df = _query_df(
            """
            SELECT
              base_tag AS label,
              COUNT(*) AS value
            FROM final_build_development_activity_events
            WHERE login = ? AND timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY
            GROUP BY 1
            ORDER BY value DESC;
            """.strip(),
            [login, days],
        )

        activity_daily_rows = []
        for row in _df_records(activity_daily_df):
            label = row.get("label") or row.get("day")
            if label is None:
                continue
            activity_daily_rows.append({"label": label, "value": row.get("value")})

        return _ok(
            {
                "summary": summary,
                "activityDaily": activity_daily_rows,
                "capabilities": _df_records(capabilities_df),
                "categories": _df_records(categories_df),
                "tags": _df_records(tags_df),
            }
        )

    except Exception as e:
        logger.exception("user drilldown failed")
        return _err(str(e), status=500)


# ----------------------------------------------------------------------------
# Users (UI-only activity from audit logs)
# ----------------------------------------------------------------------------


def _parse_instance_name(value: str | None) -> str | None:
    out = (value or "").strip()
    return out or None


def _parse_login_norm(value: str) -> str:
    return value.strip().lower()


def _sql_placeholders(n: int) -> str:
    return ",".join(["?"] * n)


def _hub_instances_sql_list() -> str:
    """Return a SQL-safe list like `'hub1','hub2'`.

    Used only to render plugin-controlled config into VIEW templates.
    """

    hub_instances = []
    if pulse_settings is not None:
        hub_instances = _parse_csv_list(getattr(pulse_settings, "PULSE_HUB_INSTANCE_NAMES", ""))

    if not hub_instances:
        # Keep the SQL valid; no instance will match this.
        return "'__none__'"

    escaped = ["'" + s.replace("'", "''") + "'" for s in hub_instances]
    return ",".join(escaped)


@app.route("/api/build/users/facets")
def build_users_facets():
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        instances = (
            _query_df(
                """
                SELECT DISTINCT instance_name
                FROM final_users_directory
                WHERE instance_name IS NOT NULL
                ORDER BY 1;
                """.strip()
            )["instance_name"]
            .dropna()
            .astype(str)
            .tolist()
        )

        return _ok({"instances": instances})
    except Exception as e:
        logger.exception("users facets failed")
        return _err(str(e), status=500)


def _window_months_where_sql(*, months: int) -> str:
    # months=3 means: this month + previous 2.
    # We use month boundaries (calendar months) instead of rolling N days.
    months = max(1, min(24, int(months)))
    return f"day >= date_trunc('month', current_date) - INTERVAL {months - 1} MONTH"


@app.route("/api/build/users/kpis")
def build_users_kpis():
    """User directory KPIs for the Users page.

    KPIs are computed from `base_users_instance_metadata_history` so they reflect
    per-instance license/profile state.

    Query parameters:
    - licenseFilter: all_enabled|no_consumer
    - instance_name: optional filter
    """

    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        standard = _read_standard_project_variables()
        excluded_profiles = _read_user_profile_exclude_consumer(standard)

        license_filter = _parse_license_filter(request.args.get("licenseFilter"))
        instance_name = _parse_instance_name(request.args.get("instance_name"))

        exclude_sql = ""
        exclude_params: list[Any] = []
        if license_filter == "no_consumer" and excluded_profiles:
            exclude_sql = (
                f" AND coalesce(upper(trim(users_userprofile)), '') NOT IN ({_sql_placeholders(len(excluded_profiles))})"
            )
            exclude_params = list(excluded_profiles)

        instance_sql = ""
        instance_params: list[Any] = []
        if instance_name:
            instance_sql = " AND instance_name = ?"
            instance_params = [instance_name]

        df = _query_df(
            (
                "WITH latest AS (\n"
                "  SELECT\n"
                "    instance_name,\n"
                "    lower(trim(users_login)) AS login_norm,\n"
                "    users_enabled,\n"
                "    users_userprofile,\n"
                "    run_ts,\n"
                "    ROW_NUMBER() OVER (\n"
                "      PARTITION BY instance_name, lower(trim(users_login))\n"
                "      ORDER BY run_ts DESC\n"
                "    ) AS rn\n"
                "  FROM base_users_instance_metadata_history\n"
                "  WHERE users_login IS NOT NULL AND length(trim(users_login)) > 0\n"
                f"    {instance_sql}\n"
                ")\n"
                "SELECT\n"
                "  COUNT(DISTINCT login_norm) FILTER (WHERE users_enabled IS TRUE) AS enabled_users,\n"
                "  COUNT(DISTINCT login_norm) FILTER (WHERE users_enabled IS TRUE" + exclude_sql + ") AS enabled_users_no_consumer\n"
                "FROM latest\n"
                "WHERE rn = 1;"
            ),
            [*instance_params, *exclude_params],
        )

        row = _df_records(df)[0] if len(df) else {}

        return _ok(
            {
                "instanceName": instance_name,
                "licenseFilter": license_filter,
                "meta": {
                    "excludedProfiles": excluded_profiles,
                    "excludedProfilesSource": "standard.user_profile_exclude_consumer",
                },
                "kpis": row,
            }
        )

    except Exception as e:
        logger.exception("users kpis failed")
        return _err(str(e), status=500)


@app.route("/api/build/users/active-monthly")
def build_users_active_monthly():
    """Monthly active users (calendar months) with license filter.

    Definition of "active": any UI activity (viewing or developing actions)
    recorded in `fact_user_activity_daily`.

    License filter is applied using the per-instance snapshot table
    `base_users_instance_metadata_history`:
    - all_enabled: enabled users
    - no_consumer: enabled users excluding profiles from
      `standard.user_profile_exclude_consumer` (default: READER, AI_CONSUMER)

    Query parameters:
    - window: this_month|last_3_months|last_12_months (default: last_3_months)
    - months: integer (optional override; 1..24)
    - licenseFilter: all_enabled|no_consumer
    - instance_name: optional filter
    """

    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        standard = _read_standard_project_variables()
        excluded_profiles = _read_user_profile_exclude_consumer(standard)

        months = _parse_window_months(request.args.get("window"))
        if months is None:
            months = int(request.args.get("months") or 3)
        months = max(1, min(24, months))

        license_filter = _parse_license_filter(request.args.get("licenseFilter"))
        instance_name = _parse_instance_name(request.args.get("instance_name"))

        # Month range (calendar months, including current partial month).
        # We keep these as SQL expressions (not parameters) to avoid interval-typing issues.
        start_month_expr = f"(date_trunc('month', current_date) - INTERVAL {months - 1} MONTH)::DATE"
        end_month_expr = "date_trunc('month', current_date)::DATE"
        next_month_expr = "(date_trunc('month', current_date) + INTERVAL 1 MONTH)::DATE"

        # Build profile exclusion SQL.
        exclude_sql = ""
        exclude_params: list[Any] = []
        if license_filter == "no_consumer" and excluded_profiles:
            exclude_sql = (
                f" AND coalesce(upper(trim(u.users_userprofile)), '') NOT IN ({_sql_placeholders(len(excluded_profiles))})"
            )
            exclude_params = list(excluded_profiles)

        instance_sql = ""
        instance_params_aggregate: list[Any] = []
        instance_params_by_instance: list[Any] = []
        instances_filter_sql = ""
        if instance_name:
            instance_sql = " AND a.instance_name = ?"
            instance_params_aggregate = [instance_name]
            # Used twice in the by-instance query:
            # - once in the activity CTE (performance)
            # - once to restrict the generated instance list
            instance_params_by_instance = [instance_name, instance_name]
            instances_filter_sql = " AND instance_name = ?"

        # By-instance series.
        by_instance_df = _query_df(
            (
                "WITH months AS (\n"
                f"  SELECT * FROM generate_series({start_month_expr}, ({next_month_expr} - INTERVAL 1 DAY)::DATE, INTERVAL 1 MONTH) AS t(month_start)\n"
                "),\n"
                "activity AS (\n"
                "  SELECT\n"
                "    date_trunc('month', a.day) AS month_start,\n"
                "    a.instance_name,\n"
                "    a.login_norm\n"
                "  FROM fact_user_activity_daily a\n"
                "  JOIN base_users_instance_metadata_history u\n"
                "    ON u.instance_name = a.instance_name\n"
                "   AND lower(trim(u.users_login)) = a.login_norm\n"
                "  WHERE "
                f"    a.day >= {start_month_expr}\n"
                f"    AND a.day < {next_month_expr}\n"
                "    AND u.users_enabled IS TRUE\n"
                f"    {exclude_sql}\n"
                f"    {instance_sql}\n"
                "),\n"
                "agg AS (\n"
                "  SELECT\n"
                "    month_start,\n"
                "    instance_name,\n"
                "    COUNT(DISTINCT login_norm) AS active_users\n"
                "  FROM activity\n"
                "  GROUP BY 1, 2\n"
                ")\n"
                "SELECT\n"
                "  CAST(m.month_start AS VARCHAR) AS month,\n"
                "  i.instance_name,\n"
                "  COALESCE(a.active_users, 0) AS active_users\n"
                "FROM months m\n"
                "CROSS JOIN (\n"
                "  SELECT DISTINCT instance_name\n"
                "  FROM base_users_instance_metadata_history\n"
                "  WHERE instance_name IS NOT NULL\n"
                f"  {instances_filter_sql}\n"
                ") i\n"
                "LEFT JOIN agg a\n"
                "  ON a.month_start = m.month_start AND a.instance_name = i.instance_name\n"
                "ORDER BY m.month_start, i.instance_name;"
            ),
            [*exclude_params, *instance_params_by_instance],
        )

        # Aggregate series (distinct across instances).
        aggregate_df = _query_df(
            (
                "WITH months AS (\n"
                f"  SELECT * FROM generate_series({start_month_expr}, ({next_month_expr} - INTERVAL 1 DAY)::DATE, INTERVAL 1 MONTH) AS t(month_start)\n"
                "),\n"
                "activity AS (\n"
                "  SELECT\n"
                "    date_trunc('month', a.day) AS month_start,\n"
                "    a.login_norm\n"
                "  FROM fact_user_activity_daily a\n"
                "  JOIN base_users_instance_metadata_history u\n"
                "    ON u.instance_name = a.instance_name\n"
                "   AND lower(trim(u.users_login)) = a.login_norm\n"
                "  WHERE "
                f"    a.day >= {start_month_expr}\n"
                f"    AND a.day < {next_month_expr}\n"
                "    AND u.users_enabled IS TRUE\n"
                f"    {exclude_sql}\n"
                f"    {instance_sql}\n"
                "),\n"
                "agg AS (\n"
                "  SELECT\n"
                "    month_start,\n"
                "    COUNT(DISTINCT login_norm) AS active_users\n"
                "  FROM activity\n"
                "  GROUP BY 1\n"
                ")\n"
                "SELECT\n"
                "  CAST(m.month_start AS VARCHAR) AS month,\n"
                "  COALESCE(a.active_users, 0) AS active_users\n"
                "FROM months m\n"
                "LEFT JOIN agg a\n"
                "  ON a.month_start = m.month_start\n"
                "ORDER BY m.month_start;"
            ),
            [*exclude_params, *instance_params_aggregate],
        )

        return _ok(
            {
                "window": request.args.get("window") or None,
                "months": months,
                "licenseFilter": license_filter,
                "instanceName": instance_name,
                "meta": {
                    "excludedProfiles": excluded_profiles if license_filter == "no_consumer" else [],
                    "excludedProfilesSource": "standard.user_profile_exclude_consumer",
                },
                "byInstance": _df_records(by_instance_df),
                "aggregate": _df_records(aggregate_df),
            }
        )

    except Exception as e:
        logger.exception("users active monthly failed")
        return _err(str(e), status=500)


@app.route("/api/build/users/leaderboard")
def build_users_leaderboard():
    """Return leaderboards for viewing and developing activity."""

    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        # Prefer calendar-month windows if provided.
        months = _parse_window_months(request.args.get("window"))
        if months is None:
            months = int(request.args.get("months") or 0) or None

        if months is not None:
            months = max(1, min(24, months))
            where = [_window_months_where_sql(months=months)]
            params: list[Any] = []
        else:
            days = int(request.args.get("days") or 30)
            days = max(1, min(365, days))
            where = ["day >= current_date - ?::INTEGER"]
            params = [days]

        instance_name = _parse_instance_name(request.args.get("instance_name"))

        if instance_name:
            where.append("instance_name = ?")
            params.append(instance_name)

        where_sql = " WHERE " + " AND ".join(where)

        # We join to final_users_directory to get the best available display fields.
        base_sql = (
            "WITH agg AS (\n"
            "  SELECT\n"
            "    login_norm,\n"
            "    SUM(viewing_actions_count) AS viewing,\n"
            "    SUM(developing_actions_count) AS developing,\n"
            "    MAX(last_activity_at) AS last_activity_at,\n"
            "    COUNT(DISTINCT instance_name) AS instances\n"
            "  FROM fact_user_activity_daily\n"
            f"  {where_sql}\n"
            "  GROUP BY 1\n"
            ")\n"
        )

        viewing_df = _query_df(
            (
                base_sql
                + "SELECT\n"
                + "  a.login_norm AS login,\n"
                + "  u.display_name AS displayName,\n"
                + "  u.email AS email,\n"
                + "  u.user_profile AS userProfile,\n"
                + "  u.enabled AS enabled,\n"
                + "  a.viewing AS value,\n"
                + "  a.developing AS developing,\n"
                + "  a.instances AS instances,\n"
                + "  a.last_activity_at AS lastActivityAt\n"
                + "FROM agg a\n"
                + "LEFT JOIN final_users_directory u ON u.login_norm = a.login_norm\n"
                + "ORDER BY value DESC NULLS LAST\n"
                + "LIMIT 50;"
            ),
            params,
        )

        developing_df = _query_df(
            (
                base_sql
                + "SELECT\n"
                + "  a.login_norm AS login,\n"
                + "  u.display_name AS displayName,\n"
                + "  u.email AS email,\n"
                + "  u.user_profile AS userProfile,\n"
                + "  u.enabled AS enabled,\n"
                + "  a.developing AS value,\n"
                + "  a.viewing AS viewing,\n"
                + "  a.instances AS instances,\n"
                + "  a.last_activity_at AS lastActivityAt\n"
                + "FROM agg a\n"
                + "LEFT JOIN final_users_directory u ON u.login_norm = a.login_norm\n"
                + "ORDER BY value DESC NULLS LAST\n"
                + "LIMIT 50;"
            ),
            params,
        )

        payload: dict[str, Any] = {
            "instanceName": instance_name,
            "viewing": _df_records(viewing_df),
            "developing": _df_records(developing_df),
        }
        if months is not None:
            payload["months"] = months
        else:
            payload["days"] = int(request.args.get("days") or 30)
        return _ok(payload)

    except Exception as e:
        logger.exception("users leaderboard failed")
        return _err(str(e), status=500)


@app.route("/api/build/users/<login>")
def build_user_detail(login: str):
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        login_norm = _parse_login_norm(login)
        if not login_norm:
            return _err("Missing login")

        # Prefer calendar-month windows if provided.
        months = _parse_window_months(request.args.get("window"))
        if months is None:
            months = int(request.args.get("months") or 0) or None

        if months is not None:
            months = max(1, min(24, months))
            where = [_window_months_where_sql(months=months), "login_norm = ?"]
            params: list[Any] = [login_norm]
        else:
            days = int(request.args.get("days") or 30)
            days = max(1, min(365, days))
            where = ["day >= current_date - ?::INTEGER", "login_norm = ?"]
            params = [days, login_norm]

        instance_name = _parse_instance_name(request.args.get("instance_name"))

        if instance_name:
            where.append("instance_name = ?")
            params.append(instance_name)

        where_sql = " WHERE " + " AND ".join(where)

        summary_df = _query_df(
            (
                "SELECT\n"
                "  SUM(viewing_actions_count) AS viewing,\n"
                "  SUM(developing_actions_count) AS developing,\n"
                "  MAX(last_activity_at) AS last_activity_at,\n"
                "  COUNT(DISTINCT instance_name) AS instances,\n"
                "  COUNT(DISTINCT project_key) AS projects\n"
                "FROM fact_user_activity_project_daily\n"
                f"{where_sql};"
            ),
            params,
        )
        summary = _df_records(summary_df)[0] if len(summary_df) else None
        if summary is not None:
            if months is not None:
                summary["months"] = months
            else:
                summary["days"] = int(request.args.get("days") or 30)

        user_df = _query_df(
            """
            SELECT
              instance_name,
              login,
              login_norm,
              display_name,
              email,
              enabled,
              user_profile,
              group_names,
              run_ts
            FROM final_users_directory
            WHERE login_norm = ?
            LIMIT 1;
            """.strip(),
            [login_norm],
        )
        user = _df_records(user_df)[0] if len(user_df) else None

        # Daily activity trend (UI only)
        daily_df = _query_df(
            (
                "SELECT\n"
                "  CAST(day AS VARCHAR) AS label,\n"
                "  SUM(viewing_actions_count) AS viewing,\n"
                "  SUM(developing_actions_count) AS developing\n"
                "FROM fact_user_activity_daily\n"
                f"{where_sql}\n"
                "GROUP BY 1\n"
                "ORDER BY 1;"
            ),
            params,
        )

        return _ok({"user": user, "summary": summary, "activityDaily": _df_records(daily_df)})

    except Exception as e:
        logger.exception("user detail failed")
        return _err(str(e), status=500)


@app.route("/api/build/users/<login>/top-projects")
def build_user_top_projects(login: str):
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        login_norm = _parse_login_norm(login)
        if not login_norm:
            return _err("Missing login")

        # Prefer calendar-month windows if provided.
        months = _parse_window_months(request.args.get("window"))
        if months is None:
            months = int(request.args.get("months") or 0) or None

        if months is not None:
            months = max(1, min(24, months))
            where = [_window_months_where_sql(months=months), "login_norm = ?"]
            params: list[Any] = [login_norm]
        else:
            days = int(request.args.get("days") or 30)
            days = max(1, min(365, days))
            where = ["day >= current_date - ?::INTEGER", "login_norm = ?"]
            params = [days, login_norm]

        limit = int(request.args.get("limit") or 10)
        limit = max(1, min(100, limit))

        instance_name = _parse_instance_name(request.args.get("instance_name"))

        if instance_name:
            where.append("instance_name = ?")
            params.append(instance_name)

        where_sql = " WHERE " + " AND ".join(where)

        df = _query_df(
            (
                "SELECT\n"
                "  instance_name AS instanceName,\n"
                "  project_key AS projectKey,\n"
                "  SUM(viewing_actions_count) AS viewing,\n"
                "  SUM(developing_actions_count) AS developing,\n"
                "  MAX(last_activity_at) AS lastActivityAt\n"
                "FROM fact_user_activity_project_daily\n"
                f"{where_sql}\n"
                "GROUP BY 1, 2\n"
                "ORDER BY developing DESC NULLS LAST, viewing DESC NULLS LAST\n"
                "LIMIT ?;"
            ),
            [*params, limit],
        )

        payload: dict[str, Any] = {"rows": _df_records(df)}
        if months is not None:
            payload["months"] = months
        else:
            payload["days"] = int(request.args.get("days") or 30)
        return _ok(payload)

    except Exception as e:
        logger.exception("user top projects failed")
        return _err(str(e), status=500)
