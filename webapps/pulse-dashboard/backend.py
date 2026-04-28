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
from pathlib import Path
from typing import Any, cast

from flask import Flask, jsonify, request, send_file, send_from_directory

# Resolve repo paths for local dev static serving.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_BUILD_DIR = _REPO_ROOT / "resource" / "pulse-dashboard" / "build"

# In DSS, `app` is injected into the module globals by the webapp runner.
# For local dev runs, this will be missing and we create an app below.
app = cast(Flask | None, globals().get("app"))


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


# ----------------------------------------------------------------------------
# Static frontend (local dev convenience)
# ----------------------------------------------------------------------------
# In DSS, the HTML/JS are handled by `body.html` + `app.js`.
# For local runs (gunicorn/flask), serve the packaged React build so `GET /`
# shows the dashboard.
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path: str):  # pragma: no cover
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
        load_report = _ensure_ready_if_enabled()

        if load_report is None:
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
              CAST(date_trunc('day', day) AS DATE) AS day,
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

        return _ok(
            {
                "activityDaily": _df_records(activity_daily_df),
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
              CAST(date_trunc('day', timestamp) AS DATE) AS day,
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

        return _ok(
            {
                "summary": summary,
                "activityDaily": _df_records(activity_daily_df),
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
              CAST(date_trunc('day', timestamp) AS DATE) AS day,
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

        return _ok(
            {
                "summary": summary,
                "activityDaily": _df_records(activity_daily_df),
                "capabilities": _df_records(capabilities_df),
                "categories": _df_records(categories_df),
                "tags": _df_records(tags_df),
            }
        )

    except Exception as e:
        logger.exception("user drilldown failed")
        return _err(str(e), status=500)
