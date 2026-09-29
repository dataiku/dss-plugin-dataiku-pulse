from __future__ import annotations

import importlib

import pytest
from flask import Blueprint, Flask


@pytest.fixture()
def debug_reload_app(monkeypatch):
    startup_module = importlib.import_module("pulse_dashboard.webapp_backend.startup")
    startup_routes_module = importlib.import_module(
        "pulse_dashboard.webapp_backend.routes.startup"
    )
    debug_module = importlib.import_module(
        "pulse_dashboard.webapp_backend.routes.debug"
    )
    startup_module = importlib.reload(startup_module)
    startup_routes_module = importlib.reload(startup_routes_module)
    debug_module = importlib.reload(debug_module)

    startup_module._startup_init_status.clear()
    startup_module._startup_init_status.update(
        {
            "state": "idle",
            "normalizedState": "NOT_STARTED",
            "retryAllowed": True,
            "message": "Waiting to check DuckDB startup state",
            "phase": "idle",
            "startedAt": None,
            "finishedAt": None,
            "durationSec": None,
            "backendStartedAt": startup_module._backend_started_at,
            "dbPath": None,
            "metadataPath": None,
            "dbMtime": None,
            "currentFileNumber": None,
            "totalFileCount": None,
            "startupCheckPerformed": False,
            "stale": False,
            "staleReason": None,
            "rebuildTriggeredBy": None,
            "report": None,
            "error": None,
        }
    )
    startup_routes_module._advanced_llm_mesh_capability_cache = {
        "enabled": True,
        "licensedInstances": ["old"],
    }

    monkeypatch.setattr(debug_module, "_require_debug_access", lambda: None)

    calls: list[dict[str, bool]] = []
    load_report = {"ok": True, "loaded": ["fact_user_activity_daily"], "failed": []}

    def ensure_database_ready(**kwargs):
        calls.append(kwargs)
        return load_report

    monkeypatch.setattr(
        debug_module,
        "_require_duckdb_engine",
        lambda: (None, None, ensure_database_ready),
    )

    app = Flask(__name__)
    bp = Blueprint("pulse_dashboard", __name__)
    debug_module.register_routes(bp)
    startup_routes_module.register_routes(bp)
    app.register_blueprint(bp)

    return app, debug_module, startup_module, startup_routes_module, calls, load_report


def test_debug_reload_route_accepts_post_and_calls_full_reload(debug_reload_app):
    app, _debug_module, startup_module, startup_routes_module, calls, load_report = (
        debug_reload_app
    )

    response = app.test_client().post("/api/debug/duckdb/reload")
    payload = response.get_json()
    matching_rules = [
        rule
        for rule in app.url_map.iter_rules()
        if rule.rule == "/api/debug/duckdb/reload"
    ]

    assert response.status_code == 200
    assert response.content_type.startswith("application/json")
    assert any("POST" in rule.methods for rule in matching_rules)
    assert payload == {"ok": True, "load": load_report}
    assert calls == [{"load_gold_tables": True, "replace_gold_tables": True}]
    assert startup_module._startup_init_status["state"] == "ready"
    assert startup_module._startup_init_status["phase"] == "frontend_ready"
    assert startup_module._startup_init_status["report"] == load_report
    assert startup_module._startup_init_status["normalizedState"] == "READY"
    assert startup_routes_module._advanced_llm_mesh_capability_cache is None

    status_response = app.test_client().get("/api/startup/init-status")
    assert status_response.get_json()["init"]["report"] == load_report


def test_debug_reload_returns_nested_failed_report_error_as_json(debug_reload_app):
    app, _debug_module, startup_module, _startup_routes_module, calls, load_report = (
        debug_reload_app
    )
    load_report.clear()
    load_report.update(
        {
            "ok": False,
            "report": {
                "ok": False,
                "loaded": [],
                "failed": [
                    {
                        "table": "fact_user_activity_daily",
                        "path": "s3://bucket/gold/fact_user_activity_daily/year=2026/month=06/day=21/data.parquet",
                        "error": "DuckDB schema mismatch in glob",
                    }
                ],
            },
            "views": {"ok": True},
        }
    )

    response = app.test_client().post("/api/debug/duckdb/reload")
    payload = response.get_json()

    assert response.status_code == 500
    assert response.content_type.startswith("application/json")
    assert payload["ok"] is False
    assert payload["load"] == load_report
    assert "fact_user_activity_daily" in payload["error"]
    assert "DuckDB schema mismatch in glob" in payload["error"]
    assert calls == [{"load_gold_tables": True, "replace_gold_tables": True}]
    assert startup_module._startup_init_status["state"] == "failed"
    assert startup_module._startup_init_status["phase"] == "failed"
    assert startup_module._startup_init_status["report"] == load_report
    assert startup_module._startup_init_status["normalizedState"] == "FAILED"
    assert startup_module._startup_init_status["error"] is not None


def test_debug_reload_failed_report_without_details_returns_fallback_error(debug_reload_app):
    app, _debug_module, startup_module, _startup_routes_module, calls, load_report = (
        debug_reload_app
    )
    load_report.clear()
    load_report.update({"ok": False, "report": {"ok": False, "failed": []}})

    response = app.test_client().post("/api/debug/duckdb/reload")
    payload = response.get_json()

    assert response.status_code == 500
    assert payload == {
        "ok": False,
        "load": load_report,
        "error": "DuckDB reload failed; inspect the load report for details.",
    }
    assert calls == [{"load_gold_tables": True, "replace_gold_tables": True}]
    assert startup_module._startup_init_status["state"] == "failed"


def test_debug_reload_surfaces_required_gold_table_error(debug_reload_app):
    app, _debug_module, startup_module, _startup_routes_module, calls, load_report = (
        debug_reload_app
    )
    load_report.clear()
    load_report.update(
        {
            "ok": False,
            "report": {"ok": True, "loaded": [], "failed": []},
            "required_gold_tables": {
                "ok": False,
                "failed": [
                    {
                        "table": "fact_user_activity_daily",
                        "reason": "no_eligible_gold_files_discovered",
                        "error": "Required dashboard GOLD table fact_user_activity_daily has no eligible source files in the managed folder.",
                    }
                ],
            },
        }
    )

    response = app.test_client().post("/api/debug/duckdb/reload")
    payload = response.get_json()

    assert response.status_code == 500
    assert payload["ok"] is False
    assert payload["load"] == load_report
    assert "fact_user_activity_daily" in payload["error"]
    assert "no eligible source files" in payload["error"]
    assert calls == [{"load_gold_tables": True, "replace_gold_tables": True}]
    assert startup_module._startup_init_status["state"] == "failed"


def test_debug_reload_permission_failure_uses_json_403(debug_reload_app, monkeypatch):
    app, debug_module, _startup_module, _startup_routes_module, calls, _load_report = (
        debug_reload_app
    )

    def deny_access() -> None:
        raise PermissionError("Administration access is required.")

    monkeypatch.setattr(debug_module, "_require_debug_access", deny_access)

    response = app.test_client().post("/api/debug/duckdb/reload")

    assert response.status_code == 403
    assert response.get_json() == {
        "ok": False,
        "error": "Administration access is required.",
    }
    assert calls == []


def test_debug_reload_unexpected_failure_uses_json_500(debug_reload_app, monkeypatch):
    app, debug_module, startup_module, _startup_routes_module, calls, _load_report = (
        debug_reload_app
    )

    def fail_reload(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("boom")

    monkeypatch.setattr(
        debug_module, "_require_duckdb_engine", lambda: (None, None, fail_reload)
    )

    response = app.test_client().post("/api/debug/duckdb/reload")

    assert response.status_code == 500
    assert response.get_json() == {"ok": False, "error": "boom"}
    assert calls == [{"load_gold_tables": True, "replace_gold_tables": True}]
    assert startup_module._startup_init_status["state"] == "failed"
    assert startup_module._startup_init_status["normalizedState"] == "FAILED"
