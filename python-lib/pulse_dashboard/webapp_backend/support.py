from __future__ import annotations

import json
import logging
import re
from typing import Any, cast

import duckdb
from flask import jsonify

from pulse_dashboard import settings as pulse_settings

try:
    from pulse_dashboard.pulse_duckdb.engine import create_connection, ensure_database_ready, query_df
except Exception:
    create_connection = None
    ensure_database_ready = None
    query_df = None

logger = logging.getLogger(__name__)
if not logger.handlers:
    gunicorn_error_logger = logging.getLogger("gunicorn.error")
    if gunicorn_error_logger.handlers:
        logger.handlers = gunicorn_error_logger.handlers
        logger.setLevel(gunicorn_error_logger.level)
        logger.propagate = False


_READ_ONLY_QUERY_LIMIT = 200
_READ_ONLY_SQL_RE = re.compile(r"^\s*(select|with|show|describe|desc|explain)\b", re.IGNORECASE)
_ALLOWED_READ_ONLY_STATEMENT_TYPES = (
    duckdb.StatementType.SELECT,
    duckdb.StatementType.EXPLAIN,
)


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


def _df_records(df):
    return json.loads(df.to_json(orient="records", date_format="iso"))


def _duckdb_relation_exists(query_df, relation_name: str) -> bool:
    rows = query_df(
        """
        SELECT 1 AS present
        FROM information_schema.tables
        WHERE table_schema = 'main' AND table_name = ?
        UNION ALL
        SELECT 1 AS present
        FROM information_schema.views
        WHERE table_schema = 'main' AND table_name = ?
        LIMIT 1
        """.strip(),
        [relation_name, relation_name],
    )
    return rows is not None and not rows.empty


def _duckdb_busy_response(message: str = "DuckDB is initializing"):
    return _ok({"ok": False, "busy": True, "initializing": True, "error": message}, status=503)


def _normalize_debug_sql(sql: Any) -> str:
    from pulse_dashboard.webapp_backend.services.users import RequestValidationError

    if sql is None:
        raise RequestValidationError("Missing SQL query")
    normalized = str(sql).strip()
    if not normalized:
        raise RequestValidationError("Missing SQL query")
    return normalized


def _validate_read_only_debug_sql(sql: str) -> str:
    from pulse_dashboard.webapp_backend.services.users import RequestValidationError

    normalized = _normalize_debug_sql(sql)
    stripped = normalized.rstrip().rstrip(';').strip()
    if not stripped:
        raise RequestValidationError("Missing SQL query")
    if ';' in stripped:
        raise RequestValidationError("Only a single SQL statement is allowed")
    if not _READ_ONLY_SQL_RE.match(stripped):
        raise RequestValidationError("Only read-only SELECT/SHOW/DESCRIBE/EXPLAIN queries are allowed")
    try:
        statements = duckdb.extract_statements(stripped)
    except Exception as exc:
        raise RequestValidationError(f"Invalid SQL query: {exc}") from exc
    if len(statements) != 1:
        raise RequestValidationError("Only a single SQL statement is allowed")
    if statements[0].type not in _ALLOWED_READ_ONLY_STATEMENT_TYPES:
        raise RequestValidationError("Only read-only SQL is allowed")
    return stripped


def _current_user_auth_info() -> dict[str, Any] | None:
    from flask import request

    try:
        import dataiku

        request_headers = dict(request.headers)
        auth_info = dataiku.api_client().get_auth_info_from_browser_headers(request_headers, with_secrets=False)
        return auth_info if isinstance(auth_info, dict) else None
    except Exception:
        logger.exception("Unable to resolve current DSS user for administration check")
        return None


def _parse_group_names(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(group).strip() for group in value if str(group).strip()]
    return []


def _has_administration_access() -> bool:
    from pulse_dashboard.webapp_backend.full_backend import is_local_dev
    from pulse_dashboard.webapp_backend.services.users import _read_standard_project_variables

    if is_local_dev():
        return True

    standard = _read_standard_project_variables() or {}
    administration_owner = standard.get("administration_owner")
    if isinstance(administration_owner, dict):
        administration_group = str(administration_owner.get("value") or "").strip().lower()
    else:
        administration_group = str(administration_owner or "").strip().lower()
    if not administration_group:
        return False

    auth_info = _current_user_auth_info() or {}
    groups = _parse_group_names(auth_info.get("groups") or auth_info.get("userGroups") or auth_info.get("groupNames"))
    return any(str(group).strip().lower() == administration_group for group in groups)


def _require_debug_access() -> None:
    from pulse_dashboard.webapp_backend.full_backend import is_local_dev

    if is_local_dev():
        return
    if not _has_administration_access():
        raise PermissionError("Administration access is required.")
