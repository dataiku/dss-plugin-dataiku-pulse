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


@app.route("/__ping")
def ping():
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
            return jsonify({
                "ok": False,
                "error": msg,
                "hint": "DuckDB not initialized yet. Load GOLD tables or enable auto-init.",
            }), 503

        logger.exception("duckdb query failed")
        return jsonify({"ok": False, "error": msg}), 500
