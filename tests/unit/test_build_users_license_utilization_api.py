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


def _create_current_base_tables(conn):
    conn.execute(
        """
        CREATE TABLE base_users_instance_metadata (
            instance_name VARCHAR,
            users_login VARCHAR,
            users_displayname VARCHAR,
            users_enabled VARCHAR,
            users_userprofile VARCHAR,
            run_ts TIMESTAMP
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE base_license_max_licenses_latest (
            instance_name VARCHAR,
            license_profile VARCHAR,
            max_licenses INTEGER,
            run_ts TIMESTAMP
        );
        """
    )


def _insert_current_base_rows(conn):
    conn.executemany(
        "INSERT INTO base_users_instance_metadata VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("inst-a", "alice", "Alice", "True", "DESIGNER", "2024-02-01 00:00:00"),
            ("inst-a", "bob", "Bob", "True", "DESIGNER", "2024-02-01 00:00:00"),
            ("inst-a", "carol", "Carol", "False", "DESIGNER", "2024-02-01 00:00:00"),
            ("inst-b", "alice", "Alice", "True", "DESIGNER", "2024-02-01 00:00:00"),
            ("inst-b", "dana", "Dana", "True", "READER", "2024-02-01 00:00:00"),
            ("inst-b", "erin", "Erin", "True", "ZERO", "2024-02-01 00:00:00"),
        ],
    )
    conn.executemany(
        "INSERT INTO base_license_max_licenses_latest VALUES (?, ?, ?, ?)",
        [
            ("inst-a", "DESIGNER", 10, "2024-02-01 00:00:00"),
            ("inst-b", "DESIGNER", 20, "2024-02-01 00:00:00"),
            ("inst-b", "READER", None, "2024-02-01 00:00:00"),
            ("inst-b", "ZERO", 0, "2024-02-01 00:00:00"),
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


def test_current_profile_summary_reads_base_tables_and_respects_filters(license_api_app):
    app, conn = license_api_app
    _create_current_base_tables(conn)
    _insert_current_base_rows(conn)

    response = app.test_client().get("/api/build/users/current-license-utilization?licenseFilter=license_creator")
    payload = response.get_json()

    assert response.status_code == 200, payload
    assert payload["meta"]["source"] == "base_users_instance_metadata + base_license_max_licenses_latest"
    assert payload["meta"]["profileField"] == "users_userprofile"
    rows = {row["license_profile"]: row for row in payload["profileRows"]}
    assert set(rows) == {"DESIGNER"}
    assert rows["DESIGNER"]["assigned_count"] == 2
    assert rows["DESIGNER"]["entitled_count"] == 20
    assert rows["DESIGNER"]["available_count"] == 18
    assert rows["DESIGNER"]["utilization_pct"] == 10.0
    assert {item["instance_name"] for item in rows["DESIGNER"]["instances"]} == {"inst-a", "inst-b"}


def test_current_instance_summary_is_base_detail_not_fact_latest_rows(license_api_app):
    app, conn = license_api_app
    _create_current_base_tables(conn)
    _insert_current_base_rows(conn)
    _create_fact(conn)
    conn.execute(
        "INSERT INTO fact_license_utilization_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("2024-01-01", "inst-a", "DESIGNER", 999, 999, 0, 100.0, "2024-01-01 00:00:00"),
    )

    response = app.test_client().get("/api/build/users/current-license-utilization?instance_name=inst-a")
    payload = response.get_json()

    assert response.status_code == 200, payload
    assert payload["instanceName"] == "inst-a"
    assert len(payload["instanceRows"]) == 1
    row = payload["instanceRows"][0]
    assert row["instance_name"] == "inst-a"
    assert row["assigned_count"] == 2
    assert row["entitled_count"] == 10
    assert row["available_count"] == 8
    assert row["utilization_pct"] == 20.0


def test_current_summary_preserves_kpi_response_contract(license_api_app):
    app, conn = license_api_app
    _create_current_base_tables(conn)
    _insert_current_base_rows(conn)
    conn.execute(
        """
        CREATE TABLE fact_user_activity_daily (
            day DATE,
            instance_name VARCHAR,
            login_norm VARCHAR,
            viewing_actions_count INTEGER,
            developing_actions_count INTEGER,
            last_activity_at DATE
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE base_license_status_latest (
            instance_name VARCHAR,
            run_ts TIMESTAMP,
            license_kind VARCHAR,
            has_license BOOLEAN,
            valid BOOLEAN,
            expired BOOLEAN,
            community BOOLEAN,
            fallback_profile VARCHAR,
            expires_on VARCHAR,
            licensee_company VARCHAR,
            licensee_name VARCHAR,
            standard_offer VARCHAR,
            emitted_by VARCHAR,
            emitted_on VARCHAR
        );
        """
    )

    response = app.test_client().get("/api/build/users/kpis?licenseFilter=license_creator")
    payload = response.get_json()

    assert response.status_code == 200, payload
    for key in ["kpis", "licenseStatusSummary", "byProfile", "byLicenseGroup", "byLicenseGroupProfiles", "byInstance"]:
        assert key in payload
    assert "profileRows" not in payload
    assert "instanceRows" not in payload


def test_historical_profile_filter_scopes_fact_series(license_api_app):
    app, conn = license_api_app
    _create_fact(conn)
    _insert_fact_rows(conn)

    response = app.test_client().get("/api/build/users/license-utilization?license_profile=READER")
    payload = response.get_json()

    assert response.status_code == 200, payload
    assert payload["licenseProfile"] == "READER"
    assert {row["license_profile"] for row in payload["historyRows"]} == {"READER"}
    assert {row["license_profile"] for row in payload["latestRows"]} == {"READER"}
