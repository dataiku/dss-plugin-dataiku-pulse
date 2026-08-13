from __future__ import annotations

import hashlib
import importlib

import duckdb
import pytest
from flask import Blueprint, Flask


@pytest.fixture()
def backend_module(monkeypatch):
    module = importlib.import_module("pulse_dashboard.webapp_backend.routes.consumption_products")
    module = importlib.reload(module)

    conn = duckdb.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE final_build_products_catalog (
            product_id VARCHAR,
            instance_name VARCHAR,
            project_key VARCHAR,
            product_type VARCHAR,
            product_key VARCHAR,
            product_name VARCHAR,
            owner_login VARCHAR,
            product_subtype VARCHAR,
            created_at TIMESTAMP,
            updated_at TIMESTAMP,
            activity_30d INTEGER,
            active_users_30d INTEGER
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

    def product_id(instance_name: str, project_key: str | None, product_type: str, product_key: str) -> str:
        return hashlib.md5(
            f"{instance_name}|{project_key or ''}|{product_type}|{product_key}".encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()

    catalog_rows = [
        (product_id("tam-global", "DIAG_PARSER_BRANCH1", "web_application", "Gv9CLFn"), "tam-global", "DIAG_PARSER_BRANCH1", "web_application", "Gv9CLFn", "admintoolkit", "owner-web", None, "2024-01-01", "2024-06-01", 0, 0),
        (product_id("tam-global", "PROJECT_GOVERNANCE_ADMIN", "web_application", "WiOjA29"), "tam-global", "PROJECT_GOVERNANCE_ADMIN", "web_application", "WiOjA29", "Project Inventory Review", "owner-web", None, "2024-01-01", "2024-06-01", 0, 0),
        (product_id("tam-global", "PROJECT_GOVERNANCE_ADMIN", "web_application", "BqrI0DB"), "tam-global", "PROJECT_GOVERNANCE_ADMIN", "web_application", "BqrI0DB", "Project-Answers", "owner-web", None, "2024-01-01", "2024-06-01", 0, 0),
        (product_id("tam-global", "PROJ-EXACT", "web_application", "STRICT1"), "tam-global", "PROJ-EXACT", "web_application", "STRICT1", "Strict App", "owner-strict", None, "2024-01-01", "2024-06-01", 0, 0),
        (product_id("tam-global", "PROJECT_TUT_DKU_APPS", "dataiku_application", "PROJECT_TUT_DKU_APPS"), "tam-global", "PROJECT_TUT_DKU_APPS", "dataiku_application", "PROJECT_TUT_DKU_APPS", "DKU App A", "owner-app", None, "2024-01-01", "2024-06-01", 0, 0),
        (product_id("tam-global", "PROJECT_TUT_DKU_APPS_1", "dataiku_application", "PROJECT_TUT_DKU_APPS_1"), "tam-global", "PROJECT_TUT_DKU_APPS_1", "dataiku_application", "PROJECT_TUT_DKU_APPS_1", "DKU App B", "owner-app", None, "2024-01-01", "2024-06-01", 0, 0),
        (product_id("tam-global", "PROJ-AMB-1", "web_application", "AMB1"), "tam-global", "PROJ-AMB-1", "web_application", "AMB1", "Ambiguous App 1", "owner-amb", None, "2024-01-01", "2024-06-01", 0, 0),
        (product_id("tam-global", "PROJ-AMB-2", "web_application", "AMB1"), "tam-global", "PROJ-AMB-2", "web_application", "AMB1", "Ambiguous App 2", "owner-amb", None, "2024-01-01", "2024-06-01", 0, 0),
        (product_id("other-instance", "PROJ-OTHER", "web_application", "CROSS1"), "other-instance", "PROJ-OTHER", "web_application", "CROSS1", "Cross Instance", "owner-other", None, "2024-01-01", "2024-06-01", 0, 0),
        (product_id("tam-global", "PROJ-SAVED", "saved_model", "MODEL1"), "tam-global", "PROJ-SAVED", "saved_model", "MODEL1", "Saved Model", "owner-model", None, "2024-01-01", "2024-06-01", 0, 0),
        (product_id("tam-global", "PROJ-AGENT", "agent_tool", "AGENT1"), "tam-global", "PROJ-AGENT", "agent_tool", "AGENT1", "Agent Tool", "owner-agent", None, "2024-01-01", "2024-06-01", 0, 0),
        (product_id("tam-global", "PROJ-API", "api_service", "API1"), "tam-global", "PROJ-API", "api_service", "API1", "API Service", "owner-api", None, "2024-01-01", "2024-06-01", 0, 0),
        (product_id("tam-global", "PROJ-INSIGHT", "insight", "INS1"), "tam-global", "PROJ-INSIGHT", "insight", "INS1", "Insight Alpha", "owner-insight", None, "2024-01-01", "2024-06-01", 0, 0),
        (product_id("tam-global", "PROJ-RAG", "retrieval_augmented_llm", "RAG1"), "tam-global", "PROJ-RAG", "retrieval_augmented_llm", "RAG1", "RAG Product", "owner-rag", None, "2024-01-01", "2024-06-01", 0, 0),
        (product_id("tam-global", "PROJ-DASH", "dashboard", "DASH1"), "tam-global", "PROJ-DASH", "dashboard", "DASH1", "Dash Product", "owner-dash", None, "2024-01-01", "2024-06-01", 0, 0),
    ]
    conn.executemany("INSERT INTO final_build_products_catalog VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", catalog_rows)

    event_rows = []

    def add_events(count: int, *, instance_name: str, project_key: str | None, object_type: str, object_key: str, login_prefix: str):
        for idx in range(count):
            event_rows.append((f"2026-08-01 00:00:{idx % 60:02d}", instance_name, project_key, object_type, object_key, f"{login_prefix}{idx % 5}"))

    add_events(610, instance_name="tam-global", project_key="DIAG_PARSER_BRANCH1", object_type="web_application", object_key="Gv9CLFn", login_prefix="strictg")
    add_events(326, instance_name="tam-global", project_key=None, object_type="web_application", object_key="Gv9CLFn", login_prefix="fallbackg")
    add_events(1, instance_name="tam-global", project_key="PROJECT_INVENTORY_ADMIN_AZURE", object_type="web_application", object_key="WiOjA29", login_prefix="wrongw")
    add_events(1, instance_name="tam-global", project_key="PROJECT_INVENTORY_ADMIN_AZURE", object_type="web_application", object_key="BqrI0DB", login_prefix="wrongb")
    add_events(5, instance_name="tam-global", project_key="PROJ-EXACT", object_type="web_application", object_key="STRICT1", login_prefix="strict1")
    add_events(4, instance_name="tam-global", project_key="PROJ-INSIGHT", object_type="insight", object_key="INS1", login_prefix="insight")
    add_events(10, instance_name="tam-global", project_key=None, object_type="dataiku_application", object_key="PROJECT_TUT_DKU_APPS_1", login_prefix="app1")
    add_events(3, instance_name="tam-global", project_key=None, object_type="dataiku_application", object_key="PROJECT_TUT_DKU_APPS", login_prefix="app0")
    add_events(6, instance_name="tam-global", project_key=None, object_type="dataiku_application", object_key="PROJECT_BB_PG", login_prefix="missing")
    add_events(4, instance_name="tam-global", project_key=None, object_type="web_application", object_key="AMB1", login_prefix="amb")
    add_events(2, instance_name="tam-global", project_key=None, object_type="web_application", object_key="CROSS1", login_prefix="cross")
    add_events(7, instance_name="tam-global", project_key="PROJ-RECIPE", object_type="recipe", object_key="REC1", login_prefix="recipe")
    add_events(8, instance_name="tam-global", project_key="PROJ-DASH", object_type="dashboard", object_key="DASH1", login_prefix="dash")

    conn.executemany("INSERT INTO v_object_activity_events VALUES (?, ?, ?, ?, ?, ?)", event_rows)

    monkeypatch.setattr(module, "_ensure_ready_if_enabled", lambda: None)
    monkeypatch.setattr(module, "_parse_days_arg", lambda default=30, maximum=None: 3650)

    def create_connection(read_only: bool = True):
        return conn.cursor()

    def query_df(sql: str, params=None):
        cur = conn.cursor()
        if params is None:
            return cur.execute(sql).df()
        return cur.execute(sql, params).df()

    monkeypatch.setattr(module, "_require_duckdb_engine", lambda: (query_df, create_connection, lambda **kwargs: {}))

    app = Flask(__name__)
    bp = Blueprint("test_consumption_products", __name__)
    module.register_routes(bp)
    app.register_blueprint(bp)

    yield module, app, product_id, conn
    conn.close()


def test_facets_accept_explicit_owner_for_self_scope(backend_module):
    _module, app, _product_id, _conn = backend_module
    client = app.test_client()

    response = client.get("/api/consumption/products/facets?scope=self&owner=owner-web")
    payload = response.get_json()

    assert response.status_code == 200, payload
    assert payload["instances"] == ["tam-global"]
    assert payload["projects"] == ["DIAG_PARSER_BRANCH1", "PROJECT_GOVERNANCE_ADMIN"]
    assert payload["types"] == ["web_application"]
    assert payload["owners"] == ["owner-web"]


def test_summary_accepts_explicit_owner_for_self_scope(backend_module):
    _module, app, _product_id, _conn = backend_module
    client = app.test_client()

    response = client.get("/api/consumption/products/summary?scope=self&owner=owner-web&days=3650")
    payload = response.get_json()

    assert response.status_code == 200, payload
    assert payload["windowDays"] == 365
    assert payload["totals"]["events"] >= 0
    assert all((row.get("ownerLogin") in {None, "owner-web"}) for row in payload["topProducts"])
    assert any(row["label"] == "web_application" for row in payload["byType"])


def test_details_accept_explicit_owner_for_self_scope(backend_module):
    _module, app, product_id, _conn = backend_module
    client = app.test_client()

    current_product_id = product_id("tam-global", "DIAG_PARSER_BRANCH1", "web_application", "Gv9CLFn")
    response = client.get(f"/api/consumption/products/details?scope=self&owner=owner-web&productId={current_product_id}")
    payload = response.get_json()

    assert response.status_code == 200, payload
    assert payload["product"]["ownerLogin"] == "owner-web"


def test_lifecycle_accepts_explicit_owner_for_self_scope(backend_module):
    _module, app, _product_id, _conn = backend_module
    client = app.test_client()

    response = client.get("/api/consumption/products/lifecycle-summary?scope=self&owner=owner-web&days=3650")
    payload = response.get_json()

    assert response.status_code == 200, payload
    assert payload["days"] == 3650
    assert payload["summary"]["productsWithCreatedAt"] >= 0


@pytest.mark.parametrize(
    "path",
    [
        "/api/consumption/products/facets?scope=self",
        "/api/consumption/products/summary?scope=self&days=30",
        "/api/consumption/products/lifecycle-summary?scope=self&days=3650",
    ],
)
def test_self_scoped_routes_without_owner_or_auth_still_fail(backend_module, monkeypatch, path):
    module, app, _product_id, _conn = backend_module
    client = app.test_client()

    monkeypatch.setattr(module, "_current_user_auth_info", lambda: None)

    response = client.get(path)
    payload = response.get_json()

    assert response.status_code == 403, payload
    assert payload["error"] == "Unable to resolve authenticated user"


def test_details_self_scope_without_owner_or_auth_still_fails(backend_module, monkeypatch):
    module, app, product_id, _conn = backend_module
    client = app.test_client()

    monkeypatch.setattr(module, "_current_user_auth_info", lambda: None)
    current_product_id = product_id("tam-global", "DIAG_PARSER_BRANCH1", "web_application", "Gv9CLFn")

    response = client.get(f"/api/consumption/products/details?scope=self&productId={current_product_id}")
    payload = response.get_json()

    assert response.status_code == 403, payload
    assert payload["error"] == "Unable to resolve authenticated user"


def test_non_self_behavior_remains_unchanged(backend_module):
    _module, app, _product_id, _conn = backend_module
    client = app.test_client()

    facets_response = client.get("/api/consumption/products/facets")
    summary_response = client.get("/api/consumption/products/summary?days=3650")

    assert facets_response.status_code == 200, facets_response.get_json()
    assert summary_response.status_code == 200, summary_response.get_json()
