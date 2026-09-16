from __future__ import annotations

import hashlib
import importlib
import json
import threading
from pathlib import Path
from typing import Any

import duckdb
import pytest
from flask import Flask


class FakeReport:
    def __init__(self, data: Any):
        self.data = data


class FakeFuture:
    def __init__(
        self,
        report_data: Any = None,
        error: Exception | None = None,
        block: bool = False,
    ):
        self.report_data = report_data if report_data is not None else {"bundleChecksRunInfo": {"status": "SUCCESS"}}
        self.error = error
        self.waited = False
        self.wait_started = threading.Event()
        self.release = threading.Event()
        if not block:
            self.release.set()

    def wait_for_result(self):
        self.waited = True
        self.wait_started.set()
        self.release.wait(timeout=5)
        if self.error is not None:
            raise self.error
        return FakeReport(self.report_data)


class FakeProject:
    def __init__(self, future: FakeFuture):
        self.future = future

    def start_run_project_standards_checks(self):
        FakeDSSClient.start_count += 1
        return self.future


class FakeDSSClient:
    calls: list[dict[str, Any]] = []
    project_keys: list[str] = []
    start_count = 0
    future = FakeFuture()

    def __init__(self, **kwargs: Any):
        self.calls.append(dict(kwargs))

    def get_project(self, project_key: str):
        self.project_keys.append(project_key)
        return FakeProject(self.future)


@pytest.fixture()
def route_app(monkeypatch, tmp_path: Path):
    module = importlib.import_module("pulse_dashboard.webapp_backend.routes.project_standards")
    module = importlib.reload(module)

    conn = duckdb.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE base_asset_index (
            instance_name VARCHAR,
            project_key VARCHAR,
            object_type VARCHAR,
            object_key VARCHAR
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE final_build_products_catalog (
            product_id VARCHAR,
            instance_name VARCHAR,
            project_key VARCHAR,
            product_type VARCHAR,
            product_key VARCHAR
        );
        """
    )
    conn.executemany(
        "INSERT INTO base_asset_index VALUES (?, ?, ?, ?)",
        [
            ("worker-a", "PROJ_A", "dataset", "customers"),
            ("worker-a", "", "dataset", "no-project"),
        ],
    )
    conn.execute(
        "INSERT INTO final_build_products_catalog VALUES (?, ?, ?, ?, ?)",
        [asset_id("worker-a", "PROD_PROJECT", "api_service", "svc"), "worker-a", "PROD_PROJECT", "api_service", "svc"],
    )

    def query_df(sql, params=None):
        return conn.execute(sql, params or []).df()

    monkeypatch.setattr(module, "_require_duckdb_engine", lambda: (query_df, lambda: conn, lambda **kwargs: {}))
    monkeypatch.setattr(module, "_ensure_ready_if_enabled", lambda: None)
    monkeypatch.setattr(module, "_PROJECT_STANDARDS_CACHE_ROOT", tmp_path)
    monkeypatch.setattr(module.dataikuapi, "DSSClient", FakeDSSClient)
    FakeDSSClient.calls = []
    FakeDSSClient.project_keys = []
    FakeDSSClient.start_count = 0
    FakeDSSClient.future = FakeFuture()

    app = Flask(__name__)
    app.config["_PULSE_DASHBOARD_PULSE_PRIMARY"] = {
        "ignore_certs": True,
        "worker_hosts": [
            {
                "worker_name": "worker-a",
                "worker_enabled": True,
                "worker_url": "https://worker.example",
                "worker_api": "SECRET_API_KEY",
            }
        ]
    }
    module.register_routes(app)
    return module, app, tmp_path


def asset_id(instance_name: str, project_key: str, object_type: str, object_key: str) -> str:
    return hashlib.md5(
        f"{instance_name}|{project_key}|{object_type}|{object_key}".encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()


def post_run(app: Flask, payload: dict[str, Any] | None):
    return app.test_client().post("/api/project-standards/run", json=payload)


def wait_for_file(path: Path, *, timeout: float = 5.0) -> None:
    assert threading.Event().wait(0) is False
    deadline = threading.Event()
    import time

    end = time.time() + timeout
    while time.time() < end:
        if path.exists():
            return
        deadline.wait(0.02)
    raise AssertionError(f"timed out waiting for {path}")


def test_start_returns_202_before_blocked_future_resolves(route_app, monkeypatch):
    module, app, cache_root = route_app
    monkeypatch.setattr(module, "_has_administration_access", lambda: True)
    report_payload = {"bundleChecksRunInfo": {"status": "SUCCESS"}, "checks": [{"id": "check-a"}]}
    FakeDSSClient.future = FakeFuture(report_payload, block=True)

    response = post_run(app, {"assetId": asset_id("worker-a", "PROJ_A", "dataset", "customers")})
    payload = response.get_json()

    assert response.status_code == 202, payload
    assert payload["ok"] is True
    assert payload["state"] == "running"
    assert payload["deduped"] is False
    assert payload["instanceName"] == "worker-a"
    assert payload["projectKey"] == "PROJ_A"
    assert payload["runId"]
    assert FakeDSSClient.start_count == 1
    assert FakeDSSClient.future.wait_started.wait(timeout=2)
    assert not (cache_root / "worker-a-PROJ_A.json").exists()

    FakeDSSClient.future.release.set()
    wait_for_file(cache_root / "worker-a-PROJ_A.json")
    assert json.loads((cache_root / "worker-a-PROJ_A.json").read_text(encoding="utf-8")) == report_payload
    assert ("worker-a", "PROJ_A") not in module._ACTIVE_RUNS
    assert "SECRET_API_KEY" not in json.dumps(payload)
    assert "https://worker.example" not in json.dumps(payload)


def test_duplicate_same_process_run_returns_active_run_without_second_start(route_app, monkeypatch):
    module, app, cache_root = route_app
    monkeypatch.setattr(module, "_has_administration_access", lambda: True)
    FakeDSSClient.future = FakeFuture(block=True)

    first = post_run(app, {"assetId": asset_id("worker-a", "PROJ_A", "dataset", "customers")})
    first_payload = first.get_json()
    second = post_run(app, {"assetId": asset_id("worker-a", "PROJ_A", "dataset", "customers")})
    second_payload = second.get_json()

    assert first.status_code == 202, first_payload
    assert second.status_code == 202, second_payload
    assert second_payload["runId"] == first_payload["runId"]
    assert second_payload["deduped"] is True
    assert second_payload["state"] == "running"
    assert FakeDSSClient.start_count == 1
    assert FakeDSSClient.calls == [{"host": "https://worker.example", "api_key": "SECRET_API_KEY", "insecure_tls": True}]

    FakeDSSClient.future.release.set()
    wait_for_file(cache_root / "worker-a-PROJ_A.json")
    assert ("worker-a", "PROJ_A") not in module._ACTIVE_RUNS


def test_successful_background_run_removes_prior_error_artifact(route_app, monkeypatch):
    module, app, cache_root = route_app
    monkeypatch.setattr(module, "_has_administration_access", lambda: True)
    error_path = cache_root / "worker-a-PROJ_A.error.json"
    error_path.write_text(json.dumps({"state": "failed"}), encoding="utf-8")
    report_payload = {"bundleChecksRunInfo": {"status": "SUCCESS"}, "checks": [{"id": "check-a"}]}
    FakeDSSClient.future = FakeFuture(report_payload)

    response = post_run(app, {"assetId": asset_id("worker-a", "PROJ_A", "dataset", "customers")})
    payload = response.get_json()

    assert response.status_code == 202, payload
    wait_for_file(cache_root / "worker-a-PROJ_A.json")
    assert json.loads((cache_root / "worker-a-PROJ_A.json").read_text(encoding="utf-8")) == report_payload
    assert not error_path.exists()
    assert ("worker-a", "PROJ_A") not in module._ACTIVE_RUNS


def test_request_payload_cannot_override_resolved_identity(route_app, monkeypatch):
    _module, app, cache_root = route_app
    monkeypatch.setattr("pulse_dashboard.webapp_backend.routes.project_standards._has_administration_access", lambda: True)

    response = post_run(
        app,
        {
            "assetId": asset_id("worker-a", "PROJ_A", "dataset", "customers"),
            "instanceName": "attacker",
            "projectKey": "ATTACKER_PROJECT",
        },
    )
    payload = response.get_json()

    assert response.status_code == 202, payload
    assert payload["instanceName"] == "worker-a"
    assert payload["projectKey"] == "PROJ_A"
    assert FakeDSSClient.project_keys == ["PROJ_A"]
    wait_for_file(cache_root / "worker-a-PROJ_A.json")
    assert not (cache_root / "attacker-ATTACKER_PROJECT.json").exists()


@pytest.mark.parametrize("payload", [None, {}, {"assetId": "not-md5"}, {"assetId": ""}])
def test_malformed_or_missing_asset_id_fails_without_remote_call(route_app, monkeypatch, payload):
    _module, app, cache_root = route_app
    monkeypatch.setattr("pulse_dashboard.webapp_backend.routes.project_standards._has_administration_access", lambda: True)

    response = post_run(app, payload)

    assert response.status_code == 400
    assert FakeDSSClient.calls == []
    assert list(cache_root.iterdir()) == []


def test_missing_asset_fails_without_remote_call(route_app, monkeypatch):
    _module, app, cache_root = route_app
    monkeypatch.setattr("pulse_dashboard.webapp_backend.routes.project_standards._has_administration_access", lambda: True)

    response = post_run(app, {"assetId": "0" * 32})

    assert response.status_code == 404
    assert response.get_json()["error"] == "Asset not found"
    assert FakeDSSClient.calls == []
    assert list(cache_root.iterdir()) == []


def test_asset_without_project_key_fails_without_remote_call(route_app, monkeypatch):
    _module, app, cache_root = route_app
    monkeypatch.setattr("pulse_dashboard.webapp_backend.routes.project_standards._has_administration_access", lambda: True)

    response = post_run(app, {"assetId": asset_id("worker-a", "", "dataset", "no-project")})

    assert response.status_code == 400
    assert "project_key" in response.get_json()["error"]
    assert FakeDSSClient.calls == []
    assert list(cache_root.iterdir()) == []


@pytest.mark.parametrize(
    "workers, expected_error",
    [
        ([{"worker_enabled": True, "worker_url": "https://worker.example", "worker_api": "SECRET"}], "No enabled"),
        ([{"worker_name": "other", "worker_enabled": True, "worker_url": "https://worker.example", "worker_api": "SECRET"}], "No enabled"),
        ([{"worker_name": "worker-a", "worker_enabled": False, "worker_url": "https://worker.example", "worker_api": "SECRET"}], "No enabled"),
        (
            [
                {"worker_name": "worker-a", "worker_enabled": True, "worker_url": "https://one", "worker_api": "SECRET1"},
                {"worker_name": "worker-a", "worker_enabled": True, "worker_url": "https://two", "worker_api": "SECRET2"},
            ],
            "Multiple enabled",
        ),
        ([{"worker_name": "worker-a", "worker_enabled": True, "worker_url": "", "worker_api": "SECRET"}], "worker_url"),
        ([{"worker_name": "worker-a", "worker_enabled": True, "worker_url": "https://worker.example", "worker_api": ""}], "worker_api"),
    ],
)
def test_worker_configuration_failures_do_not_connect_or_cache(route_app, monkeypatch, workers, expected_error):
    module, app, cache_root = route_app
    monkeypatch.setattr(module, "_has_administration_access", lambda: True)
    app.config["_PULSE_DASHBOARD_PULSE_PRIMARY"] = {"worker_hosts": workers}

    response = post_run(app, {"assetId": asset_id("worker-a", "PROJ_A", "dataset", "customers")})
    payload = response.get_json()

    assert response.status_code == 409, payload
    assert expected_error in payload["error"]
    assert "SECRET" not in json.dumps(payload)
    assert FakeDSSClient.calls == []
    assert list(cache_root.iterdir()) == []
    assert module._ACTIVE_RUNS == {}


def test_unauthorized_request_does_not_lookup_connect_run_or_cache(route_app, monkeypatch):
    module, app, cache_root = route_app
    monkeypatch.setattr(module, "_has_administration_access", lambda: False)
    monkeypatch.setattr(module, "_require_duckdb_engine", lambda: (_ for _ in ()).throw(AssertionError("should not query")))

    response = post_run(app, {"assetId": asset_id("worker-a", "PROJ_A", "dataset", "customers")})

    assert response.status_code == 403
    assert FakeDSSClient.calls == []
    assert list(cache_root.iterdir()) == []


def test_background_scheduling_failure_writes_uncertain_error_and_clears_active(route_app, monkeypatch, caplog):
    module, app, cache_root = route_app
    monkeypatch.setattr(module, "_has_administration_access", lambda: True)
    target = cache_root / "worker-a-PROJ_A.json"
    target.write_text("existing", encoding="utf-8")
    FakeDSSClient.future = FakeFuture(block=True)

    class FailingThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("SECRET_API_KEY thread detail")

    monkeypatch.setattr(module.threading, "Thread", FailingThread)
    caplog.set_level("ERROR", logger="pulse_dashboard.webapp_backend.routes.project_standards")

    response = post_run(app, {"assetId": asset_id("worker-a", "PROJ_A", "dataset", "customers")})
    payload = response.get_json()

    assert response.status_code == 500, payload
    assert payload["error"] == "Project Standards background run could not be scheduled"
    assert "SECRET_API_KEY" not in json.dumps(payload)
    assert FakeDSSClient.start_count == 1
    assert FakeDSSClient.future.waited is False
    error_payload = json.loads((cache_root / "worker-a-PROJ_A.error.json").read_text(encoding="utf-8"))
    assert error_payload["runId"]
    assert error_payload["state"] == "background_scheduling_failed"
    assert error_payload["instanceName"] == "worker-a"
    assert error_payload["projectKey"] == "PROJ_A"
    assert error_payload["startedAt"]
    assert error_payload["finishedAt"]
    assert error_payload["exceptionType"] == "RuntimeError"
    assert "SECRET_API_KEY" not in json.dumps(error_payload)
    assert "SECRET_API_KEY" not in caplog.text
    assert target.read_text(encoding="utf-8") == "existing"
    assert ("worker-a", "PROJ_A") not in module._ACTIVE_RUNS


def test_background_scheduling_error_cache_failure_is_sanitized(route_app, monkeypatch, caplog):
    module, app, cache_root = route_app
    monkeypatch.setattr(module, "_has_administration_access", lambda: True)
    target = cache_root / "worker-a-PROJ_A.json"
    target.write_text("existing", encoding="utf-8")
    FakeDSSClient.future = FakeFuture(block=True)

    class FailingThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("SECRET_API_KEY thread detail")

    def fail_error_cache(*, instance_name, project_key, payload, cache_root=None):
        raise OSError("SECRET_API_KEY disk detail")

    monkeypatch.setattr(module.threading, "Thread", FailingThread)
    monkeypatch.setattr(module, "_write_error_cache", fail_error_cache)
    caplog.set_level("ERROR", logger="pulse_dashboard.webapp_backend.routes.project_standards")

    response = post_run(app, {"assetId": asset_id("worker-a", "PROJ_A", "dataset", "customers")})
    payload = response.get_json()

    assert response.status_code == 500, payload
    assert payload["error"] == "Project Standards background run could not be scheduled"
    assert "SECRET_API_KEY" not in json.dumps(payload)
    assert FakeDSSClient.start_count == 1
    assert FakeDSSClient.future.waited is False
    assert not (cache_root / "worker-a-PROJ_A.error.json").exists()
    assert "SECRET_API_KEY" not in caplog.text
    assert target.read_text(encoding="utf-8") == "existing"
    assert ("worker-a", "PROJ_A") not in module._ACTIVE_RUNS


def test_future_failure_writes_sanitized_error_preserves_success_cache_and_clears_active(route_app, monkeypatch, caplog):
    module, app, cache_root = route_app
    monkeypatch.setattr(module, "_has_administration_access", lambda: True)
    target = cache_root / "worker-a-PROJ_A.json"
    target.write_text(json.dumps({"previous": "success"}), encoding="utf-8")
    FakeDSSClient.future = FakeFuture(error=RuntimeError("SECRET_API_KEY upstream detail"))

    caplog.set_level("ERROR", logger="pulse_dashboard.webapp_backend.routes.project_standards")
    response = post_run(app, {"assetId": asset_id("worker-a", "PROJ_A", "dataset", "customers")})
    payload = response.get_json()

    assert response.status_code == 202, payload
    wait_for_file(cache_root / "worker-a-PROJ_A.error.json")
    error_payload = json.loads((cache_root / "worker-a-PROJ_A.error.json").read_text(encoding="utf-8"))
    assert error_payload["runId"] == payload["runId"]
    assert error_payload["state"] == "failed"
    assert error_payload["instanceName"] == "worker-a"
    assert error_payload["projectKey"] == "PROJ_A"
    assert error_payload["exceptionType"] == "RuntimeError"
    assert "SECRET_API_KEY" not in json.dumps(error_payload)
    assert "SECRET_API_KEY" not in caplog.text
    assert target.read_text(encoding="utf-8") == json.dumps({"previous": "success"})
    assert ("worker-a", "PROJ_A") not in module._ACTIVE_RUNS


def test_cache_write_failure_writes_safe_error_and_clears_active(route_app, monkeypatch, caplog):
    module, app, cache_root = route_app
    monkeypatch.setattr(module, "_has_administration_access", lambda: True)
    target = cache_root / "worker-a-PROJ_A.json"
    target.write_text("existing", encoding="utf-8")
    original_write_error_cache = module._write_error_cache

    def fail_report_cache(*, instance_name, project_key, payload, cache_root=None):
        raise OSError("SECRET_API_KEY disk detail")

    monkeypatch.setattr(module, "_write_report_cache", fail_report_cache)
    monkeypatch.setattr(module, "_write_error_cache", original_write_error_cache)
    caplog.set_level("ERROR", logger="pulse_dashboard.webapp_backend.routes.project_standards")

    response = post_run(app, {"assetId": asset_id("worker-a", "PROJ_A", "dataset", "customers")})
    payload = response.get_json()

    assert response.status_code == 202, payload
    wait_for_file(cache_root / "worker-a-PROJ_A.error.json")
    error_payload = json.loads((cache_root / "worker-a-PROJ_A.error.json").read_text(encoding="utf-8"))
    assert error_payload["runId"] == payload["runId"]
    assert error_payload["exceptionType"] == "OSError"
    assert "SECRET_API_KEY" not in json.dumps(error_payload)
    assert "SECRET_API_KEY" not in caplog.text
    assert target.read_text(encoding="utf-8") == "existing"
    assert ("worker-a", "PROJ_A") not in module._ACTIVE_RUNS


def test_cache_writer_uses_unique_sibling_temp_paths_for_same_target(route_app, monkeypatch):
    module, _app, cache_root = route_app
    original_replace = Path.replace
    replace_calls: list[tuple[Path, Path]] = []

    def spy_replace(self, target_path):
        replace_calls.append((self, Path(target_path)))
        return original_replace(self, target_path)

    monkeypatch.setattr(Path, "replace", spy_replace)

    first_target = module._write_report_cache(instance_name="worker-a", project_key="PROJ_A", payload={"run": 1})
    second_target = module._write_report_cache(instance_name="worker-a", project_key="PROJ_A", payload={"run": 2})

    assert first_target == cache_root / "worker-a-PROJ_A.json"
    assert second_target == cache_root / "worker-a-PROJ_A.json"
    assert json.loads(second_target.read_text(encoding="utf-8")) == {"run": 2}
    assert len(replace_calls) == 2
    assert replace_calls[0][0] != replace_calls[1][0]
    assert {call[0].parent for call in replace_calls} == {cache_root}
    assert {call[1] for call in replace_calls} == {cache_root / "worker-a-PROJ_A.json"}
    assert not list(cache_root.glob("*.tmp"))


def test_cache_writer_failed_replace_preserves_target_and_removes_only_own_temp(route_app, monkeypatch):
    module, _app, cache_root = route_app
    target = cache_root / "worker-a-PROJ_A.json"
    target.write_text("existing", encoding="utf-8")
    unrelated_temp = cache_root / ".worker-a-PROJ_A.json.unrelated.tmp"
    unrelated_temp.write_text("other request", encoding="utf-8")
    temp_paths: list[Path] = []

    def fail_replace(self, target_path):
        temp_paths.append(self)
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(OSError):
        module._write_report_cache(instance_name="worker-a", project_key="PROJ_A", payload={"run": 1})

    assert target.read_text(encoding="utf-8") == "existing"
    assert unrelated_temp.read_text(encoding="utf-8") == "other request"
    assert len(temp_paths) == 1
    assert temp_paths[0].parent == cache_root
    assert not temp_paths[0].exists()


def test_product_asset_id_resolves_server_side(route_app, monkeypatch):
    module, app, cache_root = route_app
    monkeypatch.setattr(module, "_has_administration_access", lambda: True)

    response = post_run(app, {"assetId": asset_id("worker-a", "PROD_PROJECT", "api_service", "svc")})
    payload = response.get_json()

    assert response.status_code == 202, payload
    assert payload["instanceName"] == "worker-a"
    assert payload["projectKey"] == "PROD_PROJECT"
    assert FakeDSSClient.project_keys == ["PROD_PROJECT"]
    wait_for_file(cache_root / "worker-a-PROD_PROJECT.json")
