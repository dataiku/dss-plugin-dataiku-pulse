from __future__ import annotations

from flask import Blueprint, jsonify

from pulse_dashboard.webapp_backend.startup import (
    _maybe_schedule_startup_duckdb_init,
    _refresh_startup_status_metadata,
    _startup_check_completed,
    _startup_init_status,
)


def register_routes(bp: Blueprint) -> None:
    @bp.route("/api/startup/init-status")
    def startup_init_status():
        if not bool(_startup_init_status.get("startupCheckPerformed")) and not _startup_check_completed:
            _maybe_schedule_startup_duckdb_init()
        _refresh_startup_status_metadata()
        return jsonify({"ok": True, "init": dict(_startup_init_status)})
