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


def _create_user_segments_tables(conn):
    conn.execute(
        """
        CREATE TABLE base_users_instance_metadata (
            instance_name VARCHAR,
            users_login VARCHAR,
            users_displayname VARCHAR,
            users_email VARCHAR,
            users_enabled VARCHAR,
            users_userprofile VARCHAR,
            users_groups VARCHAR,
            run_ts TIMESTAMP
        );
        """
    )
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


def _insert_user_segments_rows(conn):
    user_rows = [
        ("inst-a", "viewer_a", "Viewer A", "viewer_a@example.com", "True", "DESIGNER", "grp", "2026-10-02 00:00:00"),
        ("inst-a", "excluded_viewer", "Excluded Viewer", "excluded_viewer@example.com", "True", "READER", "grp", "2026-10-02 00:00:00"),
        ("inst-a", "excluded_mixed", "Excluded Mixed", "excluded_mixed@example.com", "True", "AI_CONSUMER", "grp", "2026-10-02 00:00:00"),
        ("inst-a", "mixed_a", "Mixed A", "mixed_a@example.com", "True", "DESIGNER", "grp", "2026-10-02 00:00:00"),
        ("inst-a", "developer_a", "Developer A", "developer_a@example.com", "True", "SCIENTIST", "grp", "2026-10-02 00:00:00"),
        ("inst-a", "inactive_a", "Inactive A", "inactive_a@example.com", "True", "DESIGNER", "grp", "2026-10-02 00:00:00"),
        ("inst-b", "viewer_b", "Viewer B", "viewer_b@example.com", "True", "DESIGNER", "grp", "2026-10-02 00:00:00"),
        ("inst-b", "excluded_b", "Excluded B", "excluded_b@example.com", "True", "AI_CONSUMER", "grp", "2026-10-02 00:00:00"),
        ("inst-a", "disabled_a", "Disabled A", "disabled_a@example.com", "False", "DESIGNER", "grp", "2026-10-02 00:00:00"),
    ]
    conn.executemany(
        "INSERT INTO base_users_instance_metadata VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        user_rows,
    )

    activity_rows = [
        ("2026-10-01", "inst-a", "viewer_a", 5, 0, "2026-10-01"),
        ("2026-10-01", "inst-a", "excluded_viewer", 7, 0, "2026-10-01"),
        ("2026-10-01", "inst-a", "excluded_mixed", 2, 2, "2026-10-01"),
        ("2026-10-01", "inst-a", "mixed_a", 3, 1, "2026-10-01"),
        ("2026-10-01", "inst-a", "developer_a", 0, 4, "2026-10-01"),
        ("2026-10-01", "inst-b", "viewer_b", 2, 0, "2026-10-01"),
        ("2026-10-01", "inst-b", "excluded_b", 4, 0, "2026-10-01"),
        ("2026-10-01", "inst-a", "disabled_a", 5, 0, "2026-10-01"),
    ]
    conn.executemany(
        "INSERT INTO fact_user_activity_daily VALUES (?, ?, ?, ?, ?, ?)",
        activity_rows,
    )


def _segment_value(payload, label):
    return next(row["value"] for row in payload["segments"] if row["label"] == label)


def _rows_by_login(rows):
    return {row["login"]: row for row in rows}


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


def test_user_segments_consumer_filter_repeats_exclusion_params_and_counts_semantically(
    monkeypatch, license_api_app
):
    app, conn = license_api_app
    module = importlib.import_module("pulse_dashboard.webapp_backend.routes.build_users")
    monkeypatch.setattr(module, "_read_user_profile_exclude_consumer", lambda _standard: ["READER", "AI_CONSUMER"])
    _create_user_segments_tables(conn)
    _insert_user_segments_rows(conn)

    response = app.test_client().get("/api/build/users/segments?days=365&activityFilter=license_consumer")
    payload = response.get_json()

    assert response.status_code == 200, payload
    assert payload["activityFilter"] == "license_consumer"
    assert payload["totals"] == {
        "enabledUsers": 5,
        "viewerOnlyUsers": 2,
        "developerOnlyUsers": 1,
        "mixedUsers": 1,
        "inactiveUsers": 1,
        "viewerDominantUsers": 1,
        "developerDominantUsers": 0,
        "balancedMixedUsers": 0,
    }
    assert _segment_value(payload, "Viewer only") == 2
    assert _segment_value(payload, "Mixed") == 1
    assert _segment_value(payload, "Inactive") == 1


def test_user_segments_without_filter_preserves_existing_counts(monkeypatch, license_api_app):
    app, conn = license_api_app
    module = importlib.import_module("pulse_dashboard.webapp_backend.routes.build_users")
    monkeypatch.setattr(module, "_read_user_profile_exclude_consumer", lambda _standard: ["READER", "AI_CONSUMER"])
    _create_user_segments_tables(conn)
    _insert_user_segments_rows(conn)

    response = app.test_client().get("/api/build/users/segments?days=365")
    payload = response.get_json()

    assert response.status_code == 200, payload
    assert payload["activityFilter"] == "license_creator"
    assert payload["totals"] == {
        "enabledUsers": 8,
        "viewerOnlyUsers": 4,
        "developerOnlyUsers": 1,
        "mixedUsers": 2,
        "inactiveUsers": 1,
        "viewerDominantUsers": 1,
        "developerDominantUsers": 0,
        "balancedMixedUsers": 1,
    }


def test_user_segments_creator_filter_remains_successful(monkeypatch, license_api_app):
    app, conn = license_api_app
    module = importlib.import_module("pulse_dashboard.webapp_backend.routes.build_users")
    monkeypatch.setattr(module, "_read_user_profile_exclude_consumer", lambda _standard: ["READER", "AI_CONSUMER"])
    _create_user_segments_tables(conn)
    _insert_user_segments_rows(conn)

    response = app.test_client().get("/api/build/users/segments?days=365&activityFilter=license_creator")
    payload = response.get_json()

    assert response.status_code == 200, payload
    assert payload["activityFilter"] == "license_creator"
    assert payload["totals"]["enabledUsers"] == 8
    assert payload["totals"]["viewerOnlyUsers"] == 4


def test_user_segments_consumer_filter_preserves_instance_parameter_order(
    monkeypatch, license_api_app
):
    app, conn = license_api_app
    module = importlib.import_module("pulse_dashboard.webapp_backend.routes.build_users")
    monkeypatch.setattr(module, "_read_user_profile_exclude_consumer", lambda _standard: ["READER", "AI_CONSUMER"])
    _create_user_segments_tables(conn)
    _insert_user_segments_rows(conn)

    response = app.test_client().get(
        "/api/build/users/segments?days=365&activityFilter=license_consumer&instance_name=inst-a"
    )
    payload = response.get_json()

    assert response.status_code == 200, payload
    assert payload["instanceName"] == "inst-a"
    assert payload["totals"] == {
        "enabledUsers": 4,
        "viewerOnlyUsers": 1,
        "developerOnlyUsers": 1,
        "mixedUsers": 1,
        "inactiveUsers": 1,
        "viewerDominantUsers": 1,
        "developerDominantUsers": 0,
        "balancedMixedUsers": 0,
    }


def test_user_leaderboard_consumer_filter_uses_directory_profile_scope(
    monkeypatch, license_api_app
):
    app, conn = license_api_app
    module = importlib.import_module("pulse_dashboard.webapp_backend.routes.build_users")
    monkeypatch.setattr(module, "_read_user_profile_exclude_consumer", lambda _standard: ["READER", "AI_CONSUMER"])
    _create_user_segments_tables(conn)
    _insert_user_segments_rows(conn)

    response = app.test_client().get("/api/build/users/leaderboard?days=365&activityFilter=license_consumer")
    payload = response.get_json()

    assert response.status_code == 200, payload
    assert payload["activityFilter"] == "license_consumer"
    viewing_by_login = _rows_by_login(payload["viewing"])
    developing_by_login = _rows_by_login(payload["developing"])
    assert "excluded_viewer" not in viewing_by_login
    assert "excluded_mixed" not in viewing_by_login
    assert "excluded_mixed" not in developing_by_login
    assert viewing_by_login["viewer_a"]["value"] == 5
    assert viewing_by_login["viewer_a"]["userProfile"] == "DESIGNER"
    assert developing_by_login["mixed_a"]["value"] == 1
    assert developing_by_login["mixed_a"]["userProfile"] == "DESIGNER"


def test_user_leaderboard_default_and_creator_filters_preserve_existing_behavior(
    monkeypatch, license_api_app
):
    app, conn = license_api_app
    module = importlib.import_module("pulse_dashboard.webapp_backend.routes.build_users")
    monkeypatch.setattr(module, "_read_user_profile_exclude_consumer", lambda _standard: ["READER", "AI_CONSUMER"])
    _create_user_segments_tables(conn)
    _insert_user_segments_rows(conn)

    default_response = app.test_client().get("/api/build/users/leaderboard?days=365")
    default_payload = default_response.get_json()
    creator_response = app.test_client().get("/api/build/users/leaderboard?days=365&activityFilter=license_creator")
    creator_payload = creator_response.get_json()

    assert default_response.status_code == 200, default_payload
    assert creator_response.status_code == 200, creator_payload
    assert default_payload["activityFilter"] == "license_creator"
    assert creator_payload["activityFilter"] == "license_creator"
    default_viewing = _rows_by_login(default_payload["viewing"])
    default_developing = _rows_by_login(default_payload["developing"])
    creator_viewing = _rows_by_login(creator_payload["viewing"])
    creator_developing = _rows_by_login(creator_payload["developing"])
    assert "excluded_viewer" not in default_viewing
    assert "excluded_mixed" in default_developing
    assert default_developing["excluded_mixed"]["value"] == 2
    assert "excluded_mixed" in default_viewing
    assert default_viewing["excluded_mixed"]["value"] == 2
    assert creator_viewing == default_viewing
    assert creator_developing == default_developing


def test_user_leaderboard_consumer_filter_preserves_instance_parameter_order(
    monkeypatch, license_api_app
):
    app, conn = license_api_app
    module = importlib.import_module("pulse_dashboard.webapp_backend.routes.build_users")
    monkeypatch.setattr(module, "_read_user_profile_exclude_consumer", lambda _standard: ["READER", "AI_CONSUMER"])
    _create_user_segments_tables(conn)
    _insert_user_segments_rows(conn)

    response = app.test_client().get(
        "/api/build/users/leaderboard?days=365&activityFilter=license_consumer&instance_name=inst-a"
    )
    payload = response.get_json()

    assert response.status_code == 200, payload
    assert payload["instanceName"] == "inst-a"
    viewing_by_login = _rows_by_login(payload["viewing"])
    developing_by_login = _rows_by_login(payload["developing"])
    assert "excluded_viewer" not in viewing_by_login
    assert "excluded_mixed" not in viewing_by_login
    assert "viewer_a" in viewing_by_login
    assert "mixed_a" in viewing_by_login
    assert "excluded_mixed" not in developing_by_login
    assert "mixed_a" in developing_by_login
    assert viewing_by_login["viewer_a"]["instances"] == 1
    assert developing_by_login["mixed_a"]["instances"] == 1


def _create_adoption_tables(conn):
    conn.execute(
        """
        CREATE TABLE base_users_instance_metadata (
            instance_name VARCHAR,
            users_login VARCHAR,
            users_displayname VARCHAR,
            users_email VARCHAR,
            users_enabled VARCHAR,
            users_userprofile VARCHAR,
            users_groups VARCHAR,
            run_ts TIMESTAMP
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE fact_user_activity_daily (
            instance_name VARCHAR,
            login_norm VARCHAR,
            viewing_actions_count INTEGER,
            developing_actions_count INTEGER,
            last_activity_at TIMESTAMP
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE final_build_formal_mau_daily (
            instance_name VARCHAR,
            login_norm VARCHAR,
            application_open_count INTEGER,
            last_application_open_at TIMESTAMP
        );
        """
    )


def _insert_adoption_rows(conn):
    conn.executemany(
        "INSERT INTO base_users_instance_metadata VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("inst-a", "alice", "Alice", "alice@example.com", "True", "DESIGNER", None, "2026-10-21 00:00:00"),
            ("inst-a", "bob", "Bob", "bob@example.com", "True", "READER", None, "2026-10-21 00:00:00"),
            ("inst-a", "disabled", "Disabled", "disabled@example.com", "False", "DESIGNER", None, "2026-10-21 00:00:00"),
            ("inst-b", "alice", "Alice", "alice-b@example.com", "True", "DESIGNER", None, "2026-10-21 00:00:00"),
        ],
    )
    conn.executemany(
        "INSERT INTO fact_user_activity_daily VALUES (?, ?, ?, ?, ?)",
        [
            ("inst-a", "alice", 1, 0, "2026-10-19 09:00:00"),
            ("inst-a", "alice", 2, 0, "2026-10-20 09:00:00"),
            ("inst-b", "alice", 1, 0, "2026-10-20 10:00:00"),
            ("inst-a", "bob", 1, 0, "2026-10-01 09:00:00"),
            ("inst-a", "disabled", 1, 0, "2026-10-20 12:00:00"),
            ("inst-a", "alice", 1, 0, "2026-11-01 12:00:00"),
            ("inst-a", "alice", 1, 0, "2026-07-04 12:00:00"),
            ("inst-a", "alice", 1, 0, "2026-02-28 12:00:00"),
        ],
    )
    conn.executemany(
        "INSERT INTO final_build_formal_mau_daily VALUES (?, ?, ?, ?)",
        [
            ("inst-a", "alice", 1, "2026-10-19 11:00:00"),
            ("inst-a", "alice", 1, "2026-10-20 11:00:00"),
            ("inst-b", "alice", 1, "2026-10-20 11:00:00"),
            ("inst-a", "bob", 1, "2026-10-01 11:00:00"),
            ("inst-a", "alice", 1, "2026-07-04 11:00:00"),
            ("inst-a", "alice", 1, "2026-11-01 11:00:00"),
        ],
    )


def _set_adoption_reporting_date(monkeypatch, module, reporting_date: str):
    monkeypatch.setattr(module, "_ADOPTION_REPORTING_DATE_SQL", f"DATE '{reporting_date}'")


def test_users_adoption_trends_weekly_buckets_and_distinct_counts(monkeypatch, license_api_app):
    app, conn = license_api_app
    module = importlib.import_module("pulse_dashboard.webapp_backend.routes.build_users")
    _set_adoption_reporting_date(monkeypatch, module, "2026-10-21")
    monkeypatch.setattr(module, "_read_user_profile_exclude_consumer", lambda _standard: ["READER"])
    _create_adoption_tables(conn)
    _insert_adoption_rows(conn)
    conn.execute(
        "INSERT INTO fact_user_activity_daily VALUES (?, ?, ?, ?, ?)",
        ("inst-a", "alice", 1, 0, "2026-10-18 23:59:59"),
    )

    response = app.test_client().get("/api/build/users/adoption-trends?period=weekly")
    payload = response.get_json()

    assert response.status_code == 200, payload
    assert payload["period"] == "weekly"
    assert len(payload["periods"]) == 12
    assert payload["periods"][-1]["periodStart"] == "2026-10-19"
    assert payload["periods"][-1]["periodEnd"] == "2026-10-26"
    assert payload["periods"][-1]["isCurrentPeriod"] is True
    assert payload["periods"][-1]["isPartialPeriod"] is True
    assert payload["observed"]["aggregate"][-1]["active_users"] == 2
    assert payload["formal"]["aggregate"][-1]["active_users"] == 2
    by_start = {row["periodStart"]: row["active_users"] for row in payload["observed"]["aggregate"]}
    assert by_start["2026-10-12"] == 1
    assert payload["observed"]["history"]["lastObservation"].startswith("2026-10-20")


def test_users_adoption_trends_quarterly_counts_whole_period_distincts_and_current_breakdowns(
    monkeypatch, license_api_app
):
    app, conn = license_api_app
    module = importlib.import_module("pulse_dashboard.webapp_backend.routes.build_users")
    _set_adoption_reporting_date(monkeypatch, module, "2026-10-21")
    monkeypatch.setattr(module, "_read_user_profile_exclude_consumer", lambda _standard: ["READER"])
    _create_adoption_tables(conn)
    _insert_adoption_rows(conn)

    response = app.test_client().get("/api/build/users/adoption-trends?period=quarterly&activityFilter=license_creator")
    payload = response.get_json()

    assert response.status_code == 200, payload
    assert len(payload["periods"]) == 4
    assert [row["label"] for row in payload["periods"]] == ["Q1 2026", "Q2 2026", "Q3 2026", "Q4 2026"]
    by_label = {row["label"]: row["active_users"] for row in payload["observed"]["aggregate"]}
    assert by_label["Q3 2026"] == 1
    assert by_label["Q4 2026"] == 2
    assert payload["observed"]["currentPeriod"]["label"] == "Q4 2026"
    assert payload["formal"]["currentPeriod"]["active_users"] == 3
    profile_rows = {(row["license_group"], row["userProfile"]): row["active_users"] for row in payload["formal"]["byProfile"]}
    assert profile_rows[("Creator Licenses", "DESIGNER")] == 2
    assert profile_rows[("Consumer Licenses", "READER")] == 1


def test_users_adoption_trends_controlled_date_crosses_year_quarter_and_leap_day(
    monkeypatch, license_api_app
):
    app, conn = license_api_app
    module = importlib.import_module("pulse_dashboard.webapp_backend.routes.build_users")
    _set_adoption_reporting_date(monkeypatch, module, "2027-01-01")
    monkeypatch.setattr(module, "_read_user_profile_exclude_consumer", lambda _standard: ["READER"])
    _create_adoption_tables(conn)
    _insert_adoption_rows(conn)

    response = app.test_client().get("/api/build/users/adoption-trends?period=quarterly&activityFilter=license_creator")
    payload = response.get_json()

    assert response.status_code == 200, payload
    assert [row["label"] for row in payload["periods"]] == ["Q2 2026", "Q3 2026", "Q4 2026", "Q1 2027"]
    assert payload["periods"][-1]["isCurrentPeriod"] is True
    by_label = {row["label"]: row["active_users"] for row in payload["observed"]["aggregate"]}
    assert by_label["Q4 2026"] == 2
    assert by_label["Q1 2027"] == 0
    assert payload["formal"]["currentPeriod"]["active_users"] == 0

    _set_adoption_reporting_date(monkeypatch, module, "2024-03-01")
    conn.execute(
        "INSERT INTO fact_user_activity_daily VALUES (?, ?, ?, ?, ?)",
        ("inst-a", "alice", 1, 0, "2024-02-29 12:00:00"),
    )
    weekly_response = app.test_client().get("/api/build/users/adoption-trends?period=weekly&activityFilter=license_creator")
    weekly_payload = weekly_response.get_json()

    assert weekly_response.status_code == 200, weekly_payload
    assert weekly_payload["periods"][-1]["periodStart"] == "2024-02-26"
    assert weekly_payload["periods"][-1]["periodEnd"] == "2024-03-04"
    assert weekly_payload["observed"]["aggregate"][-1]["active_users"] == 1


def test_users_adoption_trends_monthly_calendar_and_formal_missing(monkeypatch, license_api_app):
    app, conn = license_api_app
    module = importlib.import_module("pulse_dashboard.webapp_backend.routes.build_users")
    _set_adoption_reporting_date(monkeypatch, module, "2026-10-21")
    monkeypatch.setattr(module, "_read_user_profile_exclude_consumer", lambda _standard: ["READER"])
    conn.execute(
        """
        CREATE TABLE base_users_instance_metadata (
            instance_name VARCHAR,
            users_login VARCHAR,
            users_displayname VARCHAR,
            users_email VARCHAR,
            users_enabled VARCHAR,
            users_userprofile VARCHAR,
            users_groups VARCHAR,
            run_ts TIMESTAMP
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE fact_user_activity_daily (
            instance_name VARCHAR,
            login_norm VARCHAR,
            viewing_actions_count INTEGER,
            developing_actions_count INTEGER,
            last_activity_at TIMESTAMP
        );
        """
    )

    response = app.test_client().get("/api/build/users/adoption-trends?period=monthly")
    payload = response.get_json()

    assert response.status_code == 200, payload
    assert [row["periodStart"] for row in payload["periods"]] == ["2026-08-01", "2026-09-01", "2026-10-01"]
    assert payload["formal"]["available"] is False
    assert payload["formal"]["reason"] == "formal_mau_view_missing"


def test_users_adoption_trends_rejects_invalid_period(license_api_app):
    app, conn = license_api_app
    _create_adoption_tables(conn)
    response = app.test_client().get("/api/build/users/adoption-trends?period=rolling")
    payload = response.get_json()

    assert response.status_code == 400
    assert payload["ok"] is False

def _create_user_directory_tables(conn):
    conn.execute(
        """
        CREATE TABLE base_users_instance_metadata (
            instance_name VARCHAR,
            users_login VARCHAR,
            users_displayname VARCHAR,
            users_email VARCHAR,
            users_enabled VARCHAR,
            users_userprofile VARCHAR,
            users_resultinguserprofile VARCHAR,
            users_lastsessionactivity TIMESTAMP,
            run_ts TIMESTAMP,
            partition_date DATE
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE fact_user_activity_daily (
            instance_name VARCHAR,
            login_norm VARCHAR,
            last_activity_at TIMESTAMP,
            viewing_actions_count INTEGER,
            developing_actions_count INTEGER
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE fact_user_activity_project_daily (
            instance_name VARCHAR,
            login_norm VARCHAR,
            project_key VARCHAR,
            day DATE,
            last_activity_at TIMESTAMP,
            viewing_actions_count INTEGER,
            developing_actions_count INTEGER
        );
        """
    )


def _insert_user_directory_rows(conn):
    conn.executemany(
        "INSERT INTO base_users_instance_metadata VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("inst-a", " Alice ", "Old Alice", "old@example.com", "True", "READER", None, "2026-08-01", "2026-08-01", "2026-08-01"),
            ("inst-a", "Alice", "Alice A", "alice@example.com", "True", "DESIGNER", None, "2026-10-01", "2026-10-01", "2026-10-01"),
            ("inst-b", "ALICE", "Alice B", "alice-b@example.com", "False", "READER", None, "2026-10-02", "2026-10-02", "2026-10-02"),
            ("inst-a", "bob", "Bob Builder", "bob@example.com", "False", "READER", None, None, "2026-10-01", "2026-10-01"),
            ("inst-a", "carol", "Carol Creator", "carol@example.com", "True", "DESIGNER", None, None, "2026-10-01", "2026-10-01"),
            ("inst-b", "dana", "Dana Unknown", "dana@example.com", None, None, None, None, "2026-10-01", "2026-10-01"),
            ("inst-a", "erin", "Erin Empty", "erin@example.com", "True", "DESIGNER", "", None, "2026-10-01", "2026-10-01"),
            ("inst-a", "frank", "Frank Space", "frank@example.com", "True", "DESIGNER", "   ", None, "2026-10-01", "2026-10-01"),
            ("inst-a", "grace", "Grace Override", "grace@example.com", "True", "DESIGNER", "READER", None, "2026-10-01", "2026-10-01"),
            ("inst-a", "hank", "Hank Unknown", "hank@example.com", "True", None, None, None, "2026-10-01", "2026-10-01"),
            ("inst-b", "   ", "Blank", "blank@example.com", "True", "DESIGNER", None, None, "2026-10-01", "2026-10-01"),
        ],
    )
    conn.executemany(
        "INSERT INTO fact_user_activity_daily VALUES (?, ?, ?, ?, ?)",
        [
            ("inst-a", "alice", "2026-10-05", 3, 1),
            ("inst-b", "alice", "2026-10-07", 2, 0),
            ("inst-b", "activity_only", "2026-10-09", 7, 0),
            ("inst-a", "carol", "2026-10-03", 0, 5),
        ],
    )


def test_user_directory_lists_metadata_users_with_filters_search_and_activity(monkeypatch, license_api_app):
    app, conn = license_api_app
    module = importlib.import_module("pulse_dashboard.webapp_backend.routes.build_users")
    monkeypatch.setattr(module, "_require_organization_access", lambda: None)
    _create_user_directory_tables(conn)
    _insert_user_directory_rows(conn)

    response = app.test_client().get("/api/build/users/directory?limit=10&sort=display_name_asc")
    payload = response.get_json()

    assert response.status_code == 200, payload
    assert payload["total"] == 8
    rows_by_login = {row["login"]: row for row in payload["rows"]}
    assert set(rows_by_login) == {"alice", "bob", "carol", "dana", "erin", "frank", "grace", "hank"}
    assert rows_by_login["alice"]["instanceCount"] == 2
    assert rows_by_login["alice"]["accountStatus"] == "Mixed"
    assert rows_by_login["alice"]["latestActivityAt"].startswith("2026-10-07")
    assert rows_by_login["bob"]["latestActivityAt"] is None
    assert rows_by_login["bob"]["accountStatus"] == "Disabled"
    assert rows_by_login["dana"]["accountStatus"] == "Unknown"
    assert rows_by_login["erin"]["licenseProfiles"] == "DESIGNER"
    assert rows_by_login["frank"]["licenseProfiles"] == "DESIGNER"
    assert rows_by_login["grace"]["licenseProfiles"] == "READER"
    assert rows_by_login["hank"]["licenseProfiles"] == "UNKNOWN"

    disabled_response = app.test_client().get("/api/build/users/directory?account_status=disabled&license_group=Consumer%20Licenses")
    disabled_payload = disabled_response.get_json()
    assert disabled_response.status_code == 200, disabled_payload
    assert [row["login"] for row in disabled_payload["rows"]] == ["alice", "bob"]

    creator_response = app.test_client().get("/api/build/users/directory?license_group=Creator%20Licenses&limit=20")
    creator_payload = creator_response.get_json()
    assert creator_response.status_code == 200, creator_payload
    creator_logins = {row["login"] for row in creator_payload["rows"]}
    assert {"erin", "frank"}.issubset(creator_logins)
    assert "grace" not in creator_logins

    profile_response = app.test_client().get("/api/build/users/directory?license_profile=DESIGNER&limit=20")
    profile_payload = profile_response.get_json()
    assert profile_response.status_code == 200, profile_payload
    profile_logins = {row["login"] for row in profile_payload["rows"]}
    assert {"erin", "frank"}.issubset(profile_logins)
    assert "grace" not in profile_logins

    facets_response = app.test_client().get("/api/build/users/directory/facets")
    facets_payload = facets_response.get_json()
    assert facets_response.status_code == 200, facets_payload
    assert {row["value"] for row in facets_payload["licenseProfiles"]} >= {"DESIGNER", "READER", "UNKNOWN"}

    services = importlib.import_module("pulse_dashboard.webapp_backend.services.users")
    detail_record = services._normalize_user_directory_record({"users_resultinguserprofile": "", "users_userprofile": "DESIGNER"})
    assert detail_record["user_profile"] == rows_by_login["erin"]["licenseProfiles"]

    instance_response = app.test_client().get("/api/build/users/directory?instance_name=inst-a&account_status=disabled&license_group=Consumer%20Licenses")
    instance_payload = instance_response.get_json()
    assert instance_response.status_code == 200, instance_payload
    assert [row["login"] for row in instance_payload["rows"]] == ["bob"]

    search_response = app.test_client().get("/api/build/users/directory?q=alice-b")
    search_payload = search_response.get_json()
    assert search_response.status_code == 200, search_payload
    assert [row["login"] for row in search_payload["rows"]] == ["alice"]


def test_user_directory_paginates_sorts_validates_and_requires_org(monkeypatch, license_api_app):
    app, conn = license_api_app
    module = importlib.import_module("pulse_dashboard.webapp_backend.routes.build_users")
    _create_user_directory_tables(conn)
    _insert_user_directory_rows(conn)

    monkeypatch.setattr(module, "_require_organization_access", lambda: None)
    page_response = app.test_client().get("/api/build/users/directory?limit=2&offset=1&sort=login_desc")
    page_payload = page_response.get_json()
    assert page_response.status_code == 200, page_payload
    assert page_payload["total"] == 8
    assert [row["login"] for row in page_payload["rows"]] == ["grace", "frank"]

    bad_response = app.test_client().get("/api/build/users/directory?sort=bad_sql")
    assert bad_response.status_code == 400

    monkeypatch.setattr(module, "_require_organization_access", lambda: (_ for _ in ()).throw(PermissionError("Organization access is required.")))
    denied_response = app.test_client().get("/api/build/users/directory")
    assert denied_response.status_code == 403
    denied_facets_response = app.test_client().get("/api/build/users/directory/facets")
    assert denied_facets_response.status_code == 403


def _install_real_directory_guard(monkeypatch, module, *, standard_vars, auth_groups, local_dev=False):
    support = importlib.import_module("pulse_dashboard.webapp_backend.support")
    monkeypatch.setattr("pulse_dashboard.webapp_backend.full_backend.is_local_dev", lambda: local_dev)
    monkeypatch.setattr(support, "_current_user_auth_info", lambda: None if auth_groups is None else {"groups": auth_groups})
    monkeypatch.setattr("pulse_dashboard.webapp_backend.services.users._read_standard_project_variables", lambda: standard_vars)
    monkeypatch.setattr(module, "_require_organization_access", support._require_organization_access)


def test_user_directory_real_guard_allows_admin_without_organization_owner(monkeypatch, license_api_app):
    app, conn = license_api_app
    module = importlib.import_module("pulse_dashboard.webapp_backend.routes.build_users")
    _create_user_directory_tables(conn)
    _insert_user_directory_rows(conn)
    _install_real_directory_guard(
        monkeypatch,
        module,
        standard_vars={"administration_owner": {"value": "admins"}},
        auth_groups=["admins"],
    )

    response = app.test_client().get("/api/build/users/directory?limit=1")

    assert response.status_code == 200, response.get_json()


def test_user_directory_real_guard_allows_configured_organization_member(monkeypatch, license_api_app):
    app, conn = license_api_app
    module = importlib.import_module("pulse_dashboard.webapp_backend.routes.build_users")
    _create_user_directory_tables(conn)
    _insert_user_directory_rows(conn)
    _install_real_directory_guard(
        monkeypatch,
        module,
        standard_vars={"organization_owner": {"value": "org-users"}, "administration_owner": {"value": "admins"}},
        auth_groups=["org-users"],
    )

    response = app.test_client().get("/api/build/users/directory/facets")

    assert response.status_code == 200, response.get_json()


@pytest.mark.parametrize("auth_groups", [[], None, ["other"]])
def test_user_directory_real_guard_denies_without_org_or_admin_before_db(monkeypatch, auth_groups):
    module = importlib.import_module("pulse_dashboard.webapp_backend.routes.build_users")
    module = importlib.reload(module)
    _install_real_directory_guard(
        monkeypatch,
        module,
        standard_vars={"organization_owner": {"value": "org-users"}, "administration_owner": {"value": "admins"}},
        auth_groups=auth_groups,
    )
    monkeypatch.setattr(
        module,
        "_require_duckdb_engine",
        lambda: (_ for _ in ()).throw(AssertionError("database must not be accessed before authorization")),
    )
    monkeypatch.setattr(module, "_ensure_ready_if_enabled", lambda: None)
    app = Flask(__name__)
    module.register_routes(app)

    response = app.test_client().get("/api/build/users/directory")
    facets_response = app.test_client().get("/api/build/users/directory/facets")

    assert response.status_code == 403
    assert facets_response.status_code == 403
