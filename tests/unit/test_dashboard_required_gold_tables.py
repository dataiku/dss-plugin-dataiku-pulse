from __future__ import annotations

from types import SimpleNamespace

import duckdb
import pytest

from pulse_dashboard.pulse_duckdb.engine import init_db
from pulse_dashboard.pulse_duckdb.engine.view_builder import build_views_from_specs


def _create_table(conn: duckdb.DuckDBPyConnection, table_name: str) -> None:
    conn.execute(f'CREATE TABLE "{table_name}" (id INTEGER);')


def _create_required_tables(conn: duckdb.DuckDBPyConnection, *, exclude: set[str] | None = None) -> None:
    exclude = exclude or set()
    for table_name in init_db._REQUIRED_DASHBOARD_GOLD_TABLES - exclude:
        _create_table(conn, table_name)


def test_required_daily_facts_present_passes_validation():
    conn = duckdb.connect(database=":memory:")
    try:
        _create_required_tables(conn)

        report = init_db._validate_required_dashboard_gold_tables(
            conn,
            allowed_names=set(init_db._REQUIRED_DASHBOARD_GOLD_TABLES),
            load_report={"ok": True, "loaded": [], "failed": []},
        )

        assert report == {"ok": True, "failed": []}
    finally:
        conn.close()


@pytest.mark.parametrize(
    "missing_table",
    [
        "base_users_instance_metadata",
        "fact_user_activity_daily",
        "base_license_status_latest",
    ],
)
def test_missing_required_contract_fails_with_actionable_error(missing_table):
    conn = duckdb.connect(database=":memory:")
    try:
        _create_required_tables(conn, exclude={missing_table})

        report = init_db._validate_required_dashboard_gold_tables(
            conn,
            allowed_names=set(init_db._REQUIRED_DASHBOARD_GOLD_TABLES) - {missing_table},
            load_report={"ok": True, "loaded": [], "failed": []},
        )

        assert report["ok"] is False
        assert report["failed"] == [
            {
                "table": missing_table,
                "reason": "no_eligible_gold_files_discovered",
                "error": f"Required dashboard GOLD table {missing_table} has no eligible source files in the managed folder.",
            }
        ]
    finally:
        conn.close()


def test_user_activity_load_failure_preserves_original_table_error():
    conn = duckdb.connect(database=":memory:")
    try:
        _create_required_tables(conn, exclude={"fact_user_activity_daily"})

        report = init_db._validate_required_dashboard_gold_tables(
            conn,
            allowed_names=set(init_db._REQUIRED_DASHBOARD_GOLD_TABLES),
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


def test_optional_rag_source_absent_creates_empty_target_and_warning():
    conn = duckdb.connect(database=":memory:")
    try:
        report = init_db._maybe_create_inventory_views(
            conn,
            allowed_names=set(),
            load_report={"ok": True, "loaded": [], "failed": []},
        )

        warning = next(
            entry
            for entry in report["optional_absent"]
            if entry["source"] == "base_retrieval_augmented_llms_project_metadata"
        )
        assert warning["target"] == "base_retrieval_augmented_llms_metadata"
        assert warning["reason"] == "optional_source_absent"
        assert conn.execute('SELECT COUNT(*) FROM "base_retrieval_augmented_llms_metadata";').fetchone() == (0,)
        assert [
            row[1]
            for row in conn.execute("PRAGMA table_info('base_retrieval_augmented_llms_metadata')").fetchall()
        ] == [
            "instance_name",
            "project_key",
            "retrieval_augmented_llms_id",
            "retrieval_augmented_llms_activeversion",
            "owner_login",
            "last_modified_by_login",
            "created_at",
            "updated_at",
            "details_json",
        ]
    finally:
        conn.close()


def test_optional_absence_mechanism_applies_to_other_inventory_sources():
    conn = duckdb.connect(database=":memory:")
    try:
        report = init_db._maybe_create_inventory_views(
            conn,
            allowed_names=set(),
            load_report={"ok": True, "loaded": [], "failed": []},
        )

        warning = next(entry for entry in report["optional_absent"] if entry["source"] == "base_webapps_project_metadata")
        assert warning["target"] == "base_webapps_metadata"
        assert conn.execute('SELECT COUNT(*) FROM "base_webapps_metadata";').fetchone() == (0,)
    finally:
        conn.close()


def test_optional_source_present_preserves_normal_derived_output():
    conn = duckdb.connect(database=":memory:")
    try:
        conn.execute(
            """
            CREATE TABLE base_retrieval_augmented_llms_project_metadata AS
            SELECT
              'dss-prod' AS instance_name,
              'FIN' AS project_key,
              'rag-1' AS retrieval_augmented_llms_id,
              'v1' AS retrieval_augmented_llms_activeversion,
              '{"k":"v"}' AS extras,
              TIMESTAMP '2026-10-07 12:00:00' AS run_ts,
              DATE '2026-10-07' AS partition_date
            UNION ALL
            SELECT
              'dss-prod' AS instance_name,
              'FIN' AS project_key,
              'rag-1' AS retrieval_augmented_llms_id,
              'v0' AS retrieval_augmented_llms_activeversion,
              '{}' AS extras,
              TIMESTAMP '2026-10-06 12:00:00' AS run_ts,
              DATE '2026-10-06' AS partition_date;
            """
        )

        report = init_db._maybe_create_inventory_views(conn, allowed_names={"base_retrieval_augmented_llms_project_metadata"})

        assert not [
            entry
            for entry in report["optional_absent"]
            if entry["source"] == "base_retrieval_augmented_llms_project_metadata"
        ]
        rows = conn.execute(
            """
            SELECT instance_name, project_key, retrieval_augmented_llms_id, retrieval_augmented_llms_activeversion, details_json
            FROM base_retrieval_augmented_llms_metadata;
            """
        ).fetchall()
        assert rows == [("dss-prod", "FIN", "rag-1", "v1", '{"k":"v"}')]
    finally:
        conn.close()


@pytest.mark.parametrize(
    "kwargs, expected_message",
    [
        (
            {
                "allowed_names": {"base_retrieval_augmented_llms_project_metadata"},
                "load_report": {"ok": True, "loaded": [], "failed": []},
            },
            "was discovered but was not created",
        ),
        (
            {
                "allowed_names": {"base_retrieval_augmented_llms_project_metadata"},
                "load_report": {
                    "ok": False,
                    "loaded": [],
                    "failed": [
                        {
                            "table": "base_retrieval_augmented_llms_project_metadata",
                            "path": "s3://bucket/gold/base_retrieval_augmented_llms_project_metadata/data.parquet",
                            "error": "DuckDB schema mismatch",
                        }
                    ],
                },
            },
            "was discovered but failed to load: DuckDB schema mismatch",
        ),
    ],
)
def test_optional_source_discovered_but_broken_is_failure(kwargs, expected_message):
    conn = duckdb.connect(database=":memory:")
    try:
        with pytest.raises(RuntimeError, match=expected_message):
            init_db._maybe_create_inventory_views(conn, **kwargs)
        assert init_db._object_type(conn, "base_retrieval_augmented_llms_metadata") is None
    finally:
        conn.close()


def test_empty_optional_targets_allow_product_catalog_schema_to_build():
    conn = duckdb.connect(database=":memory:")
    try:
        conn.execute(
            """
            CREATE TABLE base_projects_instance_metadata AS
            SELECT
              'dss-prod' AS instance_name,
              'FIN' AS project_key,
              'Finance' AS projects_name,
              'alice' AS projects_ownerlogin,
              'Alice' AS projects_ownerdisplayname,
              'alice' AS projects_creationtag_lastmodifiedby_login,
              'alice' AS projects_versiontag_lastmodifiedby_login,
              TIMESTAMP '2026-10-01' AS projects_creationtag_lastmodifiedon,
              TIMESTAMP '2026-10-02' AS projects_versiontag_lastmodifiedon,
              'NORMAL' AS projects_projecttype,
              NULL::VARCHAR AS projects_projectapptype,
              FALSE AS projects_tutorialproject,
              'AUTO' AS projects_commitmode;
            """
        )
        init_db._maybe_create_inventory_views(conn, allowed_names=set())
        init_db._ensure_dev_activity_base_tables(conn)
        conn.execute(
            """
            CREATE TABLE base_dataiku_products_registry AS
            SELECT * FROM (VALUES
              ('retrieval_augmented_llm', 'base_retrieval_augmented_llms_metadata', 'instance_name', 'project_key', 'retrieval_augmented_llms_id', 'retrieval_augmented_llms_id', 'retrieval_augmented_llms_activeversion', 'owner_login', 'last_modified_by_login', 'created_at', 'updated_at', NULL),
              ('web_application', 'base_webapps_metadata', 'instance_name', 'project_key', 'webapp_id', 'webapp_name', 'webapp_type', 'owner_login', 'last_modified_by_login', 'created_at', 'updated_at', NULL)
            ) AS t(product_type, source_table, instance_name_col, project_key_col, key_col, name_col, subtype_col, owner_col, last_modified_by_col, created_at_col, updated_at_col, where_sql);
            """
        )

        views_report = build_views_from_specs(conn)

        assert not [error for error in views_report["errors"] if "final_build_products_catalog" in error["spec"]]
        assert init_db._object_type(conn, "final_build_products_catalog") == "VIEW"
        assert conn.execute("SELECT COUNT(*) FROM final_build_products_catalog;").fetchone() == (0,)
        assert [
            row[1]
            for row in conn.execute("PRAGMA table_info('final_build_products_catalog')").fetchall()
        ] == [
            "product_id",
            "instance_name",
            "project_key",
            "product_type",
            "product_key",
            "product_name",
            "product_subtype",
            "owner_login",
            "last_modified_by_login",
            "created_at",
            "updated_at",
            "activity_30d",
            "active_users_30d",
            "last_activity_at",
            "project_name",
        ]
    finally:
        conn.close()


@pytest.mark.parametrize("missing_table", [None, "fact_user_activity_daily"])
def test_ensure_database_ready_validates_required_contracts(monkeypatch, tmp_path, missing_table):
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
    monkeypatch.setattr(settings, "PULSE_DUCKDB_INIT_LOCK_PATH", str(tmp_path / ".duckdb_init.lock"), raising=False)
    monkeypatch.setattr(
        settings,
        "resolve_dashboard_duckdb_location",
        lambda: ("TEST_PROJECT", db_path, metadata_path),
        raising=False,
    )
    monkeypatch.setattr(init_db, "shared_prepare_duckdb", lambda **_kwargs: SimpleNamespace(conn=duckdb.connect(database=":memory:")))
    monkeypatch.setattr(init_db, "initialize_database", lambda: None)
    monkeypatch.setattr(init_db, "create_connection", lambda read_only=False: conn)
    monkeypatch.setattr(init_db, "_maybe_create_inventory_views", lambda _conn, **_kwargs: {"ok": True, "created": [], "optional_absent": []})
    monkeypatch.setattr(init_db, "_maybe_create_license_views", lambda _conn: {"ok": True, "created": []})
    monkeypatch.setattr(init_db, "_ensure_dev_activity_base_tables", lambda _conn: {"ok": True, "created": []})
    monkeypatch.setattr(init_db, "write_duckdb_metadata", lambda **kwargs: metadata_writes.append(kwargs["build_reason"]))
    monkeypatch.setattr(init_db, "_set_status_callback", lambda *_args: None)

    def _build_views(_conn):
        view_calls.append("build")
        return {"ok": True, "statements": 0, "skipped": [], "errors": []}

    loaded_tables = set(init_db._REQUIRED_DASHBOARD_GOLD_TABLES)
    if missing_table is not None:
        loaded_tables.remove(missing_table)

    def _list_gold_paths(*, suffixes):
        return [f"gold/{table_name}/year=2026/month=06/day=21/data.parquet" for table_name in sorted(loaded_tables)]

    def _load_gold_tables(_conn, **_kwargs):
        for table_name in sorted(loaded_tables):
            _create_table(_conn, table_name)
        return {"ok": True, "loaded": [], "skipped": [], "failed": []}

    monkeypatch.setattr(view_builder, "build_views_from_specs", _build_views)
    monkeypatch.setattr(gold_loader, "list_gold_paths", _list_gold_paths)
    monkeypatch.setattr(gold_loader, "infer_table_name", lambda path: path.split("/")[1])
    monkeypatch.setattr(gold_loader, "load_gold_tables", _load_gold_tables)

    result = init_db.ensure_database_ready(load_gold_tables=True, replace_gold_tables=True)

    if missing_table is None:
        assert result["ok"] is True
        assert result["required_gold_tables"] == {"ok": True, "failed": []}
        assert result["inventory_views"] == {"ok": True, "created": [], "optional_absent": []}
        assert view_calls == ["build"]
        assert metadata_writes == ["None"]
    else:
        assert result["ok"] is False
        assert result["required_gold_tables"]["failed"] == [
            {
                "table": missing_table,
                "reason": "no_eligible_gold_files_discovered",
                "error": f"Required dashboard GOLD table {missing_table} has no eligible source files in the managed folder.",
            }
        ]
        assert metadata_writes == []
