from __future__ import annotations

import importlib

import duckdb
import pytest
from flask import Flask


@pytest.fixture()
def license_api_app(monkeypatch):
    module = importlib.import_module("pulse_dashboard.webapp_backend.routes.build_users")
    module = importlib.reload(module)

    conn = duckdb.connect(":memory:")

    def query_df(sql: str, params=None):
        return conn.execute(sql, params or []).df()

    def create_connection(read_only: bool = True):
        return conn.cursor()

    monkeypatch.setattr(module, "_ensure_ready_if_enabled", lambda: None)
    monkeypatch.setattr(module, "_require_duckdb_engine", lambda: (query_df, create_connection, lambda **kwargs: {}))

    app = Flask(__name__)
    module.register_routes(app)
    yield app, conn
    conn.close()


def _create_fact(conn):
    conn.execute(
        """
        CREATE TABLE fact_license_utilization_daily (
            snapshot_date DATE,
            instance_name VARCHAR,
            license_profile VARCHAR,
            entitled_count INTEGER,
            assigned_count INTEGER,
            available_count INTEGER,
            utilization_pct DOUBLE,
            run_ts TIMESTAMP
        );
        """
    )


def _insert_fact_rows(conn):
    conn.executemany(
        "INSERT INTO fact_license_utilization_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("2024-01-01", "inst-a", "DESIGNER", 10, 4, 6, 40.0, "2024-01-01 02:00:00"),
            ("2024-01-02", "inst-a", "DESIGNER", 10, 5, 5, 50.0, "2024-01-02 02:00:00"),
            ("2024-01-02", "inst-a", "READER", None, 2, None, None, "2024-01-02 02:00:00"),
            ("2024-01-02", "inst-a", "ZERO", 0, 1, -1, None, "2024-01-02 02:00:00"),
            ("2024-01-03", "inst-b", "DESIGNER", 20, 12, 8, 60.0, "2024-01-03 02:00:00"),
        ],
    )


def test_latest_rows_are_sourced_from_fact(license_api_app):
    app, conn = license_api_app
    _create_fact(conn)
    _insert_fact_rows(conn)

    response = app.test_client().get("/api/build/users/license-utilization")
    payload = response.get_json()

    assert response.status_code == 200, payload
    assert payload["ok"] is True
    assert payload["available"] is True
    assert payload["meta"]["source"] == "fact_license_utilization_daily"

    rows = {(row["instance_name"], row["license_profile"]): row for row in payload["latestRows"]}
    assert rows[("inst-a", "DESIGNER")]["snapshot_date"].startswith("2024-01-02")
    assert rows[("inst-a", "DESIGNER")]["entitled_count"] == 10
    assert rows[("inst-a", "DESIGNER")]["assigned_count"] == 5
    assert rows[("inst-a", "DESIGNER")]["available_count"] == 5
    assert rows[("inst-a", "DESIGNER")]["utilization_pct"] == 50.0
    assert rows[("inst-b", "DESIGNER")]["entitled_count"] == 20


def test_instance_filter_preserves_instance_grain(license_api_app):
    app, conn = license_api_app
    _create_fact(conn)
    _insert_fact_rows(conn)

    response = app.test_client().get("/api/build/users/license-utilization?instance_name=inst-a")
    payload = response.get_json()

    assert response.status_code == 200, payload
    assert payload["instanceName"] == "inst-a"
    assert payload["meta"]["preservesSeparateInstances"] is False
    assert {row["instance_name"] for row in payload["latestRows"]} == {"inst-a"}
    assert {row["instance_name"] for row in payload["historyRows"]} == {"inst-a"}


def test_absent_fact_returns_explicit_unavailable_state(license_api_app):
    app, _conn = license_api_app

    response = app.test_client().get("/api/build/users/license-utilization")
    payload = response.get_json()

    assert response.status_code == 200, payload
    assert payload["ok"] is True
    assert payload["available"] is False
    assert payload["latestRows"] == []
    assert payload["historyRows"] == []
    assert "Rebuild GOLD tables" in payload["unavailableReason"]


def test_null_and_zero_entitlement_states_are_preserved(license_api_app):
    app, conn = license_api_app
    _create_fact(conn)
    _insert_fact_rows(conn)

    response = app.test_client().get("/api/build/users/license-utilization?instance_name=inst-a")
    payload = response.get_json()

    rows = {row["license_profile"]: row for row in payload["latestRows"]}
    assert rows["READER"]["entitled_count"] is None
    assert rows["READER"]["available_count"] is None
    assert rows["READER"]["utilization_pct"] is None
    assert rows["ZERO"]["entitled_count"] == 0
    assert rows["ZERO"]["available_count"] == -1
    assert rows["ZERO"]["utilization_pct"] is None


def test_multiple_instances_are_not_summed_into_capacity_total(license_api_app):
    app, conn = license_api_app
    _create_fact(conn)
    _insert_fact_rows(conn)

    response = app.test_client().get("/api/build/users/license-utilization")
    payload = response.get_json()

    assert response.status_code == 200, payload
    assert payload["meta"]["preservesSeparateInstances"] is True
    assert payload["meta"]["scopeLabel"] == "separate DSS instances"
    assert payload["meta"]["seriesCount"] == 4
    assert "totals" not in payload
    assert {row["instance_name"] for row in payload["latestRows"]} == {"inst-a", "inst-b"}


def test_history_window_is_applied_per_instance_profile_series(license_api_app):
    app, conn = license_api_app
    _create_fact(conn)
    conn.executemany(
        "INSERT INTO fact_license_utilization_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("2024-01-01", "inst-a", "DESIGNER", 10, 4, 6, 40.0, "2024-01-01 02:00:00"),
            ("2024-01-02", "inst-a", "DESIGNER", 10, 5, 5, 50.0, "2024-01-02 02:00:00"),
            ("2024-02-01", "inst-b", "DESIGNER", 20, 12, 8, 60.0, "2024-02-01 02:00:00"),
        ],
    )

    response = app.test_client().get("/api/build/users/license-utilization?days=1")
    payload = response.get_json()

    assert response.status_code == 200, payload
    assert payload["meta"]["latestSnapshotDates"] == ["2024-01-02", "2024-02-01"]
    history_keys = {(row["instance_name"], row["license_profile"]) for row in payload["historyRows"]}
    assert history_keys == {("inst-a", "DESIGNER"), ("inst-b", "DESIGNER")}
