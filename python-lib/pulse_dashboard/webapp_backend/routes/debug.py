from __future__ import annotations

import logging
from typing import Any

from flask import Blueprint, jsonify, request
from shared_duckdb.sql_utils import quote_identifier, validate_identifier

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


def _safe_ident(name: str) -> str:
    return validate_identifier(name)


def register_routes(bp: Blueprint) -> None:
    @bp.route("/api/debug/duckdb/tables")
    def debug_duckdb_tables():
        try:
            _require_debug_access()
            _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
            _ensure_ready_if_enabled()

            if duckdb_init_in_progress():
                return _duckdb_busy_response()

            conn = _create_connection(read_only=True)
            try:
                rows = conn.execute("PRAGMA show_tables;").fetchall()
                table_names = sorted([str(r[0]) for r in rows])
                table_stats: list[dict[str, Any]] = []

                for table_name in table_names:
                    safe_table_name = _safe_ident(table_name)
                    columns_df = conn.execute(f"PRAGMA table_info({quote_identifier(safe_table_name)});").df()  # nosec B608 (table_name is validated)
                    row_count_row = conn.execute(f"SELECT COUNT(*) AS n FROM {quote_identifier(safe_table_name)};").fetchone()  # nosec B608 (table_name is validated)
                    size_row = conn.execute(
                        """
                        SELECT estimated_size
                        FROM duckdb_tables()
                        WHERE schema_name = 'main' AND table_name = ?
                        LIMIT 1;
                        """,
                        [safe_table_name],
                    ).fetchone()

                    table_stats.append(
                        {
                            "table": table_name,
                            "columnCount": int(len(columns_df.index)),
                            "rowCount": int(row_count_row[0]) if row_count_row else 0,
                            "estimatedSizeBytes": int(size_row[0]) if size_row and size_row[0] is not None else None,
                        }
                    )
            finally:
                conn.close()

            return jsonify({"ok": True, "tables": table_names, "tableStats": table_stats})
        except RequestValidationError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except PermissionError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 403
        except Exception as exc:
            logger.exception("duckdb tables failed")
            return jsonify({"ok": False, "error": str(exc)}), 500

    @bp.route("/api/debug/duckdb/table/<table_name>")
    def debug_duckdb_table(table_name: str):
        try:
            _require_debug_access()
            _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
            _ensure_ready_if_enabled()
            table_name = _safe_ident(table_name)

            if duckdb_init_in_progress():
                return _duckdb_busy_response()

            conn = _create_connection(read_only=True)
            try:
                cols_df = conn.execute(f"PRAGMA table_info({quote_identifier(table_name)});").df()  # nosec B608 (table_name is validated)
                sample_df = conn.execute(f"SELECT * FROM {quote_identifier(table_name)} LIMIT 10;").df()  # nosec B608 (table_name is validated)
            finally:
                conn.close()

            return jsonify({"ok": True, "columns": _df_records(cols_df), "sample": _df_records(sample_df)})
        except RequestValidationError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except PermissionError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 403
        except Exception as exc:
            logger.exception("duckdb table info failed")
            return jsonify({"ok": False, "error": str(exc)}), 500

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
