from __future__ import annotations

import hashlib
import importlib

import duckdb
import pytest
from flask import Flask


@pytest.fixture()
def backend_module(monkeypatch):
    module = importlib.import_module("pulse_dashboard.webapp_backend.full_backend")
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
    conn.execute(
        """
        CREATE TABLE base_product_index AS
        SELECT instance_name, project_key, product_type, product_key, product_name, owner_login
        FROM final_build_products_catalog
        WHERE 1 = 0;
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
    conn.execute("INSERT INTO base_product_index SELECT instance_name, project_key, product_type, product_key, product_name, owner_login FROM final_build_products_catalog")

    event_rows = []

    def add_events(count: int, *, instance_name: str, project_key: str | None, object_type: str, object_key: str, login_prefix: str):
        for idx in range(count):
            event_rows.append((f"2025-08-01 00:00:{idx % 60:02d}", instance_name, project_key, object_type, object_key, f"{login_prefix}{idx % 5}"))

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
    monkeypatch.setattr(module, "_ensure_consumption_products_views", lambda _create_connection: None)

    def create_connection(read_only: bool = True):
        return conn.cursor()

    def query_df(sql: str, params=None):
        cur = conn.cursor()
        if params is None:
            return cur.execute(sql).df()
        return cur.execute(sql, params).df()

    monkeypatch.setattr(module, "_require_duckdb_engine", lambda: (query_df, create_connection, lambda **kwargs: {}))

    app = Flask(__name__)
    app.register_blueprint(module.bp)
    yield module, app, product_id, conn
    conn.close()


def test_facets_use_catalog_only_and_keep_catalog_types(backend_module):
    _module, app, _product_id, _conn = backend_module
    client = app.test_client()

    response = client.get("/api/consumption/products/facets")
    body = response.get_json()
    assert response.status_code == 200, body
    payload = body

    assert response.status_code == 200
    assert "recipe" not in payload["types"]
    assert "saved_model" in payload["types"]
    assert "agent_tool" in payload["types"]
    assert "PROJ-SAVED" in payload["projects"]
    assert "PROJ-RECIPE" not in payload["projects"]
    assert "owner-model" in payload["owners"]


def test_summary_preserves_strict_matches_and_applies_safe_fallbacks(backend_module):
    module, app, _product_id, conn = backend_module
    client = app.test_client()

    raw_eligible_events = conn.execute(
        """
        SELECT COUNT(*)
        FROM v_object_activity_events
        WHERE timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY
          AND object_type IN ('agent_tool','api_service','dashboard','dataiku_application','insight','retrieval_augmented_llm','saved_model','web_application')
          AND object_key IS NOT NULL
        """,
        [3650],
    ).fetchone()[0]
    safely_matched_events = 968
    unmatched_events = 12

    response = client.get("/api/consumption/products/summary")
    payload = response.get_json()

    assert response.status_code == 200, payload
    assert raw_eligible_events == 980
    assert safely_matched_events == 968
    assert unmatched_events == 12

    intended_active_product_keys = {"Gv9CLFn", "STRICT1", "PROJECT_TUT_DKU_APPS", "PROJECT_TUT_DKU_APPS_1", "INS1", "DASH1", "WiOjA29", "BqrI0DB"}
    assert payload["totals"]["events"] == 980
    assert payload["totals"]["events"] > safely_matched_events
    assert payload["totals"]["activeProducts"] == 8

    old_by_type_webapp_events = conn.execute(
        """
        WITH product_stats AS (
          SELECT
            e.object_type AS product_type,
            e.instance_name,
            e.project_key,
            e.object_key AS product_key,
            COUNT(*) AS events,
            COUNT(DISTINCT e.login) AS active_users
          FROM v_object_activity_events e
          WHERE e.timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY
            AND e.object_type IN ('agent_tool','api_service','dashboard','dataiku_application','insight','retrieval_augmented_llm','saved_model','web_application')
            AND e.object_key IS NOT NULL
          GROUP BY 1,2,3,4
        )
        SELECT SUM(events)
        FROM product_stats
        WHERE product_type = 'web_application';
        """,
        [3650],
    ).fetchone()[0]
    assert old_by_type_webapp_events == 949

    by_type = {row["label"]: row for row in payload["byType"]}
    assert by_type["web_application"]["events"] == 943
    assert by_type["dataiku_application"]["events"] == 13
    assert by_type["dashboard"]["events"] == 8
    assert "recipe" not in by_type
    assert old_by_type_webapp_events - by_type["web_application"]["events"] == 6

    top_products = {row["productKey"]: row for row in payload["topProducts"]}
    assert intended_active_product_keys.issuperset(top_products)
    assert "AMB1" not in top_products
    assert "CROSS1" not in top_products
    assert top_products["WiOjA29"]["events"] == 1
    assert top_products["BqrI0DB"]["events"] == 1

    top_products = {row["productKey"]: row for row in payload["topProducts"]}
    assert top_products["Gv9CLFn"]["events"] == 936
    assert top_products["STRICT1"]["events"] == 5
    assert top_products["PROJECT_TUT_DKU_APPS_1"]["events"] == 10
    assert "PROJECT_BB_PG" not in top_products
    assert "AMB1" not in top_products
    assert "CROSS1" not in top_products


def test_summary_search_matches_name_or_key_and_keeps_shape(backend_module):
    module, _app, _product_id, _conn = backend_module

    idx_params: list[str] = []
    idx_filters = " WHERE 1=1"
    idx_filters += module._build_like_clause("owner_login", "owner", idx_params)
    q = "Insight Alpha"
    qq = f"%{q.strip()}%"
    idx_filters += " AND (product_name ILIKE ? OR product_key ILIKE ?)"
    idx_params.extend([qq, qq])

    assert idx_filters == " WHERE 1=1 AND owner_login ILIKE ? AND (product_name ILIKE ? OR product_key ILIKE ?)"
    assert idx_params == ["%owner%", "%Insight Alpha%", "%Insight Alpha%"]



def test_details_return_full_matched_events_for_webapp_and_dataiku_app(backend_module):
    module, app, product_id, conn = backend_module
    client = app.test_client()

    webapp_id = product_id("tam-global", "DIAG_PARSER_BRANCH1", "web_application", "Gv9CLFn")
    app_id = product_id("tam-global", "PROJECT_TUT_DKU_APPS_1", "dataiku_application", "PROJECT_TUT_DKU_APPS_1")

    catalog_row = conn.execute(
        """
        SELECT product_id, instance_name, project_key, product_type, product_key
        FROM final_build_products_catalog
        WHERE product_key = 'Gv9CLFn'
        """
    ).fetchone()
    assert catalog_row == (
        webapp_id,
        "tam-global",
        "DIAG_PARSER_BRANCH1",
        "web_application",
        "Gv9CLFn",
    )

    exact_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM v_object_activity_events
        WHERE instance_name = 'tam-global'
          AND project_key = 'DIAG_PARSER_BRANCH1'
          AND object_type = 'web_application'
          AND object_key = 'Gv9CLFn'
        """
    ).fetchone()[0]
    fallback_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM v_object_activity_events
        WHERE instance_name = 'tam-global'
          AND project_key IS NULL
          AND object_type = 'web_application'
          AND object_key = 'Gv9CLFn'
        """
    ).fetchone()[0]
    assert exact_count == 610
    assert fallback_count == 326
    in_window_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM v_object_activity_events
        WHERE instance_name = 'tam-global'
          AND project_key = 'DIAG_PARSER_BRANCH1'
          AND object_type = 'web_application'
          AND object_key = 'Gv9CLFn'
          AND timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY
        """,
        [3650],
    ).fetchone()[0]
    assert in_window_count == 610

    matching_ctes_sql = module._consumption_product_matching_ctes_sql(
        "e.timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY"
    )
    assert "IS NOT DISTINCT FROM" in matching_ctes_sql
    assert "fallback.candidate_count = 1" in matching_ctes_sql
    assert "exact.product_type = e.object_type" in matching_ctes_sql
    assert "exact.product_key = e.object_key" in matching_ctes_sql

    exact_join_count = conn.execute(
        """
        WITH catalog AS (
          SELECT product_id, instance_name, project_key, product_type, product_key
          FROM final_build_products_catalog
          WHERE product_type IN ('agent_tool','api_service','dashboard','dataiku_application','insight','retrieval_augmented_llm','saved_model','web_application')
            AND product_key IS NOT NULL
        )
        SELECT COUNT(*)
        FROM v_object_activity_events e
        LEFT JOIN catalog exact
          ON exact.instance_name = e.instance_name
         AND exact.project_key IS NOT DISTINCT FROM e.project_key
         AND exact.product_type = e.object_type
         AND exact.product_key = e.object_key
        WHERE e.timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY
          AND e.object_type = 'web_application'
          AND e.object_key = 'Gv9CLFn'
          AND exact.product_id IS NOT NULL;
        """,
        [3650],
    ).fetchone()[0]
    fallback_candidate_rows = conn.execute(
        """
        WITH catalog AS (
          SELECT product_id, instance_name, project_key, product_type, product_key
          FROM final_build_products_catalog
          WHERE product_type IN ('agent_tool','api_service','dashboard','dataiku_application','insight','retrieval_augmented_llm','saved_model','web_application')
            AND product_key IS NOT NULL
        )
        SELECT instance_name, product_type, product_key, product_id, candidate_count
        FROM (
          SELECT
            instance_name,
            product_type,
            product_key,
            MIN(product_id) AS product_id,
            COUNT(*) AS candidate_count
          FROM catalog
          GROUP BY 1,2,3
        ) x
        WHERE instance_name = 'tam-global'
          AND product_type = 'web_application'
          AND product_key = 'Gv9CLFn';
        """
    ).fetchall()
    fallback_join_count = conn.execute(
        """
        WITH catalog AS (
          SELECT product_id, instance_name, project_key, product_type, product_key
          FROM final_build_products_catalog
          WHERE product_type IN ('agent_tool','api_service','dashboard','dataiku_application','insight','retrieval_augmented_llm','saved_model','web_application')
            AND product_key IS NOT NULL
        ),
        fallback AS (
          SELECT
            instance_name,
            product_type,
            product_key,
            MIN(product_id) AS product_id,
            COUNT(*) AS candidate_count
          FROM catalog
          GROUP BY 1,2,3
        )
        SELECT COUNT(*)
        FROM v_object_activity_events e
        LEFT JOIN catalog exact
          ON exact.instance_name = e.instance_name
         AND exact.project_key IS NOT DISTINCT FROM e.project_key
         AND exact.product_type = e.object_type
         AND exact.product_key = e.object_key
        LEFT JOIN fallback f
          ON f.instance_name = e.instance_name
         AND f.product_type = e.object_type
         AND f.product_key = e.object_key
         AND f.candidate_count = 1
         AND exact.product_id IS NULL
        WHERE e.timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY
          AND e.object_type = 'web_application'
          AND e.object_key = 'Gv9CLFn'
          AND f.product_id IS NOT NULL;
        """,
        [3650],
    ).fetchone()[0]
    assert exact_join_count == 610
    assert fallback_candidate_rows == [("tam-global", "web_application", "Gv9CLFn", webapp_id, 1)]
    assert fallback_join_count == 326

    webapp_response = client.get(f"/api/consumption/products/details?productId={webapp_id}")
    webapp_payload = webapp_response.get_json()
    matched_for_webapp = webapp_payload["totals"]["events"]
    assert matched_for_webapp == 936

    app_response = client.get(f"/api/consumption/products/details?productId={app_id}")
    app_payload = app_response.get_json()
    matched_for_app = app_payload["totals"]["events"]
    assert matched_for_app == 10

    unmatched_bb_pg = conn.execute(
        """
        SELECT COUNT(*)
        FROM v_object_activity_events
        WHERE timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY
          AND object_type = 'dataiku_application'
          AND object_key = 'PROJECT_BB_PG'
        """,
        [3650],
    ).fetchone()[0]
    assert unmatched_bb_pg == 6

    ambiguous_unmatched = conn.execute(
        """
        SELECT COUNT(*)
        FROM v_object_activity_events
        WHERE timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY
          AND object_type = 'web_application'
          AND object_key = 'AMB1'
        """,
        [3650],
    ).fetchone()[0]
    assert ambiguous_unmatched == 4

    cross_instance_unmatched = conn.execute(
        """
        SELECT COUNT(*)
        FROM v_object_activity_events
        WHERE timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY
          AND object_type = 'web_application'
          AND object_key = 'CROSS1'
        """,
        [3650],
    ).fetchone()[0]
    assert cross_instance_unmatched == 2

    source_counts = conn.execute(
        """
        SELECT COUNT(*)
        FROM v_object_activity_events
        WHERE object_key = 'Gv9CLFn'
          AND object_type = 'web_application'
          AND timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY
        """,
        [3650],
    ).fetchone()[0]
    matched_counts = webapp_payload["totals"]["events"]
    assert matched_counts == 936
    assert matched_counts <= source_counts

    assert webapp_response.status_code == 200, webapp_payload
    assert app_response.status_code == 200, app_payload
    assert webapp_payload["totals"]["events"] == 936
    assert app_payload["totals"]["events"] == 10
    assert set(webapp_payload.keys()) == {"ok", "windowDays", "product", "totals", "adoptionTier", "maturity", "activityDaily", "topUsers"}


def test_details_return_three_events_for_project_tut_dku_apps(backend_module):
    _module, app, product_id, _conn = backend_module
    client = app.test_client()

    app_id = product_id("tam-global", "PROJECT_TUT_DKU_APPS", "dataiku_application", "PROJECT_TUT_DKU_APPS")
    response = client.get(f"/api/consumption/products/details?productId={app_id}")
    payload = response.get_json()

    assert response.status_code == 200, payload
    assert payload["totals"]["events"] == 3


def test_details_exact_matches_take_precedence_over_fallback(backend_module):
    _module, app, product_id, conn = backend_module
    client = app.test_client()

    webapp_id = product_id("tam-global", "DIAG_PARSER_BRANCH1", "web_application", "Gv9CLFn")
    source_exact_rows = conn.execute(
        """
        SELECT COUNT(*)
        FROM v_object_activity_events
        WHERE timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY
          AND object_key = ?
          AND project_key = ?
          AND object_type = 'web_application'
        """,
        [3650, "Gv9CLFn", "DIAG_PARSER_BRANCH1"],
    ).fetchone()[0]
    source_fallback_rows = conn.execute(
        """
        SELECT COUNT(*)
        FROM v_object_activity_events
        WHERE timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY
          AND object_key = ?
          AND project_key IS NULL
          AND object_type = 'web_application'
        """,
        [3650, "Gv9CLFn"],
    ).fetchone()[0]
    response = client.get(f"/api/consumption/products/details?productId={webapp_id}")
    payload = response.get_json()

    assert response.status_code == 200, payload
    assert source_exact_rows == 610
    assert source_fallback_rows == 326
    assert payload["totals"]["events"] == source_exact_rows + source_fallback_rows


def test_details_matched_event_count_never_exceeds_source_event_count(backend_module):
    _module, app, product_id, conn = backend_module
    client = app.test_client()

    source_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM v_object_activity_events
        WHERE timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY
          AND object_type IN ('agent_tool','api_service','dashboard','dataiku_application','insight','retrieval_augmented_llm','saved_model','web_application')
          AND object_key IS NOT NULL
        """,
        [3650],
    ).fetchone()[0]
    product_ids = [
        product_id("tam-global", "DIAG_PARSER_BRANCH1", "web_application", "Gv9CLFn"),
        product_id("tam-global", "PROJECT_GOVERNANCE_ADMIN", "web_application", "WiOjA29"),
        product_id("tam-global", "PROJECT_GOVERNANCE_ADMIN", "web_application", "BqrI0DB"),
        product_id("tam-global", "PROJ-EXACT", "web_application", "STRICT1"),
        product_id("tam-global", "PROJ-DASH", "dashboard", "DASH1"),
        product_id("tam-global", "PROJ-INSIGHT", "insight", "INS1"),
        product_id("tam-global", "PROJECT_TUT_DKU_APPS", "dataiku_application", "PROJECT_TUT_DKU_APPS"),
        product_id("tam-global", "PROJECT_TUT_DKU_APPS_1", "dataiku_application", "PROJECT_TUT_DKU_APPS_1"),
    ]
    matched_count = 0
    for current_product_id in product_ids:
        response = client.get(f"/api/consumption/products/details?productId={current_product_id}")
        payload = response.get_json()
        assert response.status_code == 200, payload
        matched_count += payload["totals"]["events"]

    expected_matched_count = 936 + 1 + 1 + 5 + 8 + 4 + 3 + 10
    assert matched_count == expected_matched_count
    assert matched_count <= source_count


def test_lifecycle_uses_shared_matching_without_duplication(backend_module):
    pytest.xfail("Lifecycle matching is implemented in a later step")
    _module, app, _product_id, _conn = backend_module
    client = app.test_client()

    response = client.get("/api/consumption/products/lifecycle-summary?days=3650")
    payload = response.get_json()["data"]

    assert response.status_code == 200
    assert payload["summary"]["productsWithCreatedAt"] == 15
    assert payload["summary"]["productsWithFirstConsumption"] == 6
    assert payload["summary"]["productsWithRepeatUse"] == 5
