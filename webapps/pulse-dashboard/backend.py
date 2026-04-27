from __future__ import annotations

# Dataiku Pulse Dashboard webapp backend.
#
# This backend serves API endpoints and (optionally) serves the React build as
# static assets. The frontend build is stored under the plugin `resource/`
# folder and `webapps/pulse-dashboard/body.html` points to the build's
# `index.html`.

import json
import logging
import sys
from pathlib import Path

from typing import cast

from flask import Flask, jsonify, request

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
    from pulse_dashboard.pulse_duckdb.engine.query import query_df  # type: ignore
except Exception:
    try:
        repo_root = Path(__file__).resolve().parents[2]
        python_lib = repo_root / "python-lib"
        if python_lib.is_dir():
            sys.path.insert(0, str(python_lib))
        from pulse_dashboard.pulse_duckdb.engine.query import query_df  # type: ignore
    except Exception:
        logger.exception("Failed to import Pulse dashboard libraries")
        query_df = None


if app is None:  # pragma: no cover
    app = Flask(__name__)

# From here on, `app` is always a Flask instance.
app = cast(Flask, app)


@app.route("/__ping", endpoint="pulse_dashboard_ping")
def pulse_dashboard_ping():
    return "OK"


@app.route("/api/status")
def status():
    return jsonify({"status": "Online", "msg": "Backend is running"})


def _df_records(df):
    return json.loads(df.to_json(orient="records", date_format="iso"))


@app.route("/api/duckdb/query")
def duckdb_query():
    if query_df is None:
        return jsonify({"ok": False, "error": "pulse_duckdb not available"}), 500

    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"ok": False, "error": "Missing query parameter 'q'"}), 400

    try:
        df = query_df(q)
        return jsonify({"ok": True, "rows": _df_records(df)})
    except Exception as e:
        # Common case when the app starts before any initialization step:
        # the DuckDB file may not exist yet.
        msg = str(e)
        if "database does not exist" in msg:
            return jsonify(
                {
                    "ok": False,
                    "error": msg,
                    "hint": "DuckDB not initialized yet. Load GOLD tables or enable auto-init.",
                }
            ), 503

        logger.exception("duckdb query failed")
        return jsonify({"ok": False, "error": msg}), 500


# -----------------------------------------------------------------------------
# DuckDB debug endpoints (used by the React debug pages)
# -----------------------------------------------------------------------------
try:
    from pulse_dashboard.pulse_duckdb.engine.rebuild import rebuild_gold_tables  # type: ignore
except Exception:
    rebuild_gold_tables = None


@app.route("/api/debug/duckdb/reload", methods=["POST"])
def debug_duckdb_reload():
    if rebuild_gold_tables is None:
        return jsonify({"ok": False, "error": "DuckDB rebuild not available"}), 500

    try:
        report = rebuild_gold_tables(replace=True)
        return jsonify({"ok": True, "load": report})
    except Exception as e:
        logger.exception("DuckDB reload failed")
        return jsonify({"ok": False, "error": str(e)}), 500


try:
    from pulse_dashboard.pulse_duckdb.engine.create_conn import create_connection  # type: ignore
except Exception:
    create_connection = None


@app.route("/api/debug/duckdb/tables")
def debug_duckdb_tables():
    if create_connection is None:
        return jsonify({"ok": False, "error": "DuckDB connection not available"}), 500

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


@app.route("/api/debug/duckdb/table/<table_name>")
def debug_duckdb_table_info(table_name: str):
    if create_connection is None:
        return jsonify({"ok": False, "error": "DuckDB connection not available"}), 500

    conn = create_connection(read_only=True)
    try:
        if not table_name.replace("_", "").isalnum():
            return jsonify({"ok": False, "error": "Invalid table name"}), 400

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

        ident = '"' + table_name.replace('"', "") + '"'

        sample_error = None
        sample_df = None
        try:
            sample_df = conn.execute(f"SELECT * FROM {ident} LIMIT 10;").df()
        except Exception as e:
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


@app.route("/api/debug/duckdb/query")
def debug_duckdb_query():
    if query_df is None:
        return jsonify({"ok": False, "error": "pulse_duckdb not available"}), 500

    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"ok": False, "error": "Missing query parameter 'q'"}), 400

    try:
        df = query_df(q)
        return jsonify({"ok": True, "rows": _df_records(df)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
