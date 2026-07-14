from __future__ import annotations

import importlib

import pytest
from flask import Flask


@pytest.fixture()
def backend_module(monkeypatch, tmp_path):
    from pulse_dashboard import settings as pulse_settings

    duckdb_path = tmp_path / "pulse.duckdb"
    monkeypatch.setattr(pulse_settings, "DUCKDB_PATH", duckdb_path, raising=False)
    monkeypatch.setattr(
        pulse_settings,
        "DUCKDB_METADATA_PATH",
        duckdb_path.with_suffix(f"{duckdb_path.suffix}.meta.json"),
        raising=False,
    )
    monkeypatch.setattr(
        pulse_settings,
        "PULSE_DUCKDB_STARTUP_STALE_TOLERANCE_SEC",
        5.0,
        raising=False,
    )
    monkeypatch.setattr(
        pulse_settings,
        "PULSE_DUCKDB_REBUILD_ON_STARTUP_STALE",
        True,
        raising=False,
    )

    module = importlib.import_module("pulse_dashboard.webapp_backend.full_backend")
    module = importlib.reload(module)

    module._startup_init_started = False
    module._startup_check_completed = False
    module._startup_init_status.clear()
    module._startup_init_status.update(
        {
            "state": "idle",
            "normalizedState": "NOT_STARTED",
            "retryAllowed": True,
            "message": "Waiting to check DuckDB startup state",
            "phase": "idle",
            "startedAt": None,
            "finishedAt": None,
            "durationSec": None,
            "backendStartedAt": module._backend_started_at,
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
    app = Flask(__name__)
    app.register_blueprint(module.bp)
    return module, app


def test_init_status_triggers_start_once_and_returns_immediately(backend_module, monkeypatch):
    backend_module, app = backend_module
    calls: list[str] = []

    def fake_schedule() -> None:
        calls.append("scheduled")
        backend_module._startup_init_status["startupCheckPerformed"] = True

    monkeypatch.setattr(backend_module, "_maybe_schedule_startup_duckdb_init", fake_schedule)
    client = app.test_client()

    first = client.get("/api/startup/init-status")
    second = client.get("/api/startup/init-status")

    assert first.status_code == 200
    assert second.status_code == 200
    assert calls == ["scheduled"]


def test_init_status_exposes_normalized_state_and_progress(backend_module):
    backend_module, app = backend_module
    backend_module._startup_init_status.update(
        {
            "state": "running",
            "startupCheckPerformed": True,
            "report": {"loaded": ["a", "b"], "failed": ["c"]},
        }
    )

    client = app.test_client()
    response = client.get("/api/startup/init-status")
    payload = response.get_json()["init"]

    assert response.status_code == 200
    assert payload["state"] == "running"
    assert payload["normalizedState"] == "INITIALIZING"
    assert payload["retryAllowed"] is False
    assert payload["currentFileNumber"] == 2
    assert payload["totalFileCount"] == 3


def test_init_status_marks_failure_retryable(backend_module):
    backend_module, app = backend_module
    backend_module._startup_init_status.update(
        {
            "state": "failed",
            "phase": "failed",
            "startupCheckPerformed": True,
            "error": "boom",
        }
    )

    client = app.test_client()
    response = client.get("/api/startup/init-status")
    payload = response.get_json()["init"]

    assert response.status_code == 200
    assert payload["normalizedState"] == "FAILED"
    assert payload["retryAllowed"] is True


def test_ping_stays_lightweight_during_initialization(backend_module, monkeypatch):
    backend_module, app = backend_module
    called = {"schedule": 0}

    def fail_schedule() -> None:
        called["schedule"] += 1
        raise AssertionError("/__ping must not schedule startup init")

    monkeypatch.setattr(backend_module, "_maybe_schedule_startup_duckdb_init", fail_schedule)
    backend_module._startup_init_status["state"] = "running"

    client = app.test_client()
    response = client.get("/__ping")

    assert response.status_code == 200
    assert response.data == b"OK"
    assert called["schedule"] == 0
