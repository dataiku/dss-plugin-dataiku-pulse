from __future__ import annotations

from types import SimpleNamespace

import duckdb
import pytest

from pulse_dashboard.pulse_duckdb.engine import init_db


def _create_table(conn: duckdb.DuckDBPyConnection, table_name: str) -> None:
    conn.execute(f'CREATE TABLE "{table_name}" (id INTEGER);')


def test_required_daily_facts_present_passes_validation():
    conn = duckdb.connect(database=":memory:")
    try:
        _create_table(conn, "fact_user_activity_daily")
        _create_table(conn, "fact_user_activity_project_daily")

        report = init_db._validate_required_dashboard_gold_tables(
            conn,
            allowed_names={"fact_user_activity_daily", "fact_user_activity_project_daily"},
            load_report={"ok": True, "loaded": [], "failed": []},
        )

        assert report == {"ok": True, "failed": []}
    finally:
        conn.close()


def test_missing_user_activity_source_fails_with_actionable_error():
    conn = duckdb.connect(database=":memory:")
    try:
        _create_table(conn, "fact_user_activity_project_daily")

        report = init_db._validate_required_dashboard_gold_tables(
            conn,
            allowed_names={"fact_user_activity_project_daily"},
            load_report={"ok": True, "loaded": [], "failed": []},
        )

        assert report["ok"] is False
        assert report["failed"] == [
            {
                "table": "fact_user_activity_daily",
                "reason": "no_eligible_gold_files_discovered",
                "error": "Required dashboard GOLD table fact_user_activity_daily has no eligible source files in the managed folder.",
            }
        ]
    finally:
        conn.close()


def test_user_activity_load_failure_preserves_original_table_error():
    conn = duckdb.connect(database=":memory:")
    try:
        _create_table(conn, "fact_user_activity_project_daily")

        report = init_db._validate_required_dashboard_gold_tables(
            conn,
            allowed_names={"fact_user_activity_daily", "fact_user_activity_project_daily"},
            load_report={
                "ok": False,
                "loaded": [],
                "failed": [
                    {
                        "table": "fact_user_activity_daily",
                        "path": "s3://bucket/gold/fact_user_activity_daily/year=2026/month=06/day=21/data.parquet",
                        "error": "DuckDB schema mismatch in glob",
                    }
                ],
            },
        )

        assert report["ok"] is False
        assert report["failed"] == [
            {
                "table": "fact_user_activity_daily",
                "reason": "table_load_failed",
                "error": "DuckDB schema mismatch in glob",
            }
        ]
    finally:
        conn.close()


@pytest.mark.parametrize("missing_table", [None, "fact_user_activity_daily"])
def test_ensure_database_ready_validates_required_daily_facts(monkeypatch, tmp_path, missing_table):
    from pulse_dashboard import settings
    from pulse_dashboard.pulse_duckdb.engine import gold_loader, view_builder

    db_path = tmp_path / "pulse.duckdb"
    metadata_path = db_path.with_suffix(f"{db_path.suffix}.meta.json")
    conn = duckdb.connect(database=":memory:")
    view_calls: list[str] = []
    metadata_writes: list[str] = []

    monkeypatch.setattr(settings, "PULSE_AUTO_LOAD_GOLD_TABLES", True, raising=False)
    monkeypatch.setattr(settings, "PULSE_AUTO_LOAD_REPLACE", True, raising=False)
    monkeypatch.setattr(settings, "PULSE_GOLD_LOAD_PREFIX", "", raising=False)
    monkeypatch.setattr(settings, "PULSE_GOLD_LOAD_NAME_GLOB", "*", raising=False)
    monkeypatch.setattr(settings, "PULSE_GOLD_TABLES_FOLDER_ID", "", raising=False)
    monkeypatch.setattr(settings, "PULSE_GOLD_TABLES_FOLDER_NAME", "gold_data", raising=False)
    monkeypatch.setattr(
        settings,
        "resolve_dashboard_duckdb_location",
        lambda: ("TEST_PROJECT", db_path, metadata_path),
        raising=False,
    )
    monkeypatch.setattr(init_db, "shared_prepare_duckdb", lambda **_kwargs: SimpleNamespace(conn=duckdb.connect(database=":memory:")))
    monkeypatch.setattr(init_db, "initialize_database", lambda: None)
    monkeypatch.setattr(init_db, "create_connection", lambda read_only=False: conn)
    monkeypatch.setattr(init_db, "_maybe_create_inventory_views", lambda _conn: None)
    monkeypatch.setattr(init_db, "_maybe_create_license_views", lambda _conn: {"ok": True, "created": []})
    monkeypatch.setattr(init_db, "_ensure_dev_activity_base_tables", lambda _conn: {"ok": True, "created": []})
    monkeypatch.setattr(init_db, "write_duckdb_metadata", lambda **kwargs: metadata_writes.append(kwargs["build_reason"]))
    monkeypatch.setattr(init_db, "_set_status_callback", lambda *_args: None)

    def _build_views(_conn):
        view_calls.append("build")
        return {"ok": True, "statements": 0, "skipped": [], "errors": []}

    def _list_gold_paths(*, suffixes):
        paths = ["gold/fact_user_activity_project_daily/year=2026/month=06/day=21/data.parquet"]
        if missing_table is None:
            paths.append("gold/fact_user_activity_daily/year=2026/month=06/day=21/data.parquet")
        return paths

    def _load_gold_tables(_conn, **_kwargs):
        _create_table(_conn, "fact_user_activity_project_daily")
        if missing_table is None:
            _create_table(_conn, "fact_user_activity_daily")
        return {"ok": True, "loaded": [], "skipped": [], "failed": []}

    monkeypatch.setattr(view_builder, "build_views_from_specs", _build_views)
    monkeypatch.setattr(gold_loader, "list_gold_paths", _list_gold_paths)
    monkeypatch.setattr(gold_loader, "infer_table_name", lambda path: path.split("/")[1])
    monkeypatch.setattr(gold_loader, "load_gold_tables", _load_gold_tables)

    result = init_db.ensure_database_ready(load_gold_tables=True, replace_gold_tables=True)

    if missing_table is None:
        assert result["ok"] is True
        assert result["required_gold_tables"] == {"ok": True, "failed": []}
        assert view_calls == ["build"]
        assert metadata_writes == ["None"]
    else:
        assert result["ok"] is False
        assert result["required_gold_tables"]["failed"] == [
            {
                "table": "fact_user_activity_daily",
                "reason": "no_eligible_gold_files_discovered",
                "error": "Required dashboard GOLD table fact_user_activity_daily has no eligible source files in the managed folder.",
            }
        ]
        assert metadata_writes == []
