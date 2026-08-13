from __future__ import annotations

from collections.abc import Callable

import pandas as pd
from flask import Flask

import pulse_dashboard.webapp_backend.full_backend as fb


class FakeConsumptionSummaryEngine:
    def __init__(self):
        self.calls: list[tuple[str, list[object]]] = []
        self.last_idx_params: list[object] = []

    def query_df(self, sql, params=None):
        params_list = list(params or [])
        normalized = " ".join(sql.split())
        self.calls.append((sql, params_list))

        if "FROM final_build_products_catalog" in normalized and "product_name ILIKE ? OR product_key ILIKE ?" in normalized:
            self.last_idx_params = params_list
            return pd.DataFrame(
                [
                    {
                        "instance_name": "instA",
                        "project_key": "PROJ1",
                        "product_type": "web_application",
                        "product_key": "WK1",
                    }
                ]
            )

        if "COUNT(*) AS eligible_events" in normalized:
            return pd.DataFrame(
                [
                    {
                        "eligible_events": 5,
                        "exact_matches": 2,
                        "fallback_matches": 1,
                        "matched_events": 3,
                        "unmatched_events": 2,
                    }
                ]
            )

        if "AND e.matched_product_id IS NULL GROUP BY 1" in normalized:
            return pd.DataFrame([
                {"label": "web_application", "events": 1},
                {"label": "dashboard", "events": 1},
            ])

        if "SELECT DISTINCT e.instance_name AS instanceName" in normalized:
            return pd.DataFrame(
                [
                    {
                        "instanceName": "instA",
                        "projectKey": None,
                        "productType": "web_application",
                        "productKey": "AMB1",
                    }
                ]
            )

        if "COUNT(DISTINCT matched_product_id) FILTER" in normalized and "FROM matched_events e" in normalized:
            return pd.DataFrame([
                {"events": 3, "active_users": 2, "active_products": 2}
            ])

        if "FROM product_rollup p CROSS JOIN product_concentration pc;" in normalized:
            return pd.DataFrame(
                [
                    {
                        "avg_users_per_product": 1.5,
                        "collaborative_products": 1,
                        "single_user_products": 1,
                        "multi_user_light_products": 1,
                        "repeat_products": 1,
                        "adopted_products": 1,
                        "top_product_events": 2,
                        "top1_product_events": 2,
                        "top5_product_events": 3,
                    }
                ]
            )

        if "FROM user_rollup u CROSS JOIN user_concentration uc;" in normalized:
            return pd.DataFrame(
                [
                    {
                        "avg_products_per_user": 1.5,
                        "top_user_products": 2,
                        "top1_user_events": 2,
                        "top5_user_events": 3,
                    }
                ]
            )

        if "FROM type_rollup tr" in normalized:
            return pd.DataFrame(
                [
                    {
                        "label": "web_application",
                        "events": 2,
                        "active_users": 2,
                        "active_products": 1,
                        "avg_users_per_product": 2.0,
                        "max_users_on_product": 2,
                        "avg_maturity_score": 0.9,
                        "max_maturity_score": 0.9,
                        "adoption_count": 1,
                    },
                    {
                        "label": "dashboard",
                        "events": 1,
                        "active_users": 1,
                        "active_products": 1,
                        "avg_users_per_product": 1.0,
                        "max_users_on_product": 1,
                        "avg_maturity_score": 0.4,
                        "max_maturity_score": 0.4,
                        "adoption_count": 0,
                    },
                ]
            )

        if "GROUP BY 1 ORDER BY 1;" in normalized and "date_trunc('day', timestamp)" in normalized:
            return pd.DataFrame(
                [
                    {"label": "2026-08-03", "value": 1},
                    {"label": "2026-08-04", "value": 2},
                ]
            )

        if "INNER JOIN final_build_products_catalog c ON c.product_id = act.product_id" in normalized:
            return pd.DataFrame(
                [
                    {
                        "productId": "p-exact",
                        "instanceName": "instA",
                        "projectKey": "PROJ1",
                        "productType": "web_application",
                        "productKey": "WK1",
                        "productName": "Workbench 1",
                        "ownerLogin": "owner1",
                        "events": 2,
                        "activeUsers": 2,
                        "lastActivityAt": "2026-08-04T00:00:00",
                    },
                    {
                        "productId": "p-fallback",
                        "instanceName": "instA",
                        "projectKey": "PROJ2",
                        "productType": "dashboard",
                        "productKey": "DB1",
                        "productName": "Dashboard 1",
                        "ownerLogin": "owner2",
                        "events": 1,
                        "activeUsers": 1,
                        "lastActivityAt": "2026-08-03T00:00:00",
                    },
                ]
            )

        raise AssertionError(f"Unexpected SQL: {sql}")


def _build_test_app(monkeypatch) -> tuple[Flask, FakeConsumptionSummaryEngine]:
    engine = FakeConsumptionSummaryEngine()

    def fake_require_duckdb_engine():
        return engine.query_df, (lambda read_only=False: _FakeDirectConnection([{"events": 3, "active_users": 2, "active_products": 2}])), (lambda *args, **kwargs: {"ok": True})

    monkeypatch.setattr(fb, "_require_duckdb_engine", fake_require_duckdb_engine)
    monkeypatch.setattr(fb, "_ensure_ready_if_enabled", lambda: None)
    monkeypatch.setattr(fb, "_ensure_consumption_products_views", lambda create_connection: None)

    flask_app = Flask(__name__)
    flask_app.register_blueprint(fb.bp)
    return flask_app, engine


class _FakeDirectConnection:
    def __init__(self, rows):
        self._rows = rows
        self.calls: list[tuple[str, list[object] | None]] = []

    def execute(self, sql, params=None):
        self.calls.append((sql, list(params or [])))
        return self

    def df(self):
        return pd.DataFrame(self._rows)

    def close(self):
        return None


def _make_context(app: Flask, engine: FakeConsumptionSummaryEngine, query_string: str = ""):
    with app.test_request_context(f"/api/consumption/products/summary{query_string}"):
        filters = fb._parse_consumption_product_summary_filters()
        return fb._build_consumption_product_query_context(engine.query_df, **filters)


def test_build_consumption_product_query_context_normalizes_exact_and_fallback(monkeypatch):
    app, engine = _build_test_app(monkeypatch)
    context = _make_context(app, engine)

    assert context.days == 30
    assert context.filters["types"] == []
    assert "exact.instance_name_norm = lower(trim(CAST(e.instance_name AS VARCHAR)))" in " ".join(context.matched_events_cte.split())
    assert "exact.project_key_norm IS NOT DISTINCT FROM NULLIF(trim(CAST(e.project_key AS VARCHAR)), '')" in " ".join(context.matched_events_cte.split())
    assert "exact.product_type_norm = lower(trim(CAST(e.object_type AS VARCHAR)))" in " ".join(context.matched_events_cte.split())
    assert "exact.product_key_norm = trim(CAST(e.object_key AS VARCHAR))" in " ".join(context.matched_events_cte.split())
    assert "fallback.candidate_count = 1" in context.matched_events_cte
    assert "COALESCE(exact.product_id, fallback.product_id) AS matched_product_id" in context.matched_events_cte
    assert "AS normalized_instance_name" in context.matched_events_cte
    assert "AS normalized_project_key" in context.matched_events_cte
    assert "AS normalized_product_type" in context.matched_events_cte
    assert "AS normalized_product_key" in context.matched_events_cte
    assert "eligible_events AS ( SELECT e.* FROM v_object_activity_events e" in " ".join(context.matched_events_cte.split())


def test_build_consumption_product_query_context_uses_catalog_filter_pairs(monkeypatch):
    app, engine = _build_test_app(monkeypatch)
    context = _make_context(
        app,
        engine,
        "?days=15&q=bench&owner=owner1&instances=instA&projects=PROJ1&types=web_application",
    )

    assert context.days == 15
    assert context.filters == {
        "days": 15,
        "q": "bench",
        "instances": ["instA"],
        "projects": ["PROJ1"],
        "types": ["web_application"],
        "owner": "owner1",
    }
    assert "EXISTS (SELECT 1 FROM catalog c WHERE c.product_id = e.matched_product_id" in context.matched_where_sql
    assert context.matched_where_params == ["%owner1%", "%bench%", "%bench%"]


def test_totals_helper_uses_matched_population(monkeypatch):
    app, engine = _build_test_app(monkeypatch)
    context = _make_context(app, engine)
    direct_conn = _FakeDirectConnection([{"events": 3, "active_users": 2, "active_products": 2}])

    totals = fb._query_consumption_product_totals(engine.query_df, lambda read_only=True: direct_conn, context)

    assert totals == {
        "events": 3,
        "activeUsers": 2,
        "activeProducts": 2,
        "executionPath": "standalone_literal_days",
        "windowDaysUsed": 30,
        "parameterCount": 0,
        "backendFile": fb._CONSUMPTION_MATCHER_FILE,
    }
    totals_sql = direct_conn.calls[0][0]
    assert context.matched_events_cte not in totals_sql
    assert "now() - 30 * INTERVAL 1 DAY" in totals_sql
    assert "?::INTEGER" not in totals_sql
    assert "LEFT JOIN catalog exact" in totals_sql
    assert "LEFT JOIN fallback_keys fallback" in totals_sql
    assert "AND fallback.candidate_count = 1" in totals_sql
    assert "AND exact.product_id IS NULL" in totals_sql
    assert direct_conn.calls[0][1] == []


def test_product_rollups_helper_uses_only_matched_events(monkeypatch):
    app, engine = _build_test_app(monkeypatch)
    context = _make_context(app, engine)

    rollups = fb._query_consumption_product_product_rollups(engine.query_df, context)

    assert rollups["top1ProductEvents"] == 2
    assert rollups["top5ProductEvents"] == 3
    sql = next(
        sql
        for sql, _ in engine.calls
        if "FROM product_rollup p" in " ".join(sql.split()) and "CROSS JOIN product_concentration pc" in " ".join(sql.split())
    )
    assert "e.matched_product_id IS NOT NULL" in sql


def test_user_rollups_helper_uses_only_matched_events(monkeypatch):
    app, engine = _build_test_app(monkeypatch)
    context = _make_context(app, engine)

    rollups = fb._query_consumption_product_user_rollups(engine.query_df, context)

    assert rollups == {
        "avgProductsPerUser": 1.5,
        "topUserProducts": 2,
        "top1UserEvents": 2,
        "top5UserEvents": 3,
    }
    sql = next(
        sql
        for sql, _ in engine.calls
        if "FROM user_rollup u" in " ".join(sql.split()) and "CROSS JOIN user_concentration uc" in " ".join(sql.split())
    )
    assert "e.matched_product_id IS NOT NULL" in sql


def test_activity_and_type_totals_are_consistent(monkeypatch):
    app, engine = _build_test_app(monkeypatch)
    context = _make_context(app, engine)
    direct_conn = _FakeDirectConnection([{"events": 3, "active_users": 2, "active_products": 2}])

    totals = fb._query_consumption_product_totals(engine.query_df, lambda read_only=True: direct_conn, context)
    activity_daily = fb._query_consumption_product_activity_daily(engine.query_df, context)
    by_type = fb._query_consumption_product_by_type(engine.query_df, context)

    assert totals["events"] == sum(int(row["value"]) for row in activity_daily["activityDaily"])
    assert totals["events"] == sum(int(row["events"]) for row in by_type["byType"])
    assert totals["activeProducts"] == sum(int(row["active_products"]) for row in by_type["byType"])


def test_diagnostics_helper_schema_and_unmatched_visibility(monkeypatch):
    app, engine = _build_test_app(monkeypatch)
    context = _make_context(app, engine)

    diagnostics = fb._query_consumption_product_diagnostics(engine.query_df, context)

    assert diagnostics == {
        "eligibleEvents": 5,
        "exactMatches": 2,
        "fallbackMatches": 1,
        "matchedEvents": 3,
        "unmatchedEvents": 2,
        "unmatchedByType": [
            {"label": "web_application", "events": 1},
            {"label": "dashboard", "events": 1},
        ],
        "sampleUnmatchedKeys": [
            {
                "instanceName": "instA",
                "projectKey": None,
                "productType": "web_application",
                "productKey": "AMB1",
            }
        ],
    }

    diagnostics_sql = next(sql for sql, _ in engine.calls if "COUNT(*) AS eligible_events" in sql)
    normalized_sql = " ".join(diagnostics_sql.split())
    assert "COUNT(*) FILTER (WHERE matched_product_id IS NOT NULL AND normalized_project_key IS NOT NULL)" not in normalized_sql
    assert "COUNT(*) FILTER (WHERE matched_product_id IS NOT NULL AND normalized_project_key IS NULL)" not in normalized_sql
    assert "COUNT(*) FILTER (WHERE match_type = 'exact') AS exact_matches" in normalized_sql
    assert "COUNT(*) FILTER (WHERE match_type = 'fallback') AS fallback_matches" in normalized_sql
    assert "COUNT(*) FILTER (WHERE matched_product_id IS NOT NULL) AS matched_events" in normalized_sql
    assert "COUNT(*) FILTER (WHERE matched_product_id IS NULL) AS unmatched_events" in normalized_sql


def test_normalized_join_uses_normalized_aliases(monkeypatch):
    app, engine = _build_test_app(monkeypatch)
    context = _make_context(app, engine)

    normalized_sql = " ".join(context.matched_events_cte.split())
    assert "exact.instance_name_norm = lower(trim(CAST(e.instance_name AS VARCHAR)))" in normalized_sql
    assert "exact.project_key_norm IS NOT DISTINCT FROM NULLIF(trim(CAST(e.project_key AS VARCHAR)), '')" in normalized_sql
    assert "exact.product_type_norm = lower(trim(CAST(e.object_type AS VARCHAR)))" in normalized_sql
    assert "exact.product_key_norm = trim(CAST(e.object_key AS VARCHAR))" in normalized_sql
    assert "fallback.instance_name_norm = lower(trim(CAST(e.instance_name AS VARCHAR)))" in normalized_sql
    assert "fallback.product_type_norm = lower(trim(CAST(e.object_type AS VARCHAR)))" in normalized_sql
    assert "fallback.product_key_norm = trim(CAST(e.object_key AS VARCHAR))" in normalized_sql


def test_null_and_empty_project_keys_match_via_not_distinct(monkeypatch):
    app, engine = _build_test_app(monkeypatch)
    context = _make_context(app, engine)

    normalized_sql = " ".join(context.matched_events_cte.split())
    assert "NULLIF(trim(CAST(project_key AS VARCHAR)), '') AS project_key_norm" in normalized_sql
    assert "exact.project_key_norm IS NOT DISTINCT FROM NULLIF(trim(CAST(e.project_key AS VARCHAR)), '')" in normalized_sql


def test_safe_fallback_remains_unique_only(monkeypatch):
    app, engine = _build_test_app(monkeypatch)
    context = _make_context(app, engine)

    normalized_sql = " ".join(context.matched_events_cte.split())
    assert "COUNT(DISTINCT product_id) AS candidate_count" in normalized_sql
    assert "fallback.candidate_count = 1" in normalized_sql
    assert "AND exact.product_id IS NULL" in normalized_sql


def test_event_norm_alias_collisions_do_not_affect_matching(monkeypatch):
    app, engine = _build_test_app(monkeypatch)
    context = _make_context(app, engine)

    normalized_sql = " ".join(context.matched_events_cte.split())
    assert "SELECT e.* FROM v_object_activity_events e" in normalized_sql
    assert "exact.instance_name_norm = e.instance_name_norm" not in normalized_sql
    assert "exact.project_key_norm IS NOT DISTINCT FROM e.project_key_norm" not in normalized_sql
    assert "exact.product_type_norm = e.product_type_norm" not in normalized_sql
    assert "exact.product_key_norm = e.product_key_norm" not in normalized_sql


def test_source_matcher_columns_do_not_shadow_computed_match_fields(monkeypatch):
    app, engine = _build_test_app(monkeypatch)
    context = _make_context(app, engine)

    normalized_sql = " ".join(context.matched_events_cte.split())
    assert "SELECT e.*, exact.product_id AS exact_product_id" not in normalized_sql
    assert "matched_events AS ( SELECT e.timestamp, e.instance_name, e.project_key, e.object_type, e.object_key, e.login" in normalized_sql

    diagnostics = fb._query_consumption_product_diagnostics(engine.query_df, context)
    totals = fb._query_consumption_product_totals(engine.query_df, lambda read_only=True: _FakeDirectConnection([{"events": 3, "active_users": 2, "active_products": 2}]), context)

    assert diagnostics["exactMatches"] > 0
    assert diagnostics["matchedEvents"] > 0
    assert totals["events"] > 0
    assert totals["activeProducts"] > 0


def test_totals_query_df_matches_direct_connection(monkeypatch):
    app, engine = _build_test_app(monkeypatch)
    context = _make_context(app, engine)

    direct_rows = [{"events": 3, "active_users": 2, "active_products": 2}]
    direct_conn = _FakeDirectConnection(direct_rows)

    totals = fb._query_consumption_product_totals(
        engine.query_df,
        lambda read_only=True: direct_conn,
        context,
    )

    assert totals == {
        "events": 3,
        "activeUsers": 2,
        "activeProducts": 2,
        "executionPath": "standalone_literal_days",
        "windowDaysUsed": 30,
        "parameterCount": 0,
        "backendFile": fb._CONSUMPTION_MATCHER_FILE,
    }
    sql, params = direct_conn.calls[0]
    normalized_sql = " ".join(sql.split())
    assert "WITH catalog AS (" in normalized_sql
    assert "fallback.candidate_count = 1" in normalized_sql
    assert "exact.product_id IS NULL" in normalized_sql
    assert params == []


def test_totals_days_value_is_clamped(monkeypatch):
    _app, engine = _build_test_app(monkeypatch)
    low_context = fb.ConsumptionProductQueryContext(
        matched_events_cte="irrelevant",
        params=[],
        days=0,
        filters={"days": 0, "instances": [], "projects": [], "types": [], "q": "", "owner": ""},
        matched_where_sql="",
        matched_where_params=[],
    )
    high_context = fb.ConsumptionProductQueryContext(
        matched_events_cte="irrelevant",
        params=[],
        days=999,
        filters={"days": 999, "instances": [], "projects": [], "types": [], "q": "", "owner": ""},
        matched_where_sql="",
        matched_where_params=[],
    )
    low_conn = _FakeDirectConnection([{"events": 1, "active_users": 1, "active_products": 1}])
    high_conn = _FakeDirectConnection([{"events": 1, "active_users": 1, "active_products": 1}])

    low_totals = fb._query_consumption_product_totals(engine.query_df, lambda read_only=True: low_conn, low_context)
    high_totals = fb._query_consumption_product_totals(engine.query_df, lambda read_only=True: high_conn, high_context)

    assert "now() - 1 * INTERVAL 1 DAY" in low_conn.calls[0][0]
    assert low_totals["windowDaysUsed"] == 1
    assert "now() - 365 * INTERVAL 1 DAY" in high_conn.calls[0][0]
    assert high_totals["windowDaysUsed"] == 365


def test_totals_filters_remain_parameterized(monkeypatch):
    app, engine = _build_test_app(monkeypatch)
    context = _make_context(
        app,
        engine,
        "?days=15&instances=instA,instB&projects=PROJ1&types=web_application,dashboard&q=bench&owner=owner1",
    )
    direct_conn = _FakeDirectConnection([{"events": 3, "active_users": 2, "active_products": 2}])

    fb._query_consumption_product_totals(engine.query_df, lambda read_only=True: direct_conn, context)

    sql, params = direct_conn.calls[0]
    assert "e.instance_name IN (?, ?)" in sql
    assert "e.project_key IN (?)" in sql
    assert "COALESCE(c.owner_login, '') ILIKE ?" in sql
    assert "COALESCE(c.product_name, '') ILIKE ? OR COALESCE(c.product_key, '') ILIKE ?" in sql
    assert params == ["instA", "instB", "PROJ1", "%owner1%", "%bench%", "%bench%"]
    assert "now() - 15 * INTERVAL 1 DAY" in sql
    assert "?::INTEGER" not in sql


def test_main_summary_matches_combined_helper_outputs(monkeypatch):
    app, engine = _build_test_app(monkeypatch)
    context = _make_context(app, engine)

    totals = fb._query_consumption_product_totals(engine.query_df, lambda read_only=True: _FakeDirectConnection([{"events": 3, "active_users": 2, "active_products": 2}]), context)
    product_rollups = fb._query_consumption_product_product_rollups(engine.query_df, context)
    user_rollups = fb._query_consumption_product_user_rollups(engine.query_df, context)
    activity_daily = fb._query_consumption_product_activity_daily(engine.query_df, context)
    by_type = fb._query_consumption_product_by_type(engine.query_df, context)
    top_products = fb._query_consumption_product_top_products(engine.query_df, context)
    maturity = fb._calculate_consumption_product_maturity(totals, product_rollups, user_rollups)
    expected = fb._build_consumption_product_summary_payload(
        context=context,
        totals=totals,
        product_rollups=product_rollups,
        user_rollups=user_rollups,
        activity_daily=activity_daily,
        by_type=by_type,
        top_products=top_products,
        maturity=maturity,
    )

    with app.test_request_context("/api/consumption/products/summary?days=30"):
        response, status = fb.consumption_products_summary()

    assert status == 200
    assert response.get_json() == {"ok": True, **expected}


def test_summary_subroutes_return_expected_schema_and_identical_filters(monkeypatch):
    app, _engine = _build_test_app(monkeypatch)
    client = app.test_client()
    query = "?days=30&q=bench&instances=instA&projects=PROJ1&types=web_application&owner=owner1"
    route_to_key = {
        "/api/consumption/products/summary/totals": "events",
        "/api/consumption/products/summary/product-rollups": "avgUsersPerProduct",
        "/api/consumption/products/summary/user-rollups": "avgProductsPerUser",
        "/api/consumption/products/summary/activity-daily": "activityDaily",
        "/api/consumption/products/summary/by-type": "byType",
        "/api/consumption/products/summary/top-products": "topProducts",
        "/api/consumption/products/summary/diagnostics": "eligibleEvents",
    }

    for route, key in route_to_key.items():
        payload = client.get(route + query).get_json()
        assert payload["ok"] is True
        assert key in payload


def test_summary_response_schema_remains_unchanged(monkeypatch):
    app, _engine = _build_test_app(monkeypatch)

    with app.test_request_context("/api/consumption/products/summary?days=30"):
        response, status = fb.consumption_products_summary()

    assert status == 200
    payload = response.get_json()
    assert set(payload) == {"ok", "windowDays", "totals", "activityDaily", "byType", "topProducts"}
    assert set(payload["totals"]) == {
        "events",
        "activeUsers",
        "activeProducts",
        "avgUsersPerProduct",
        "collaborativeProducts",
        "repeatProducts",
        "singleUserProducts",
        "multiUserLightProducts",
        "adoptedProducts",
        "topProductEvents",
        "topUserProducts",
        "avgProductsPerUser",
        "top1ProductEvents",
        "top5ProductEvents",
        "top1UserEvents",
        "top5UserEvents",
        "maturityScore",
        "maturityTier",
        "maturityComponents",
    }
