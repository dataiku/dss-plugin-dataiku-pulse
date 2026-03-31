from __future__ import annotations

# Dataiku Pulse Dashboard webapp backend.
#
# This backend serves API endpoints and (optionally) serves the React build as
# static assets. The frontend build is stored under the plugin `resource/`
# folder and `webapps/pulse-dashboard/body.html` points to the build's
# `index.html`.

import json
import os
import logging
from pathlib import Path

from flask import Flask, jsonify, request


logger = logging.getLogger(__name__)

# Resolve paths relative to the plugin root.
BASE_DIR = Path(__file__).resolve().parents[2]
BUILD_DIR = BASE_DIR / "resource" / "pulse-dashboard" / "build"

# Shared dashboard backend logic lives under python-lib to keep the webapp folder small.
try:
    from pulse_dashboard import settings  # type: ignore
    from pulse_dashboard.pulse_duckdb.engine.query import query_df  # type: ignore
except Exception:  # pragma: no cover
    settings = None
    query_df = None


app = Flask(__name__, static_folder=str(BUILD_DIR))


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
            return jsonify({
                "ok": False,
                "error": msg,
                "hint": "DuckDB not initialized yet. Load GOLD tables or enable auto-init.",
            }), 503

        logger.exception("duckdb query failed")
        return jsonify({"ok": False, "error": msg}), 500
