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
import threading
import time
from functools import wraps
from pathlib import Path
from typing import Any, cast

from flask import Blueprint, Flask, jsonify, request, send_file, send_from_directory

# Resolve repo paths for local dev static serving.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_BUILD_DIR = _REPO_ROOT / "resource" / "pulse-dashboard" / "build"
_BODY_HTML_PATH = _REPO_ROOT / "webapps" / "pulse-dashboard" / "body.html"

# In DSS, `app` is injected into the module globals by the webapp runner.
# For local dev runs, this will be missing and we create an app below.
bp = Blueprint("pulse_dashboard", __name__)
_IS_LOCAL_DEV = False


logger = logging.getLogger(__name__)

_MAX_PAGE_LIMIT = 250
_MAX_LOOKBACK_DAYS = 365
_MAX_LOOKBACK_MONTHS = 24
_startup_init_lock = threading.Lock()
_startup_init_started = False
_startup_init_status: dict[str, Any] = {
    "state": "idle",
    "message": "Waiting to check DuckDB startup state",
    "startedAt": None,
    "finishedAt": None,
    "durationSec": None,
    "dbPath": None,
    "report": None,
    "error": None,
}


class RequestValidationError(ValueError):
    """Raised when request inputs are present but invalid."""


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


@bp.route("/__ping", endpoint="pulse_dashboard_ping")
def pulse_dashboard_ping():
    return "OK"


@bp.route("/api/status")
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


def _is_debug_enabled() -> bool:
    if _IS_LOCAL_DEV:
        return True
    standard = _read_standard_project_variables()
    return standard.get("debug") is True


def _require_debug_access() -> None:
    if not _is_debug_enabled():
        raise PermissionError("Debug endpoints are disabled")


def _handle_request_errors(route_name: str):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except RequestValidationError as exc:
                return _err(str(exc), status=400)
            except PermissionError as exc:
                return _err(str(exc), status=403)
            except Exception as exc:
                logger.exception("%s failed", route_name)
                return _err(str(exc), status=500)

        return wrapper

    return decorator


def _parse_int_arg(
    name: str,
    *,
    default: int | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
    required: bool = False,
) -> int | None:
    raw = request.args.get(name)
    if raw is None or str(raw).strip() == "":
        if required:
            raise RequestValidationError(f"Missing query parameter '{name}'")
        return default

    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise RequestValidationError(f"Invalid integer for '{name}'") from exc

    if minimum is not None and value < minimum:
        raise RequestValidationError(f"'{name}' must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise RequestValidationError(f"'{name}' must be <= {maximum}")
    return value


def _parse_pagination(*, default_limit: int = 25, max_limit: int = _MAX_PAGE_LIMIT) -> tuple[int, int]:
    limit = _parse_int_arg("limit", default=default_limit, minimum=1, maximum=max_limit)
    offset = _parse_int_arg("offset", default=0, minimum=0)
    return int(limit or default_limit), int(offset or 0)


def _parse_days_arg(*, default: int = 30, maximum: int = _MAX_LOOKBACK_DAYS) -> int:
    return int(_parse_int_arg("days", default=default, minimum=1, maximum=maximum) or default)


def _resolve_window_params(
    *,
    default_days: int = 30,
    max_days: int = _MAX_LOOKBACK_DAYS,
    max_months: int = _MAX_LOOKBACK_MONTHS,
) -> tuple[int | None, int | None]:
    window = request.args.get("window")
    months = _parse_window_months(window)
    if window and months is None:
        allowed = ", ".join(sorted(_WINDOW_TO_MONTHS))
        raise RequestValidationError(f"Invalid 'window'. Expected one of: {allowed}")

    if months is not None:
        return months, None

    explicit_months = _parse_int_arg("months", default=None, minimum=1, maximum=max_months)
    if explicit_months is not None:
        return explicit_months, None

    return None, _parse_days_arg(default=default_days, maximum=max_days)




@bp.route("/api/startup/flags")
def startup_flags():
    """Feature flags + small config exposed to the React SPA.

    Flags are read from DSS project standard variables so features can be enabled
    per project.

    Currently supported flags:
    - `userActivity`: always enabled.
    - `debug`: enabled when `standard.debug` is JSON boolean `true`.

    Config values:
    - `userProfileExcludeConsumer`: resolved exclusion list for the "no_consumer" toggle,
      read from `standard.user_profile_exclude_consumer`.

    Note:
    - If we cannot resolve the default project / variables, we default to disabled.
    """

    try:
        standard = _read_standard_project_variables()
        user_activity_enabled = True
        debug_enabled = standard.get("debug") is True
        excluded_profiles = _read_user_profile_exclude_consumer(standard)
        return _ok(
            {
                "flags": {
                    "userActivity": user_activity_enabled,
                    "debug": debug_enabled,
                },
                "config": {
                    "userProfileExcludeConsumer": excluded_profiles,
                },
            }
        )
    except Exception:
        logger.exception("Failed reading startup flags")
        return _ok(
            {
                "flags": {"userActivity": True, "debug": False},
                "config": {"userProfileExcludeConsumer": ["READER", "AI_CONSUMER"]},
            }
        )


@bp.route("/api/startup/duckdb")
def startup_duckdb():
    """Blocking startup initializer.

    This is meant to be called by `webapps/pulse-dashboard/body.html` before the
    React bundle is injected. It forces an eager GOLD load so the dashboard is
    usable immediately after initial render.
    """

    if ensure_database_ready is None:
        return _err("pulse_duckdb not available", status=500)

    started = time.time()
    _startup_init_status.update(
        {
            "state": "running",
            "message": "Initializing DuckDB and loading GOLD tables",
            "startedAt": started,
            "finishedAt": None,
            "durationSec": None,
            "error": None,
            "report": None,
        }
    )
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
            _startup_init_status.update(
                {
                    "state": "failed",
                    "message": "DuckDB initialization failed",
                    "finishedAt": time.time(),
                    "durationSec": duration_sec,
                    "error": json.dumps(report),
                    "report": report,
                }
            )
            return _err(
                "DuckDB initialization failed",
                status=500,
                hint=json.dumps({"durationSec": duration_sec, "load": report}),
            )
        _startup_init_status.update(
            {
                "state": "ready",
                "message": "DuckDB initialization complete",
                "finishedAt": time.time(),
                "durationSec": duration_sec,
                "error": None,
                "report": report,
            }
        )
        return _ok({"load": report, "durationSec": duration_sec})
    except Exception as e:
        _startup_init_status.update(
            {
                "state": "failed",
                "message": "DuckDB initialization failed",
                "finishedAt": time.time(),
                "durationSec": round(time.time() - started, 3),
                "error": str(e),
            }
        )
        logger.exception("DuckDB startup init failed")
        return _err(str(e), status=500)


@bp.route("/api/startup/status")
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


def _run_startup_duckdb_init() -> None:
    if ensure_database_ready is None:
        _startup_init_status.update(
            {
                "state": "unavailable",
                "message": "DuckDB engine unavailable",
                "finishedAt": time.time(),
                "error": "DuckDB engine unavailable",
            }
        )
        logger.warning("Pulse webapp startup init skipped: DuckDB engine unavailable")
        return

    try:
        started_at = time.time()
        _startup_init_status.update(
            {
                "state": "running",
                "message": "Initializing DuckDB and loading GOLD tables",
                "startedAt": started_at,
                "finishedAt": None,
                "durationSec": None,
                "error": None,
                "report": None,
            }
        )
        logger.info("Pulse webapp startup: initializing DuckDB in background")
        report = cast(
            dict[str, Any],
            ensure_database_ready(
                load_gold_tables=True,
                replace_gold_tables=getattr(pulse_settings, "PULSE_AUTO_LOAD_REPLACE", False)
                if pulse_settings is not None
                else False,
            ),
        )
        finished_at = time.time()
        duration_sec = round(finished_at - started_at, 3)
        if bool(report.get("ok", False)):
            _startup_init_status.update(
                {
                    "state": "ready",
                    "message": "DuckDB initialization complete",
                    "finishedAt": finished_at,
                    "durationSec": duration_sec,
                    "report": report,
                }
            )
            logger.info("Pulse webapp startup: DuckDB initialization finished in %ss", duration_sec)
        else:
            _startup_init_status.update(
                {
                    "state": "failed",
                    "message": "DuckDB initialization reported a failure",
                    "finishedAt": finished_at,
                    "durationSec": duration_sec,
                    "report": report,
                    "error": json.dumps(report),
                }
            )
            logger.warning(
                "Pulse webapp startup: DuckDB initialization reported failure after %ss: %s",
                duration_sec,
                report,
            )
    except Exception:
        finished_at = time.time()
        duration_sec = None
        if _startup_init_status.get("startedAt") is not None:
            try:
                duration_sec = round(finished_at - float(_startup_init_status["startedAt"]), 3)
            except Exception:
                duration_sec = None
        _startup_init_status.update(
            {
                "state": "failed",
                "message": "DuckDB initialization failed",
                "finishedAt": finished_at,
                "durationSec": duration_sec,
                "error": "DuckDB initialization failed. Check backend logs.",
            }
        )
        logger.exception("Pulse webapp startup: DuckDB initialization failed")


def _maybe_schedule_startup_duckdb_init() -> None:
    global _startup_init_started

    if pulse_settings is None or ensure_database_ready is None:
        _startup_init_status.update(
            {
                "state": "unavailable",
                "message": "DuckDB settings unavailable",
                "error": "DuckDB settings unavailable",
            }
        )
        return

    duckdb_path = Path(getattr(pulse_settings, "DUCKDB_PATH", "") or "")
    if not duckdb_path:
        _startup_init_status.update(
            {
                "state": "unavailable",
                "message": "DuckDB path is not configured",
                "error": "DuckDB path is not configured",
            }
        )
        return
    _startup_init_status["dbPath"] = str(duckdb_path)
    if duckdb_path.exists():
        _startup_init_status.update(
            {
                "state": "ready",
                "message": "DuckDB file already present",
                "finishedAt": time.time(),
                "durationSec": 0.0,
                "error": None,
            }
        )
        return

    with _startup_init_lock:
        if _startup_init_started:
            return
        _startup_init_started = True

    logger.info("Pulse webapp startup: DuckDB missing at %s; initializing now", duckdb_path)
    thread = threading.Thread(target=_run_startup_duckdb_init, name="pulse-duckdb-startup-init", daemon=True)
    thread.start()


@bp.route("/api/startup/init-status")
def startup_init_status():
    return _ok({"init": dict(_startup_init_status)})


# ----------------------------------------------------------------------------
# Static frontend (local dev convenience)
# ----------------------------------------------------------------------------
# In DSS, the HTML/JS are handled by `body.html` + `app.js`.
# For local runs (gunicorn/flask), we serve `body.html` too so local dev mirrors
# the DSS loader behavior (warmup + manifest-driven asset injection).


@bp.route("/resource/pulse-dashboard/build/<path:filename>")
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


@bp.route("/", defaults={"path": ""})
@bp.route("/<path:path>")
def serve_frontend(path: str):  # pragma: no cover
    # Mirror DSS by serving the same loader HTML.
    if _IS_LOCAL_DEV:
        return send_file(_BODY_HTML_PATH)

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

        for k, v in payload.items():
            if "description" in str(k).lower() and isinstance(v, str) and v.strip():
                return v.strip()

    return None


# Best-effort mapping from catalog object types to metadata history tables.
# These tables are expected in the GOLD outputs.
_OBJECT_EXTRAS_SOURCES: dict[str, dict[str, object]] = {
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


# Map product catalog types to the activity-event object_type.
_PRODUCT_TO_EVENT_OBJECT_TYPE = {
    "api_service": "api_endpoint",
    "insight": "dashboard",
    "agent_tool": "agent",
}


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


@bp.route("/api/duckdb/query")
@_handle_request_errors("duckdb query")
def duckdb_query():
    _require_debug_access()

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
        raise


@bp.route("/api/debug/duckdb/reload", methods=["POST"])
@_handle_request_errors("duckdb reload")
def debug_duckdb_reload():
    _require_debug_access()
    _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()

    started = time.time()
    _startup_init_status.update(
        {
            "state": "running",
            "message": "Reloading DuckDB and refreshing GOLD tables",
            "startedAt": started,
            "finishedAt": None,
            "durationSec": None,
            "error": None,
            "report": None,
        }
    )

    load_report = cast(
        dict[str, Any],
        _ensure_database_ready(load_gold_tables=True, replace_gold_tables=True),
    )

    duration_sec = round(time.time() - started, 3)
    _startup_init_status.update(
        {
            "state": "ready" if bool(load_report.get("ok", False)) else "failed",
            "message": "DuckDB reload complete" if bool(load_report.get("ok", False)) else "DuckDB reload failed",
            "finishedAt": time.time(),
            "durationSec": duration_sec,
            "error": None if bool(load_report.get("ok", False)) else json.dumps(load_report),
            "report": load_report,
        }
    )

    return _ok({"load": load_report})


@bp.route("/api/debug/duckdb/tables")
@_handle_request_errors("duckdb tables")
def debug_duckdb_tables():
    _require_debug_access()
    _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
    _ensure_ready_if_enabled()
    conn = _create_connection(read_only=True)
    try:
        rows = conn.execute("PRAGMA show_tables;").fetchall()
    finally:
        conn.close()

    tables = sorted([str(r[0]) for r in rows])
    return _ok({"tables": tables})


@bp.route("/api/debug/duckdb/table/<table_name>")
@_handle_request_errors("duckdb table info")
def debug_duckdb_table(table_name: str):
    _require_debug_access()
    _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
    _ensure_ready_if_enabled()
    table_name = _safe_ident(table_name)

    conn = _create_connection(read_only=True)
    try:
        cols_df = conn.execute(f'PRAGMA table_info("{table_name}");').df()  # nosec B608 (table_name is validated)
        sample_df = conn.execute(f'SELECT * FROM "{table_name}" LIMIT 10;').df()  # nosec B608 (table_name is validated)
    finally:
        conn.close()

    return _ok({"columns": _df_records(cols_df), "sample": _df_records(sample_df)})


@bp.route("/api/build/assets/facets")
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


@bp.route("/api/build/assets")
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
        limit, offset = _parse_pagination(default_limit=25)

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

        count_sql = f"SELECT COUNT(*) AS n FROM final_build_catalog{where_sql};"  # nosec B608 (where_sql is parameterized)
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
            f"FROM final_build_catalog{where_sql}\n"  # nosec B608 (where_sql is parameterized)
            f"ORDER BY {order_by}\n"  # nosec B608 (order_by from allowlist)
            "LIMIT ? OFFSET ?;"
        )
        rows = _query_df(sql, [*params, limit, offset])

        return _ok({"rows": _df_records(rows), "total": total})

    except Exception as e:
        logger.exception("assets query failed")
        return _err(str(e), status=500)


def _fetch_usage_and_related_assets(
    _query_df,
    *,
    project_key: str | None,
    object_type: str,
    object_key: str,
) -> tuple[int, list[dict[str, Any]]]:
    params: list[Any] = [object_type, object_key]
    where = "object_type = ? AND object_key = ?"
    if project_key is not None:
        where += " AND project_key = ?"
        params.append(project_key)

    usage_df = _query_df(
        f"SELECT COUNT(*) AS n FROM v_object_activity_events WHERE {where};",  # nosec B608 (where uses placeholders)
        params,
    )
    usage = int(usage_df.iloc[0]["n"]) if len(usage_df.index) else 0

    related_df = _query_df(
        f"""
        SELECT
          instance_name AS instanceName,
          project_key AS projectKey,
          COUNT(*) AS eventCount
        FROM v_object_activity_events
        WHERE {where}
        GROUP BY 1, 2
        ORDER BY eventCount DESC, instanceName, projectKey;
        """.strip(),  # nosec B608 (where uses placeholders)
        params,
    )

    return usage, _df_records(related_df)


def _fetch_description(
    _query_df,
    *,
    instance_name: str,
    project_key: str | None,
    object_type: str,
    object_key: str,
) -> str | None:
    spec = _OBJECT_EXTRAS_SOURCES.get(object_type)
    if not spec:
        return None

    table = str(spec["table"])
    key_col = str(spec["key_col"])
    project_scoped = bool(spec.get("project_scoped", False))

    where = ["instance_name = ?", f"{key_col} = ?"]
    params: list[Any] = [instance_name, object_key]

    if project_scoped:
        if not project_key:
            return None
        where.append("project_key = ?")
        params.append(project_key)

    df = _query_df(
        f"SELECT extras FROM {table} WHERE {' AND '.join(where)} LIMIT 1;",  # nosec B608 (table from allowlist)
        params,
    )
    if not len(df.index):
        return None

    extras = df.iloc[0].get("extras")
    return _extract_description_from_extras(extras if isinstance(extras, str) else None)


@bp.route("/api/build/assets/details")
def build_assets_details():
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        asset_id = (request.args.get("assetId") or "").strip()
        if not _is_md5(asset_id):
            return _err("Invalid or missing assetId", status=400)

        df = _query_df(
            """
            SELECT
              instance_name AS instanceName,
              project_key AS projectKey,
              object_type AS objectType,
              object_key AS objectKey,
              object_name AS objectName,
              owner_login AS ownerLogin,
              updated_at AS updatedAt,
              activity_30d AS activity30d
            FROM final_build_catalog
            WHERE asset_id = ?
            LIMIT 1;
            """.strip(),
            [asset_id],
        )

        if not len(df.index):
            return _err("Asset not found", status=404)

        row = _df_records(df)[0]
        instance_name = str(row.get("instanceName") or "")
        project_key = str(row.get("projectKey") or "")
        object_type = str(row.get("objectType") or "")
        object_key = str(row.get("objectKey") or "")

        usage, related_assets = _fetch_usage_and_related_assets(
            _query_df,
            project_key=project_key or None,
            object_type=object_type,
            object_key=object_key,
        )

        description = None
        try:
            description = _fetch_description(
                _query_df,
                instance_name=instance_name,
                project_key=project_key or None,
                object_type=object_type,
                object_key=object_key,
            )
        except Exception:
            description = None

        return _ok(
            {
                "asset": row,
                "capturedInfo": {"description": description},
                "usageSummary": {"eventsAllTime": usage},
                "relatedAssets": related_assets,
            }
        )

    except Exception as e:
        logger.exception("assets details failed")
        return _err(str(e), status=500)


@bp.route("/api/build/products/details")
def build_products_details():
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        asset_id = (request.args.get("assetId") or "").strip()
        if not _is_md5(asset_id):
            return _err("Invalid or missing assetId", status=400)

        df = _query_df(
            """
            SELECT
              instance_name AS instanceName,
              project_key AS projectKey,
              product_type AS objectType,
              product_key AS objectKey,
              product_name AS objectName,
              owner_login AS ownerLogin,
              updated_at AS updatedAt,
              activity_30d AS activity30d
            FROM final_build_products_catalog
            WHERE product_id = ?
            LIMIT 1;
            """.strip(),
            [asset_id],
        )

        if not len(df.index):
            return _err("Product not found", status=404)

        row = _df_records(df)[0]
        instance_name = str(row.get("instanceName") or "")
        project_key = str(row.get("projectKey") or "")
        product_type = str(row.get("objectType") or "")
        product_key = str(row.get("objectKey") or "")

        event_object_type = _PRODUCT_TO_EVENT_OBJECT_TYPE.get(product_type, product_type)

        usage, related_assets = _fetch_usage_and_related_assets(
            _query_df,
            project_key=project_key or None,
            object_type=event_object_type,
            object_key=product_key,
        )

        description = None
        try:
            description = _fetch_description(
                _query_df,
                instance_name=instance_name,
                project_key=project_key or None,
                object_type=product_type,
                object_key=product_key,
            )
        except Exception:
            description = None

        return _ok(
            {
                "asset": row,
                "capturedInfo": {"description": description},
                "usageSummary": {"eventsAllTime": usage},
                "relatedAssets": related_assets,
            }
        )

    except Exception as e:
        logger.exception("products details failed")
        return _err(str(e), status=500)


@bp.route("/api/build/products/facets")
def build_products_facets():
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        instances = (
            _query_df("SELECT DISTINCT instance_name FROM final_build_products_catalog ORDER BY 1;")["instance_name"]
            .dropna()
            .astype(str)
            .tolist()
        )
        projects = (
            _query_df("SELECT DISTINCT project_key FROM final_build_products_catalog ORDER BY 1;")["project_key"]
            .dropna()
            .astype(str)
            .tolist()
        )
        types = (
            _query_df("SELECT DISTINCT product_type FROM final_build_products_catalog ORDER BY 1;")["product_type"]
            .dropna()
            .astype(str)
            .tolist()
        )
        owners = (
            _query_df("SELECT DISTINCT owner_login FROM final_build_products_catalog ORDER BY 1;")["owner_login"]
            .dropna()
            .astype(str)
            .tolist()
        )

        return _ok({"instances": instances, "projects": projects, "types": types, "owners": owners})
    except Exception as e:
        logger.exception("products facets failed")
        return _err(str(e), status=500)


@bp.route("/api/build/products")
def build_products():
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        q = (request.args.get("q") or "").strip()
        owner = (request.args.get("owner") or "").strip()
        instances = _parse_csv_list(request.args.get("instances"))
        projects = _parse_csv_list(request.args.get("projects"))
        types = _parse_csv_list(request.args.get("types"))

        sort = (request.args.get("sort") or "updated_desc").strip()
        limit, offset = _parse_pagination(default_limit=25)

        # Guardrails
        limit = max(1, min(5000, limit))
        offset = max(0, offset)

        where = []
        params: list[Any] = []

        if q:
            where.append("(lower(product_name) LIKE ? OR lower(product_key) LIKE ?)")
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
            where.append(f"product_type IN ({','.join(['?'] * len(types))})")
            params.extend(types)

        where_sql = (" WHERE " + " AND ".join(where)) if where else ""

        order_by = {
            "updated_desc": "updated_at DESC NULLS LAST",
            "updated_asc": "updated_at ASC NULLS LAST",
            "activity_desc": "activity_30d DESC NULLS LAST, updated_at DESC NULLS LAST",
            "name_asc": "product_name ASC NULLS LAST",
        }.get(sort)
        if order_by is None:
            return _err(f"Invalid sort: {sort}")

        count_sql = f"SELECT COUNT(*) AS n FROM final_build_products_catalog{where_sql};"  # nosec B608 (where_sql is parameterized)
        total = int(_query_df(count_sql, params).iloc[0]["n"])

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
            f"FROM final_build_products_catalog{where_sql}\n"  # nosec B608 (where_sql is parameterized)
            f"ORDER BY {order_by}\n"  # nosec B608 (order_by from allowlist)
            "LIMIT ? OFFSET ?;"
        )
        rows = _query_df(sql, [*params, limit, offset])

        return _ok({"rows": _df_records(rows), "total": total})
    except Exception as e:
        logger.exception("products query failed")
        return _err(str(e), status=500)


@bp.route("/api/build/products/type-metrics")
def build_products_type_metrics():
    """Deeper metrics/charts for a single product type (30d window)."""

    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        product_type = (request.args.get("type") or "").strip()
        if not product_type:
            return _err("Missing type", status=400)

        days = _parse_days_arg(default=30)

        # Basic allowlist: product types are plugin-controlled (terminology.yaml)
        allowed_df = _query_df("SELECT DISTINCT product_type FROM final_build_products_catalog ORDER BY 1;")
        allowed = set(str(r.get("product_type") or "") for r in _df_records(allowed_df))
        if product_type not in allowed:
            return _err("Invalid type", status=400)

        # Inventory-level KPIs (fast, from catalog)
        kpis_df = _query_df(
            (
                "SELECT\n"
                "  COUNT(*) AS total_products,\n"
                "  COUNT(*) FILTER (WHERE activity_30d > 0) AS active_products_30d,\n"
                "  SUM(activity_30d) AS events_30d,\n"
                "  MAX(last_activity_at) AS last_activity_at\n"
                "FROM final_build_products_catalog\n"
                "WHERE product_type = ?;"
            ),
            [product_type],
        )
        kpis_row = _df_records(kpis_df)[0] if len(kpis_df.index) else {}

        # Owner concentration (inventory)
        owners_df = _query_df(
            (
                "SELECT\n"
                "  owner_login AS label,\n"
                "  COUNT(*) AS value\n"
                "FROM final_build_products_catalog\n"
                "WHERE product_type = ?\n"
                "GROUP BY 1\n"
                "ORDER BY value DESC\n"
                "LIMIT 12;"
            ),
            [product_type],
        )

        # Top products by activity_30d (inventory-level)
        top_products_df = _query_df(
            (
                "SELECT\n"
                "  product_id AS productId,\n"
                "  product_name AS label,\n"
                "  activity_30d AS value\n"
                "FROM final_build_products_catalog\n"
                "WHERE product_type = ?\n"
                "ORDER BY value DESC NULLS LAST, product_name\n"
                "LIMIT 12;"
            ),
            [product_type],
        )

        # Use event table for distinct users + daily time series.
        event_type = _PRODUCT_TO_EVENT_OBJECT_TYPE.get(product_type, product_type)
        totals_df = _query_df(
            (
                "SELECT\n"
                "  COUNT(*) AS events,\n"
                "  COUNT(DISTINCT login) AS active_users\n"
                "FROM v_object_activity_events\n"
                "WHERE timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY\n"
                "  AND object_type = ?;"
            ),
            [days, event_type],
        )
        totals = _df_records(totals_df)[0] if len(totals_df.index) else {}

        daily_df = _query_df(
            (
                "SELECT\n"
                "  CAST(CAST(date_trunc('day', timestamp) AS DATE) AS VARCHAR) AS label,\n"
                "  COUNT(*) AS value\n"
                "FROM v_object_activity_events\n"
                "WHERE timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY\n"
                "  AND object_type = ?\n"
                "GROUP BY 1\n"
                "ORDER BY 1;"
            ),
            [days, event_type],
        )

        by_project_df = _query_df(
            (
                "SELECT\n"
                "  project_key AS label,\n"
                "  COUNT(*) AS value\n"
                "FROM v_object_activity_events\n"
                "WHERE timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY\n"
                "  AND object_type = ?\n"
                "GROUP BY 1\n"
                "ORDER BY value DESC\n"
                "LIMIT 12;"
            ),
            [days, event_type],
        )

        by_instance_df = _query_df(
            (
                "SELECT\n"
                "  instance_name AS label,\n"
                "  COUNT(*) AS value\n"
                "FROM v_object_activity_events\n"
                "WHERE timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY\n"
                "  AND object_type = ?\n"
                "GROUP BY 1\n"
                "ORDER BY value DESC\n"
                "LIMIT 12;"
            ),
            [days, event_type],
        )

        return _ok(
            {
                "type": product_type,
                "windowDays": days,
                "kpis": {
                    "totalProducts": int(kpis_row.get("total_products") or 0),
                    "activeProducts30d": int(kpis_row.get("active_products_30d") or 0),
                    "events30d": int(kpis_row.get("events_30d") or 0),
                    "activeUsers30d": int(totals.get("active_users") or 0),
                    "lastActivityAt": kpis_row.get("last_activity_at"),
                },
                "charts": {
                    "activityDaily": _df_records(daily_df),
                    "topOwnersByProducts": _df_records(owners_df),
                    "topProductsByEvents": _df_records(top_products_df),
                    "topProjectsByEvents": _df_records(by_project_df),
                    "eventsByInstance": _df_records(by_instance_df),
                },
            }
        )

    except Exception as e:
        logger.exception("products type metrics failed")
        return _err(str(e), status=500)


_CONSUMPTION_PRODUCT_OBJECT_TYPES = (
    "api_endpoint",
    "agent",
    "dashboard",
    "web_application",
    "dataiku_application",
)


def _parse_string_list_param(value: str | None) -> list[str]:
    if not value:
        return []
    items = [v.strip() for v in str(value).split(",")]
    return [v for v in items if v]


def _build_in_clause(column: str, values: list[str], params: list[Any]) -> str:
    if not values:
        return ""
    placeholders = ", ".join(["?"] * len(values))
    params.extend(values)
    return f" AND {column} IN ({placeholders})"


def _build_like_clause(column: str, value: str | None, params: list[Any]) -> str:
    if not value or not str(value).strip():
        return ""
    params.append(f"%{str(value).strip()}%")
    return f" AND {column} ILIKE ?"


@bp.route("/api/consumption/products/facets")
def consumption_products_facets():
    """Facets for consumption filters."""

    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        quoted_types = ", ".join("'" + t.replace("'", "''") + "'" for t in _CONSUMPTION_PRODUCT_OBJECT_TYPES)

        instances = (
                _query_df(
                    f"SELECT DISTINCT instance_name FROM v_object_activity_events WHERE object_type IN ({quoted_types}) ORDER BY 1;"  # nosec B608 (quoted_types from allowlist)
                )["instance_name"]

            .dropna()
            .astype(str)
            .tolist()
        )

        projects = (
                _query_df(
                    f"SELECT DISTINCT project_key FROM v_object_activity_events WHERE object_type IN ({quoted_types}) ORDER BY 1;"  # nosec B608 (quoted_types from allowlist)
                )["project_key"]

            .dropna()
            .astype(str)
            .tolist()
        )

        types = (
                _query_df(
                    f"SELECT DISTINCT object_type FROM v_object_activity_events WHERE object_type IN ({quoted_types}) ORDER BY 1;"  # nosec B608 (quoted_types from allowlist)
                )["object_type"]

            .dropna()
            .astype(str)
            .tolist()
        )

        owners = (
            _query_df("SELECT DISTINCT owner_login FROM base_product_index ORDER BY 1;")["owner_login"]
            .dropna()
            .astype(str)
            .tolist()
        )

        return _ok({"instances": instances, "projects": projects, "types": types, "owners": owners})

    except Exception as e:
        logger.exception("consumption products facets failed")
        return _err(str(e), status=500)


@bp.route("/api/consumption/products/details")
def consumption_products_details():
    """Consumption drilldown for a single product (time window)."""

    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        product_id = (request.args.get("productId") or "").strip()
        if not _is_md5(product_id):
            return _err("Invalid or missing productId", status=400)

        days = _parse_days_arg(default=30)

        row_df = _query_df(
            """
            SELECT
              instance_name AS instanceName,
              project_key AS projectKey,
              product_type AS productType,
              product_key AS productKey,
              product_name AS productName,
              owner_login AS ownerLogin
            FROM final_build_products_catalog
            WHERE product_id = ?
            LIMIT 1;
            """.strip(),
            [product_id],
        )
        if not len(row_df.index):
            return _err("Product not found", status=404)

        row = _df_records(row_df)[0]
        instance_name = str(row.get("instanceName") or "")
        project_key = str(row.get("projectKey") or "")
        product_type = str(row.get("productType") or "")
        product_key = str(row.get("productKey") or "")

        params: list[Any] = [days, instance_name, project_key, product_type, product_key]
        where = (
            "timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY"
            " AND instance_name = ?"
            " AND project_key = ?"
            " AND object_type = ?"
            " AND object_key = ?"
        )

        totals_df = _query_df(
            (
                "SELECT\n"
                "  COUNT(*) AS events,\n"
                "  COUNT(DISTINCT login) AS active_users,\n"
                "  MAX(timestamp) AS last_activity_at\n"
                "FROM v_object_activity_events\n"
                f"WHERE {where};"  # nosec B608 (where uses placeholders)
            ),
            params,
        )
        totals = _df_records(totals_df)[0] if len(totals_df.index) else {}

        daily_df = _query_df(
            (
                "SELECT\n"
                "  CAST(CAST(date_trunc('day', timestamp) AS DATE) AS VARCHAR) AS label,\n"
                "  COUNT(*) AS value\n"
                "FROM v_object_activity_events\n"
                f"WHERE {where}\n"  # nosec B608 (where uses placeholders)
                "GROUP BY 1\n"
                "ORDER BY 1;"
            ),
            params,
        )

        top_users_df = _query_df(
            (
                "SELECT\n"
                "  login AS label,\n"
                "  COUNT(*) AS value\n"
                "FROM v_object_activity_events\n"
                f"WHERE {where}\n"  # nosec B608 (where uses placeholders)
                "GROUP BY 1\n"
                "ORDER BY value DESC\n"
                "LIMIT 25;"
            ),
            params,
        )

        activity_daily_rows = []
        for r in _df_records(daily_df):
            label = r.get("label") or r.get("day")
            if label is None:
                continue
            activity_daily_rows.append({"label": label, "value": r.get("value")})

        return _ok(
            {
                "windowDays": days,
                "product": row,
                "totals": {
                    "events": int(totals.get("events") or 0),
                    "activeUsers": int(totals.get("active_users") or 0),
                    "lastActivityAt": totals.get("last_activity_at"),
                },
                "activityDaily": activity_daily_rows,
                "topUsers": _df_records(top_users_df),
            }
        )

    except Exception as e:
        logger.exception("consumption products details failed")
        return _err(str(e), status=500)


@bp.route("/api/consumption/products/summary")
def consumption_products_summary():
    """Consumption overview for product-level objects.

    Supports optional filters:
    - days: int (1..365)
    - q: substring search over product key/name
    - instances: CSV list
    - projects: CSV list
    - types: CSV list (subset of product object types)
    - owner: substring match over owner_login
    """

    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        days = _parse_days_arg(default=30)

        q = (request.args.get("q") or "").strip()
        instances = _parse_string_list_param(request.args.get("instances"))
        projects = _parse_string_list_param(request.args.get("projects"))
        types = _parse_string_list_param(request.args.get("types"))
        owner = (request.args.get("owner") or "").strip()

        allowed_types = set(_CONSUMPTION_PRODUCT_OBJECT_TYPES)
        types = [t for t in types if t in allowed_types]

        params: list[Any] = [days]

        where = " WHERE timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY"

        # Limit to product object types by default.
        if types:
            where += _build_in_clause("object_type", types, params)
        else:
            quoted_types = ", ".join("'" + t.replace("'", "''") + "'" for t in _CONSUMPTION_PRODUCT_OBJECT_TYPES)
            where += f" AND object_type IN ({quoted_types})"

        where += " AND object_key IS NOT NULL"
        where += _build_in_clause("instance_name", instances, params)
        where += _build_in_clause("project_key", projects, params)

        # Use base_product_index to filter by owner and query string.
        # This avoids scanning raw event details_json.
        idx_filters = " WHERE 1=1"
        idx_params: list[Any] = []
        idx_filters += _build_like_clause("owner_login", owner, idx_params)
        if q:
            idx_filters += _build_like_clause("product_name", q, idx_params)
            # Also match product_key.
            idx_filters += _build_like_clause("product_key", q, idx_params)

        idx_df = None
        idx_pairs: set[tuple[str, str, str, str]] | None = None
        if idx_filters != " WHERE 1=1":
            idx_df = _query_df(
                (
                    "SELECT instance_name, project_key, product_type, product_key "
                    "FROM base_product_index" + idx_filters + ";"  # nosec B608 (idx_filters is built from parameterized clauses)
                ),
                idx_params,
            )
            idx_pairs = set(
                (
                    str(r.get("instance_name") or ""),
                    str(r.get("project_key") or ""),
                    str(r.get("product_type") or ""),
                    str(r.get("product_key") or ""),
                )
                for r in _df_records(idx_df)
            )

        def _apply_idx_pairs_sql(alias: str) -> tuple[str, list[Any]]:
            if not idx_pairs:
                return "", []
            pairs = list(idx_pairs)
            sub_params: list[Any] = []
            clauses = []
            for inst, proj, typ, key in pairs:
                clauses.append(f"({alias}.instance_name = ? AND {alias}.project_key = ? AND {alias}.object_type = ? AND {alias}.object_key = ?)")
                sub_params.extend([inst, proj, typ, key])
            return " AND (" + " OR ".join(clauses) + ")", sub_params

        idx_where_sql, idx_where_params = _apply_idx_pairs_sql("e")

        totals_df = _query_df(
            (
                "SELECT\n"  # nosec B608 (where/idx_where_sql use placeholders)
                "  COUNT(*) AS events,\n"
                "  COUNT(DISTINCT login) AS active_users,\n"
                "  COUNT(DISTINCT object_key) AS active_products\n"
                "FROM v_object_activity_events e\n"
                + where
                + idx_where_sql
                + ";"
            ),
            [*params, *idx_where_params],
        )
        totals = _df_records(totals_df)[0] if len(totals_df.index) else {}

        by_type_df = _query_df(
            (
                "SELECT\n"  # nosec B608 (where/idx_where_sql use placeholders)
                "  object_type AS label,\n"
                "  COUNT(*) AS events,\n"
                "  COUNT(DISTINCT login) AS active_users,\n"
                "  COUNT(DISTINCT object_key) AS active_products\n"
                "FROM v_object_activity_events e\n"
                + where
                + idx_where_sql
                + "\nGROUP BY 1\n"
                "ORDER BY events DESC;"
            ),
            [*params, *idx_where_params],
        )

        activity_daily_df = _query_df(
            (
                "SELECT\n"  # nosec B608 (where/idx_where_sql use placeholders)
                "  CAST(CAST(date_trunc('day', timestamp) AS DATE) AS VARCHAR) AS label,\n"
                "  COUNT(*) AS value\n"
                "FROM v_object_activity_events e\n"
                + where
                + idx_where_sql
                + "\nGROUP BY 1\n"
                "ORDER BY 1;"
            ),
            [*params, *idx_where_params],
        )

        top_products_df = _query_df(
            (
                "WITH act AS (\n"
                "  SELECT\n"
                "    instance_name,\n"
                "    project_key,\n"
                "    object_type AS product_type,\n"
                "    object_key AS product_key,\n"
                "    COUNT(*) AS events,\n"
                "    COUNT(DISTINCT login) AS active_users,\n"
                "    MAX(timestamp) AS last_activity_at\n"
                "  FROM v_object_activity_events e\n"  # nosec B608 (where/idx_where_sql use placeholders)
                + where
                + idx_where_sql
                + "\n  GROUP BY 1,2,3,4\n"
                "),\n"
                "idx AS (\n"
                "  SELECT instance_name, project_key, product_type, product_key, product_name, owner_login\n"
                "  FROM base_product_index\n"
                ")\n"
                "SELECT\n"  # nosec B608 (where/idx_where_sql use placeholders)
                "  md5(concat_ws('|', act.instance_name, act.project_key, act.product_type, act.product_key)) AS productId,\n"
                "  act.instance_name AS instanceName,\n"
                "  act.project_key AS projectKey,\n"
                "  act.product_type AS productType,\n"
                "  act.product_key AS productKey,\n"
                "  idx.product_name AS productName,\n"
                "  idx.owner_login AS ownerLogin,\n"
                "  act.events AS events,\n"
                "  act.active_users AS activeUsers,\n"
                "  act.last_activity_at AS lastActivityAt\n"
                "FROM act\n"
                "LEFT JOIN idx\n"
                "  ON idx.instance_name = act.instance_name\n"
                " AND idx.project_key = act.project_key\n"
                " AND idx.product_type = act.product_type\n"
                " AND idx.product_key = act.product_key\n"
                "ORDER BY events DESC NULLS LAST, lastActivityAt DESC NULLS LAST\n"
                "LIMIT 50;"
            ),
            [*params, *idx_where_params],
        )

        activity_daily_rows = []
        for row in _df_records(activity_daily_df):
            label = row.get("label") or row.get("day")
            if label is None:
                continue
            activity_daily_rows.append({"label": label, "value": row.get("value")})

        return _ok(
            {
                "windowDays": days,
                "totals": {
                    "events": int(totals.get("events") or 0),
                    "activeUsers": int(totals.get("active_users") or 0),
                    "activeProducts": int(totals.get("active_products") or 0),
                },
                "byType": _df_records(by_type_df),
                "activityDaily": activity_daily_rows,
                "topProducts": _df_records(top_products_df),
            }
        )

    except Exception as e:
        logger.exception("consumption products summary failed")
        return _err(str(e), status=500)


@bp.route("/api/build/development-activity")
def build_development_activity():
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        days = _parse_days_arg(default=30)

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


@bp.route("/api/build/development-activity/capability/<capability>")
def build_development_activity_capability(capability: str):
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        capability = capability.strip()
        if not capability:
            return _err("Missing capability")

        days = _parse_days_arg(default=30)

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


@bp.route("/api/build/development-activity/user/<login>")
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


@bp.route("/api/build/users/facets")
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


@bp.route("/api/build/users/kpis")
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
        exclude_placeholders = ""
        if license_filter == "no_consumer" and excluded_profiles:
            exclude_placeholders = _sql_placeholders(len(excluded_profiles))
            exclude_params = list(excluded_profiles)

        instance_sql = ""
        instance_params: list[Any] = []
        if instance_name:
            instance_sql = " AND instance_name = ?"
            instance_params = [instance_name]

        if exclude_placeholders:
            by_instance_exclude_condition = (
                "      AND coalesce(upper(trim(l.users_userprofile)), '') NOT IN ("
                + exclude_placeholders
                + ")\n"
            )
            by_instance_sql = (
                "WITH latest AS (\n"
                + "  SELECT\n"
                + "    instance_name,\n"
                + "    lower(trim(users_login)) AS login_norm,\n"
                + "    users_enabled,\n"
                + "    users_userprofile,\n"
                + "    run_ts,\n"
                + "    ROW_NUMBER() OVER (\n"
                + "      PARTITION BY instance_name, lower(trim(users_login))\n"
                + "      ORDER BY run_ts DESC\n"
                + "    ) AS rn\n"
                + "  FROM base_users_instance_metadata_history\n"
                + "  WHERE users_login IS NOT NULL AND length(trim(users_login)) > 0\n"
                + "),\n"
                + "activity AS (\n"
                + "  SELECT\n"
                + "    instance_name,\n"
                + "    lower(trim(login_norm)) AS login_norm,\n"
                + "    SUM(viewing_actions_count) AS total_viewing,\n"
                + "    SUM(developing_actions_count) AS total_developing\n"
                + "  FROM fact_user_activity_daily\n"
                + "  GROUP BY 1, 2\n"
                + ")\n"
                + "SELECT\n"
                + "  l.instance_name AS instanceName,\n"
                + "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled IS TRUE) AS enabled_users,\n"
                + "  COUNT(DISTINCT l.login_norm) FILTER (\n"
                + "    WHERE l.users_enabled IS TRUE\n"
                + by_instance_exclude_condition
                + "  ) AS enabled_users_no_consumer,\n"
                + "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled IS TRUE AND coalesce(a.total_viewing, 0) > 0) AS viewing_users,\n"
                + "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled IS TRUE AND coalesce(a.total_developing, 0) > 0) AS developing_users\n"
                + "FROM latest l\n"
                + "LEFT JOIN activity a ON a.instance_name = l.instance_name AND a.login_norm = l.login_norm\n"
                + "WHERE l.rn = 1\n"
                + "GROUP BY 1\n"
                + "ORDER BY enabled_users DESC, instanceName;"
            )
        else:
            df_sql = (
                "WITH latest AS (\n"
                "  SELECT\n"
                "    instance_name,\n"
                "    lower(trim(users_login)) AS login_norm,\n"
                "    trim(users_login) AS login,\n"
                "    coalesce(trim(users_displayname), trim(users_login)) AS display_name,\n"
                "    users_enabled,\n"
                "    users_userprofile,\n"
                "    run_ts,\n"
                "    ROW_NUMBER() OVER (\n"
                "      PARTITION BY instance_name, lower(trim(users_login))\n"
                "      ORDER BY run_ts DESC\n"
                "    ) AS rn\n"
                "  FROM base_users_instance_metadata_history\n"
                "  WHERE users_login IS NOT NULL AND length(trim(users_login)) > 0\n"
                f"    {instance_sql}\n"  # nosec B608 (instance_sql uses placeholders)
                ")\n"
                ", activity AS (\n"
                "  SELECT\n"
                "    lower(trim(login_norm)) AS login_norm,\n"
                "    SUM(viewing_actions_count) AS total_viewing,\n"
                "    SUM(developing_actions_count) AS total_developing\n"
                "  FROM fact_user_activity_daily\n"
                "  GROUP BY 1\n"
                ")\n"
                "SELECT\n"
                "  COUNT(DISTINCT login_norm) FILTER (WHERE users_enabled IS TRUE) AS enabled_users,\n"
                "  COUNT(DISTINCT login_norm) FILTER (WHERE users_enabled IS TRUE) AS enabled_users_no_consumer,\n"
                "  COUNT(DISTINCT login_norm) FILTER (WHERE users_enabled IS TRUE AND coalesce(total_viewing, 0) > 0) AS viewing_users,\n"
                "  COUNT(DISTINCT login_norm) FILTER (WHERE users_enabled IS TRUE AND coalesce(total_developing, 0) > 0) AS developing_users\n"
                "FROM latest l\n"
                "LEFT JOIN activity a ON a.login_norm = l.login_norm\n"
                "WHERE rn = 1;"
            )

        df = _query_df(df_sql, [*instance_params, *exclude_params])

        row = _df_records(df)[0] if len(df) else {}

        by_profile_df = _query_df(
            (
                "WITH latest AS (\n"
                "  SELECT\n"
                "    instance_name,\n"
                "    lower(trim(users_login)) AS login_norm,\n"
                "    coalesce(nullif(trim(users_userprofile), ''), 'UNKNOWN') AS user_profile,\n"
                "    users_enabled,\n"
                "    run_ts,\n"
                "    ROW_NUMBER() OVER (\n"
                "      PARTITION BY instance_name, lower(trim(users_login))\n"
                "      ORDER BY run_ts DESC\n"
                "    ) AS rn\n"
                "  FROM base_users_instance_metadata_history\n"
                "  WHERE users_login IS NOT NULL AND length(trim(users_login)) > 0\n"
                f"    {instance_sql}\n"  # nosec B608
                ")\n"
                "SELECT\n"
                "  user_profile AS profile,\n"
                "  COUNT(DISTINCT login_norm) FILTER (WHERE users_enabled IS TRUE) AS enabled_users\n"
                "FROM latest\n"
                "WHERE rn = 1\n"
                "GROUP BY 1\n"
                "ORDER BY enabled_users DESC, profile;"
            ),
            instance_params,
        )

        if exclude_placeholders:
            by_instance_exclude_condition = (
                "      AND coalesce(upper(trim(l.users_userprofile)), '') NOT IN ("
                + exclude_placeholders
                + ")\n"
            )
            by_instance_sql = (
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
                "),\n"
                "activity AS (\n"
                "  SELECT\n"
                "    instance_name,\n"
                "    lower(trim(login_norm)) AS login_norm,\n"
                "    SUM(viewing_actions_count) AS total_viewing,\n"
                "    SUM(developing_actions_count) AS total_developing\n"
                "  FROM fact_user_activity_daily\n"
                "  GROUP BY 1, 2\n"
                ")\n"
                "SELECT\n"
                "  l.instance_name AS instanceName,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled IS TRUE) AS enabled_users,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (\n"
                "    WHERE l.users_enabled IS TRUE\n"
                + by_instance_exclude_condition
                + "  ) AS enabled_users_no_consumer,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled IS TRUE AND coalesce(a.total_viewing, 0) > 0) AS viewing_users,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled IS TRUE AND coalesce(a.total_developing, 0) > 0) AS developing_users\n"
                "FROM latest l\n"
                "LEFT JOIN activity a ON a.instance_name = l.instance_name AND a.login_norm = l.login_norm\n"
                "WHERE l.rn = 1\n"
                "GROUP BY 1\n"
                "ORDER BY enabled_users DESC, instanceName;"
            )
        else:
            by_instance_sql = (
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
                "),\n"
                "activity AS (\n"
                "  SELECT\n"
                "    instance_name,\n"
                "    lower(trim(login_norm)) AS login_norm,\n"
                "    SUM(viewing_actions_count) AS total_viewing,\n"
                "    SUM(developing_actions_count) AS total_developing\n"
                "  FROM fact_user_activity_daily\n"
                "  GROUP BY 1, 2\n"
                ")\n"
                "SELECT\n"
                "  l.instance_name AS instanceName,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled IS TRUE) AS enabled_users,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled IS TRUE) AS enabled_users_no_consumer,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled IS TRUE AND coalesce(a.total_viewing, 0) > 0) AS viewing_users,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled IS TRUE AND coalesce(a.total_developing, 0) > 0) AS developing_users\n"
                "FROM latest l\n"
                "LEFT JOIN activity a ON a.instance_name = l.instance_name AND a.login_norm = l.login_norm\n"
                "WHERE l.rn = 1\n"
                "GROUP BY 1\n"
                "ORDER BY enabled_users DESC, instanceName;"
            )

        by_instance_df = _query_df(by_instance_sql, exclude_params)

        return _ok(
            {
                "instanceName": instance_name,
                "licenseFilter": license_filter,
                "meta": {
                    "excludedProfiles": excluded_profiles,
                    "excludedProfilesSource": "standard.user_profile_exclude_consumer",
                },
                "kpis": row,
                "byProfile": _df_records(by_profile_df),
                "byInstance": _df_records(by_instance_df),
            }
        )

    except Exception as e:
        logger.exception("users kpis failed")
        return _err(str(e), status=500)


@bp.route("/api/build/users/active-monthly")
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
                "WITH months AS (\n"  # nosec B608 (SQL fragments are static)
                f"  SELECT * FROM generate_series({start_month_expr}, ({next_month_expr} - INTERVAL 1 DAY)::DATE, INTERVAL 1 MONTH) AS t(month_start)\n"  # nosec B608 (month expr is internal)
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
                f"    a.day >= {start_month_expr}\n"  # nosec B608 (month expr is internal)
                f"    AND a.day < {next_month_expr}\n"  # nosec B608 (month expr is internal)
                "    AND u.users_enabled IS TRUE\n"
                f"    {exclude_sql}\n"  # nosec B608 (exclude_sql uses placeholders)
                f"    {instance_sql}\n"  # nosec B608 (instance_sql uses placeholders)
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
                f"  {instances_filter_sql}\n"  # nosec B608 (instances_filter_sql uses placeholders)
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
                "WITH months AS (\n"  # nosec B608 (SQL fragments are static)
                f"  SELECT * FROM generate_series({start_month_expr}, ({next_month_expr} - INTERVAL 1 DAY)::DATE, INTERVAL 1 MONTH) AS t(month_start)\n"  # nosec B608 (month expr is internal)
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
                f"    a.day >= {start_month_expr}\n"  # nosec B608 (month expr is internal)
                f"    AND a.day < {next_month_expr}\n"  # nosec B608 (month expr is internal)
                "    AND u.users_enabled IS TRUE\n"
                f"    {exclude_sql}\n"  # nosec B608 (exclude_sql uses placeholders)
                f"    {instance_sql}\n"  # nosec B608 (instance_sql uses placeholders)
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


@bp.route("/api/build/users/leaderboard")
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
            f"  {where_sql}\n"  # nosec B608 (where_sql uses placeholders)
            "  GROUP BY 1\n"
            ")\n"
        )

        viewing_df = _query_df(
            (
                base_sql
                + "SELECT\n"  # nosec B608 (SQL fragments are static)
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
                + "SELECT\n"  # nosec B608 (SQL fragments are static)
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
            payload["days"] = int(days or 30)
        return _ok(payload)

    except Exception as e:
        logger.exception("users leaderboard failed")
        return _err(str(e), status=500)


@bp.route("/api/build/users/<login>")
def build_user_detail(login: str):
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        login_norm = _parse_login_norm(login)
        if not login_norm:
            return _err("Missing login")

        months, days = _resolve_window_params()

        if months is not None:
            where = [_window_months_where_sql(months=months), "login_norm = ?"]
            params: list[Any] = [login_norm]
        else:
            where = ["day >= current_date - ?::INTEGER", "login_norm = ?"]
            params = [int(days or 30), login_norm]

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
                f"{where_sql};"  # nosec B608 (where_sql uses placeholders)
            ),
            params,
        )
        summary = _df_records(summary_df)[0] if len(summary_df) else None
        if summary is not None:
            if months is not None:
                summary["months"] = months
            else:
                summary["days"] = int(days or 30)

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
            ORDER BY enabled DESC, run_ts DESC, instance_name
            LIMIT 1;
            """.strip(),
            [login_norm],
        )
        user = _df_records(user_df)[0] if len(user_df) else None

        instances_df = _query_df(
            """
            SELECT
              instance_name AS instanceName,
              login,
              display_name AS displayName,
              email,
              enabled,
              user_profile AS userProfile,
              group_names AS groupNames,
              run_ts AS runTs
            FROM final_users_directory
            WHERE login_norm = ?
            ORDER BY enabled DESC, instance_name, run_ts DESC;
            """.strip(),
            [login_norm],
        )

        # Daily activity trend (UI only)
        daily_df = _query_df(
            (
                "SELECT\n"
                "  CAST(day AS VARCHAR) AS label,\n"
                "  SUM(viewing_actions_count) AS viewing,\n"
                "  SUM(developing_actions_count) AS developing\n"
                "FROM fact_user_activity_daily\n"
                f"{where_sql}\n"  # nosec B608 (where_sql uses placeholders)
                "GROUP BY 1\n"
                "ORDER BY 1;"
            ),
            params,
        )

        monthly_df = _query_df(
            (
                "SELECT\n"
                "  CAST(date_trunc('month', day) AS VARCHAR) AS month,\n"
                "  SUM(viewing_actions_count) AS viewing,\n"
                "  SUM(developing_actions_count) AS developing\n"
                "FROM fact_user_activity_daily\n"
                f"{where_sql}\n"  # nosec B608
                "GROUP BY 1\n"
                "ORDER BY 1;"
            ),
            params,
        )

        return _ok(
            {
                "user": user,
                "instances": _df_records(instances_df),
                "summary": summary,
                "activityDaily": _df_records(daily_df),
                "activityMonthly": _df_records(monthly_df),
            }
        )

    except Exception as e:
        logger.exception("user detail failed")
        return _err(str(e), status=500)


@bp.route("/api/build/users/<login>/top-projects")
def build_user_top_projects(login: str):
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        login_norm = _parse_login_norm(login)
        if not login_norm:
            return _err("Missing login")

        months, days = _resolve_window_params()

        if months is not None:
            where = [_window_months_where_sql(months=months), "login_norm = ?"]
            params: list[Any] = [login_norm]
        else:
            where = ["day >= current_date - ?::INTEGER", "login_norm = ?"]
            params = [int(days or 30), login_norm]

        limit = int(_parse_int_arg("limit", default=10, minimum=1, maximum=100) or 10)

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
                f"{where_sql}\n"  # nosec B608 (where_sql uses placeholders)
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
            payload["days"] = int(days or 30)
        return _ok(payload)

    except Exception as e:
        logger.exception("user top projects failed")
        return _err(str(e), status=500)



def register_routes(app: Flask, *, is_local_dev: bool = False) -> None:
    global _IS_LOCAL_DEV
    _IS_LOCAL_DEV = is_local_dev
    app.register_blueprint(bp)
    # In loader-backed mode, body.html owns the blocking startup init via
    # /api/startup/duckdb; avoid racing it with a background init thread.
    if not _IS_LOCAL_DEV:
        _maybe_schedule_startup_duckdb_init()
