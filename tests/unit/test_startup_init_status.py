from __future__ import annotations

import importlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from flask import Flask


STARTUP_STATUS_BASE = {
    "state": "idle",
    "normalizedState": "NOT_STARTED",
    "retryAllowed": True,
    "message": "Waiting to check DuckDB startup state",
    "phase": "idle",
    "startedAt": None,
    "finishedAt": None,
    "durationSec": None,
    "backendStartedAt": None,
    "dbPath": None,
    "metadataPath": None,
    "dbMtime": None,
    "freshnessSource": None,
    "freshnessTimestamp": None,
    "freshnessAgeSec": None,
    "freshnessToleranceSec": None,
    "rebuildOnStartupStale": None,
    "currentFileNumber": None,
    "totalFileCount": None,
    "startupCheckPerformed": False,
    "stale": False,
    "staleReason": None,
    "rebuildTriggeredBy": None,
    "report": None,
    "error": None,
}


def _iso_utc(age_sec: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=age_sec)).isoformat()


@pytest.fixture()
def startup_env(monkeypatch, tmp_path):
    from pulse_dashboard import settings as pulse_settings

    duckdb_path = tmp_path / "pulse.duckdb"
    metadata_path = duckdb_path.with_suffix(f"{duckdb_path.suffix}.meta.json")
    lock_path = tmp_path / ".duckdb_init.lock"
    monkeypatch.setattr(pulse_settings, "DUCKDB_PATH", duckdb_path, raising=False)
    monkeypatch.setattr(pulse_settings, "DUCKDB_METADATA_PATH", metadata_path, raising=False)
    monkeypatch.setattr(pulse_settings, "PULSE_DUCKDB_INIT_LOCK_PATH", str(lock_path), raising=False)
    monkeypatch.setattr(pulse_settings, "PULSE_DUCKDB_STARTUP_STALE_TOLERANCE_SEC", 86400.0, raising=False)
    monkeypatch.setattr(pulse_settings, "PULSE_DUCKDB_REBUILD_ON_STARTUP_STALE", True, raising=False)
    monkeypatch.setattr(pulse_settings, "PULSE_AUTO_LOAD_REPLACE", False, raising=False)
    monkeypatch.setattr(
        pulse_settings,
        "resolve_dashboard_duckdb_location",
        lambda: ("TEST_PROJECT", duckdb_path, metadata_path),
        raising=False,
    )

    startup_module = importlib.import_module("pulse_dashboard.webapp_backend.startup")
    startup_module = importlib.reload(startup_module)
    startup_module._startup_init_started = False
    startup_module._startup_check_completed = False
    startup_module._startup_init_status.clear()
    status = dict(STARTUP_STATUS_BASE)
    status["backendStartedAt"] = startup_module._backend_started_at
    startup_module._startup_init_status.update(status)

    reports: list[dict[str, object]] = []

    def ensure_database_ready(**kwargs):
        reports.append(kwargs)
        return {"ok": True, "loaded": ["base_users"], "failed": []}

    class SynchronousThread:
        def __init__(self, *, target, name: str, daemon: bool):
            self.target = target
            self.name = name
            self.daemon = daemon

        def start(self) -> None:
            self.target()

    monkeypatch.setattr(startup_module, "ensure_database_ready", ensure_database_ready)
    monkeypatch.setattr(startup_module.threading, "Thread", SynchronousThread)
    return startup_module, pulse_settings, duckdb_path, metadata_path, reports


def _write_metadata(metadata_path: Path, *, last_rebuild_at: str | None) -> None:
    payload = {"dbPath": "test"}
    if last_rebuild_at is not None:
        payload["lastRebuildAt"] = last_rebuild_at
    metadata_path.write_text(json.dumps(payload), encoding="utf-8")


def _touch(path: Path, *, age_sec: float = 0.0) -> None:
    path.write_text("duckdb", encoding="utf-8")
    timestamp = time.time() - age_sec
    os.utime(path, (timestamp, timestamp))


def test_register_routes_triggers_dss_startup_check(monkeypatch, tmp_path):
    from pulse_dashboard import settings as pulse_settings

    monkeypatch.setattr(pulse_settings, "DUCKDB_PATH", tmp_path / "missing.duckdb", raising=False)
    monkeypatch.setattr(pulse_settings, "DUCKDB_METADATA_PATH", tmp_path / "missing.duckdb.meta.json", raising=False)
    monkeypatch.setattr(pulse_settings, "PULSE_DUCKDB_INIT_LOCK_PATH", str(tmp_path / ".lock"), raising=False)

    full_backend = importlib.import_module("pulse_dashboard.webapp_backend.full_backend")
    full_backend = importlib.reload(full_backend)
    calls: list[str] = []
    monkeypatch.setattr(full_backend, "run_backend_startup_check", lambda: calls.append("startup"))

    app = Flask(__name__)
    full_backend.register_routes(app, is_local_dev=False)

    assert calls == ["startup"]


def test_register_routes_triggers_local_startup_check(monkeypatch, tmp_path):
    from pulse_dashboard import settings as pulse_settings

    monkeypatch.setattr(pulse_settings, "DUCKDB_PATH", tmp_path / "missing.duckdb", raising=False)
    monkeypatch.setattr(pulse_settings, "DUCKDB_METADATA_PATH", tmp_path / "missing.duckdb.meta.json", raising=False)
    monkeypatch.setattr(pulse_settings, "PULSE_DUCKDB_INIT_LOCK_PATH", str(tmp_path / ".lock"), raising=False)

    full_backend = importlib.import_module("pulse_dashboard.webapp_backend.full_backend")
    full_backend = importlib.reload(full_backend)
    calls: list[str] = []
    monkeypatch.setattr(full_backend, "run_backend_startup_check", lambda: calls.append("startup"))

    app = Flask(__name__)
    full_backend.register_routes(app, is_local_dev=True)

    assert calls == ["startup"]


def test_recent_last_rebuild_metadata_retains_database(startup_env):
    startup_module, _settings, duckdb_path, metadata_path, reports = startup_env
    _touch(duckdb_path, age_sec=7 * 86400)
    _write_metadata(metadata_path, last_rebuild_at=_iso_utc(3600))

    startup_module._maybe_schedule_startup_duckdb_init()

    status = startup_module._startup_init_status
    assert duckdb_path.exists()
    assert metadata_path.exists()
    assert reports == []
    assert status["state"] == "ready"
    assert status["startupCheckPerformed"] is True
    assert status["freshnessSource"] == "metadata_lastRebuildAt"
    assert status["stale"] is False
    assert status["staleReason"] is None
    assert status["rebuildTriggeredBy"] is None


def test_old_last_rebuild_metadata_deletes_database_and_schedules_rebuild(startup_env):
    startup_module, _settings, duckdb_path, metadata_path, reports = startup_env
    _touch(duckdb_path, age_sec=3600)
    _write_metadata(metadata_path, last_rebuild_at=_iso_utc(25 * 3600))

    startup_module._maybe_schedule_startup_duckdb_init()

    status = startup_module._startup_init_status
    assert not duckdb_path.exists()
    assert not metadata_path.exists()
    assert reports == [{"load_gold_tables": True, "replace_gold_tables": False}]
    assert status["freshnessSource"] == "metadata_lastRebuildAt"
    assert status["stale"] is True
    assert status["staleReason"] == "lastRebuildAt_older_than_tolerance"
    assert status["rebuildTriggeredBy"] == "startup_stale"
    assert status["state"] == "ready"


def test_startup_evaluates_resolved_project_path_not_import_time_default(startup_env, monkeypatch, tmp_path):
    startup_module, pulse_settings, default_path, default_metadata_path, reports = startup_env
    resolved_path = tmp_path / "resolved-project.duckdb"
    resolved_metadata_path = resolved_path.with_suffix(f"{resolved_path.suffix}.meta.json")
    monkeypatch.setattr(pulse_settings, "DUCKDB_PATH", default_path, raising=False)
    monkeypatch.setattr(pulse_settings, "DUCKDB_METADATA_PATH", default_metadata_path, raising=False)
    monkeypatch.setattr(
        pulse_settings,
        "resolve_dashboard_duckdb_location",
        lambda: ("RESOLVED_PROJECT", resolved_path, resolved_metadata_path),
        raising=False,
    )
    _touch(default_path, age_sec=3600)
    _write_metadata(default_metadata_path, last_rebuild_at=_iso_utc(3600))
    _touch(resolved_path, age_sec=3600)
    _write_metadata(resolved_metadata_path, last_rebuild_at=_iso_utc(25 * 3600))

    startup_module._maybe_schedule_startup_duckdb_init()

    status = startup_module._startup_init_status
    assert default_path.exists()
    assert default_metadata_path.exists()
    assert not resolved_path.exists()
    assert not resolved_metadata_path.exists()
    assert reports == [{"load_gold_tables": True, "replace_gold_tables": False}]
    assert pulse_settings.PULSE_SOURCE_PROJECT_KEY == "RESOLVED_PROJECT"
    assert pulse_settings.DUCKDB_PATH == resolved_path
    assert pulse_settings.DUCKDB_METADATA_PATH == resolved_metadata_path
    assert status["dbPath"] == str(resolved_path)
    assert status["metadataPath"] == str(resolved_metadata_path)
    assert status["staleReason"] == "lastRebuildAt_older_than_tolerance"
    assert status["rebuildTriggeredBy"] == "startup_stale"


@pytest.mark.parametrize("metadata", [{}, {"lastRebuildAt": "not-a-date"}])
def test_missing_or_invalid_metadata_falls_back_to_file_mtime(startup_env, metadata):
    startup_module, _settings, duckdb_path, metadata_path, reports = startup_env
    _touch(duckdb_path, age_sec=3600)
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    startup_module._maybe_schedule_startup_duckdb_init()

    status = startup_module._startup_init_status
    assert duckdb_path.exists()
    assert metadata_path.exists()
    assert reports == []
    assert status["freshnessSource"] == "mtime"
    assert status["stale"] is False
    assert status["staleReason"] == "metadata_lastRebuildAt_unavailable"
    assert status["freshnessTimestamp"] is not None


def test_invalid_metadata_with_old_mtime_deletes_database_and_reports_fallback(startup_env):
    startup_module, _settings, duckdb_path, metadata_path, reports = startup_env
    _touch(duckdb_path, age_sec=25 * 3600)
    metadata_path.write_text(json.dumps({"lastRebuildAt": "not-a-date"}), encoding="utf-8")

    startup_module._maybe_schedule_startup_duckdb_init()

    status = startup_module._startup_init_status
    assert not duckdb_path.exists()
    assert not metadata_path.exists()
    assert reports == [{"load_gold_tables": True, "replace_gold_tables": False}]
    assert status["freshnessSource"] == "mtime"
    assert status["staleReason"] == "metadata_lastRebuildAt_unavailable_mtime_older_than_tolerance"
    assert status["rebuildTriggeredBy"] == "startup_stale"


def test_rebuild_disabled_preserves_old_database(startup_env, monkeypatch):
    startup_module, pulse_settings, duckdb_path, metadata_path, reports = startup_env
    monkeypatch.setattr(pulse_settings, "PULSE_DUCKDB_REBUILD_ON_STARTUP_STALE", False, raising=False)
    _touch(duckdb_path, age_sec=3600)
    _write_metadata(metadata_path, last_rebuild_at=_iso_utc(25 * 3600))

    startup_module._maybe_schedule_startup_duckdb_init()

    status = startup_module._startup_init_status
    assert duckdb_path.exists()
    assert metadata_path.exists()
    assert reports == []
    assert status["state"] == "ready"
    assert status["stale"] is True
    assert status["staleReason"] == "lastRebuildAt_older_than_tolerance_rebuild_disabled"
    assert status["rebuildOnStartupStale"] is False
    assert status["rebuildTriggeredBy"] is None


def test_missing_database_schedules_initial_build(startup_env):
    startup_module, _settings, duckdb_path, metadata_path, reports = startup_env
    assert not duckdb_path.exists()
    assert not metadata_path.exists()

    startup_module._maybe_schedule_startup_duckdb_init()

    status = startup_module._startup_init_status
    assert reports == [{"load_gold_tables": True, "replace_gold_tables": False}]
    assert status["startupCheckPerformed"] is True
    assert status["stale"] is True
    assert status["staleReason"] == "missing"
    assert status["rebuildTriggeredBy"] == "missing"
    assert status["state"] == "ready"


def test_init_status_exposes_normalized_state_and_progress(startup_env):
    startup_module, _settings, _duckdb_path, _metadata_path, _reports = startup_env
    from pulse_dashboard.webapp_backend.routes import startup as startup_routes

    startup_routes = importlib.reload(startup_routes)
    startup_module._startup_init_status.update(
        {
            "state": "running",
            "startupCheckPerformed": True,
            "report": {"loaded": ["a", "b"], "failed": ["c"]},
        }
    )
    app = Flask(__name__)
    bp = startup_routes.Blueprint("pulse_dashboard", __name__)
    startup_routes.register_routes(bp)
    app.register_blueprint(bp)

    response = app.test_client().get("/api/startup/init-status")
    payload = response.get_json()["init"]

    assert response.status_code == 200
    assert payload["state"] == "running"
    assert payload["normalizedState"] == "INITIALIZING"
    assert payload["retryAllowed"] is False
    assert payload["currentFileNumber"] == 2
    assert payload["totalFileCount"] == 3


def test_init_status_marks_failure_retryable(startup_env):
    startup_module, _settings, _duckdb_path, _metadata_path, _reports = startup_env
    from pulse_dashboard.webapp_backend.routes import startup as startup_routes

    startup_routes = importlib.reload(startup_routes)
    startup_module._startup_init_status.update(
        {
            "state": "failed",
            "phase": "failed",
            "startupCheckPerformed": True,
            "error": "boom",
        }
    )
    app = Flask(__name__)
    bp = startup_routes.Blueprint("pulse_dashboard", __name__)
    startup_routes.register_routes(bp)
    app.register_blueprint(bp)

    response = app.test_client().get("/api/startup/init-status")
    payload = response.get_json()["init"]

    assert response.status_code == 200
    assert payload["normalizedState"] == "FAILED"
    assert payload["retryAllowed"] is True
