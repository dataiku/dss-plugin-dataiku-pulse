from __future__ import annotations

import hashlib
import importlib

import duckdb
import pytest
from flask import Flask


@pytest.fixture()
def backend_module(monkeypatch):
    full_backend = importlib.import_module("pulse_dashboard.webapp_backend.full_backend")
    full_backend = importlib.reload(full_backend)
    module = importlib.import_module("pulse_dashboard.webapp_backend.routes.build_assets")
    module = importlib.reload(module)

    conn = duckdb.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE base_asset_index (
            instance_name VARCHAR,
            project_key VARCHAR,
            object_type VARCHAR,
            object_key VARCHAR,
            object_name VARCHAR,
            owner_login VARCHAR,
            updated_at TIMESTAMP,
            created_at TIMESTAMP,
            object_subtype VARCHAR
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE asset_activity_30d (
            instance_name VARCHAR,
            project_key VARCHAR,
            object_type VARCHAR,
            object_key VARCHAR,
            activity_30d INTEGER
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE v_object_activity_events (
            timestamp TIMESTAMP,
            instance_name VARCHAR,
            project_key VARCHAR,
            object_type VARCHAR,
            object_key VARCHAR,
            login VARCHAR
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE base_projects_instance_metadata (
            instance_name VARCHAR,
            project_key VARCHAR,
            extras VARCHAR
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE base_datasets_project_metadata (
            instance_name VARCHAR,
            project_key VARCHAR,
            datasets_name VARCHAR,
            extras VARCHAR
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE base_recipes_project_metadata (
            instance_name VARCHAR,
            project_key VARCHAR,
            recipes_name VARCHAR,
            extras VARCHAR
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE base_scenarios_project_metadata (
            instance_name VARCHAR,
            project_key VARCHAR,
            scenarios_id VARCHAR,
            extras VARCHAR
        );
        """
    )

    conn.executemany(
        "INSERT INTO base_asset_index VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("inst-a", "PROJ_A", "dataset", "customers", "Customers", "alice", "2024-06-01", "2024-01-01", "SQL"),
            ("inst-a", "PROJ_A", "recipe", "prep_customers", "Prep Customers", "alice", "2024-06-02", "2024-01-02", "Prepare"),
            ("inst-b", "PROJ_B", "project", "PROJ_B", "Project B", "bob", "2024-06-03", "2024-01-03", None),
        ],
    )
    conn.executemany(
        "INSERT INTO asset_activity_30d VALUES (?, ?, ?, ?, ?)",
        [
            ("inst-a", "PROJ_A", "dataset", "customers", 5),
            ("inst-a", "PROJ_A", "recipe", "prep_customers", 3),
            ("inst-b", "PROJ_B", "project", "PROJ_B", 1),
        ],
    )
    conn.executemany(
        "INSERT INTO v_object_activity_events VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("2024-06-10", "inst-a", "PROJ_A", "dataset", "customers", "viewer-1"),
            ("2024-06-11", "inst-a", "PROJ_A", "dataset", "customers", "viewer-2"),
            ("2024-06-12", "inst-a", "PROJ_A", "recipe", "prep_customers", "viewer-3"),
            ("2024-06-13", "inst-b", "PROJ_B", "project", "PROJ_B", "viewer-4"),
        ],
    )
    conn.execute(
        "INSERT INTO base_datasets_project_metadata VALUES ('inst-a', 'PROJ_A', 'customers', '{\"description\": \"Customer dataset\"}')"
    )

    def query_df(sql, params=None):
        return conn.execute(sql, params or []).df()

    def create_connection():
        return conn

    monkeypatch.setattr(module, "_require_duckdb_engine", lambda: (query_df, create_connection, lambda **kwargs: {}))

    app = Flask(__name__)
    module.register_routes(app)

    def asset_id(instance_name: str, project_key: str, object_type: str, object_key: str) -> str:
        return hashlib.md5(
            f"{instance_name}|{project_key}|{object_type}|{object_key}".encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()

    return module, app, asset_id


def test_assets_facets_accept_explicit_owner_for_self_scope(backend_module):
    _module, app, _asset_id = backend_module
    client = app.test_client()

    response = client.get("/api/build/assets/facets?scope=self&owner=alice")
    payload = response.get_json()

    assert response.status_code == 200, payload
    assert payload["instances"] == ["inst-a"]
    assert payload["projects"] == ["PROJ_A"]
    assert payload["types"] == ["dataset", "recipe"]
    assert payload["owners"] == ["alice"]


def test_assets_metadata_summary_accepts_explicit_owner_for_self_scope(backend_module):
    _module, app, _asset_id = backend_module
    client = app.test_client()

    response = client.get("/api/build/assets/metadata-summary?scope=self&owner=alice")
    payload = response.get_json()

    assert response.status_code == 200, payload
    assert payload["summary"]["completeCount"] >= 0
    assert all(row["label"] in {"dataset", "recipe"} for row in payload["byType"])


def test_assets_list_accepts_explicit_owner_for_self_scope(backend_module):
    _module, app, _asset_id = backend_module
    client = app.test_client()

    response = client.get("/api/build/assets?scope=self&owner=alice&limit=25&offset=0")
    payload = response.get_json()

    assert response.status_code == 200, payload
    assert payload["total"] == 2
    assert {row["ownerLogin"] for row in payload["rows"]} == {"alice"}


def test_assets_details_accepts_explicit_owner_for_self_scope(backend_module):
    _module, app, asset_id = backend_module
    client = app.test_client()

    current_asset_id = asset_id("inst-a", "PROJ_A", "dataset", "customers")
    response = client.get(f"/api/build/assets/details?scope=self&owner=alice&assetId={current_asset_id}")
    payload = response.get_json()

    assert response.status_code == 200, payload
    assert payload["asset"]["ownerLogin"] == "alice"
    assert payload["capturedInfo"]["description"] == "Customer dataset"


def test_assets_self_scope_still_requires_identity_without_explicit_owner(backend_module, monkeypatch):
    module, app, _asset_id = backend_module
    client = app.test_client()

    monkeypatch.setattr(module, "_current_user_auth_info", lambda: None)

    response = client.get("/api/build/assets/facets?scope=self")
    payload = response.get_json()

    assert response.status_code == 403, payload
    assert payload["error"] == "Unable to resolve authenticated user"
