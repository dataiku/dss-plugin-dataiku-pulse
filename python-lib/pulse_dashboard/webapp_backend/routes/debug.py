from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from pulse_dashboard.pulse_duckdb.engine import ReadOnlySQLError
from pulse_dashboard.webapp_backend.services.users import RequestValidationError
from pulse_dashboard.webapp_backend.startup import duckdb_init_in_progress
from pulse_dashboard.webapp_backend.support import (
    _READ_ONLY_QUERY_LIMIT,
    _df_records,
    _duckdb_busy_response,
    _ensure_ready_if_enabled,
    _require_debug_access,
    _require_duckdb_engine,
    _validate_read_only_debug_sql,
)

logger = logging.getLogger(__name__)
if not logger.handlers:
    gunicorn_error_logger = logging.getLogger("gunicorn.error")
    if gunicorn_error_logger.handlers:
        logger.handlers = gunicorn_error_logger.handlers
        logger.setLevel(gunicorn_error_logger.level)
        logger.propagate = False
def register_routes(bp: Blueprint) -> None:
    @bp.route("/api/debug/duckdb/query", methods=["GET", "POST"])
    def debug_duckdb_query():
        try:
            _require_debug_access()
            _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
            _ensure_ready_if_enabled()

            if duckdb_init_in_progress():
                return _duckdb_busy_response()

            payload = request.get_json(silent=True) or {}
            sql_input = request.args.get("sql") if request.method == "GET" else payload.get("sql")
            sql = _validate_read_only_debug_sql(sql_input)

            conn = _create_connection(read_only=True)
            try:
                df = conn.execute(sql).df()
            except ReadOnlySQLError as exc:
                raise RequestValidationError(str(exc)) from exc
            except Exception as exc:
                raise RequestValidationError(str(exc)) from exc
            finally:
                conn.close()

            truncated = int(len(df.index)) > _READ_ONLY_QUERY_LIMIT
            limited_df = df.head(_READ_ONLY_QUERY_LIMIT).copy()

            return jsonify(
                {
                    "ok": True,
                    "sql": sql,
                    "limit": _READ_ONLY_QUERY_LIMIT,
                    "columns": list(limited_df.columns),
                    "rows": _df_records(limited_df),
                    "rowCount": int(len(df.index)),
                    "returnedRowCount": int(len(limited_df.index)),
                    "truncated": truncated,
                }
            )
        except RequestValidationError as exc:
            return jsonify(
                {
                    "ok": False,
                    "error": {
                        "code": "DUCKDB_PREVIEW_QUERY_INVALID",
                        "message": str(exc),
                    },
                }
            ), 400
        except PermissionError as exc:
            return jsonify(
                {
                    "ok": False,
                    "error": {
                        "code": "ADMIN_ACCESS_REQUIRED",
                        "message": str(exc),
                    },
                }
            ), 403
        except Exception:
            logger.exception("DuckDB preview query failed")
            return jsonify(
                {
                    "ok": False,
                    "error": {
                        "code": "DUCKDB_PREVIEW_QUERY_FAILED",
                        "message": "The preview query could not be completed.",
                    },
                }
            ), 500
