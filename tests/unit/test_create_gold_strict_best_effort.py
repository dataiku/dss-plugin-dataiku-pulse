from __future__ import annotations

import copy
import importlib.util
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_collection.pulse_duckdb import unload
from data_collection.pulse_duckdb.views import create_silver_view
from data_collection.pulse_duckdb.object_activity import (
    _create_event_mapping_module_view,
)

RECIPE_PATH = Path("custom-recipes/create-gold-tables/recipe.py")


def _load_recipe_module():
    sys.path.insert(0, str(Path("tests/stubs").resolve()))
    sys.path.insert(0, str(Path("python-lib").resolve()))
    spec = importlib.util.spec_from_file_location(
        "create_gold_recipe_best_effort_test_module", RECIPE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class _Spec:
    name: str
    depends_on: list[str]
    category: str | None = None
    module: str | None = None
    view_table_name: str | None = None


class _Result:
    def __init__(self, value=None):
        self.value = value

    def fetchone(self):
        return (self.value,)

    def fetchall(self):
        return []


class _Conn:
    def execute(self, sql, *_args, **_kwargs):
        if sql == "SELECT current_setting('memory_limit')":
            return _Result("4.0 GiB")
        if sql == "SELECT current_setting('threads')":
            return _Result(2)
        return _Result()


class _Ctx:
    project_key = "TEST_PROJECT"
    folder_lookup = "stub"
    connection_type = "EC2"
    connection_name = "stub-conn"
    bucket_or_container = "bucket"
    blob_header = "s3"
    folder_root = "root"


class _RecordingConn:
    def __init__(self):
        self.sql: list[str] = []

    def execute(self, sql, *_args, **_kwargs):
        self.sql.append(sql)
        return _Result()


@pytest.fixture()
def recipe_module():
    return _load_recipe_module()


@contextmanager
def _record_timed_phase(_conn, *, label: str):
    yield


def _install_common_recipe_stubs(
    monkeypatch, recipe_module, *, events: list[str]
) -> None:
    class _Setup:
        conn = _Conn()

    monkeypatch.setattr(
        recipe_module.dataiku, "default_project_key", lambda: "TEST_PROJECT"
    )
    monkeypatch.setattr(
        recipe_module, "resolve_gold_folder_lookup", lambda: "gold_data"
    )
    monkeypatch.setattr(
        recipe_module,
        "get_recipe_config",
        lambda: {
            "unload_behavior": "dataiku",
            "incremental_enabled": True,
            "lookback_days": 3,
        },
    )
    monkeypatch.setattr(recipe_module, "ensure_managed_folder", lambda **_kwargs: None)
    monkeypatch.setattr(
        recipe_module, "build_storage_context", lambda **_kwargs: _Ctx()
    )
    monkeypatch.setattr(recipe_module, "prepare_duckdb", lambda **_kwargs: _Setup())
    monkeypatch.setattr(
        recipe_module,
        "effective_memory_limit_bytes",
        lambda: (10 * 1024**3, "cgroup_v2"),
    )
    monkeypatch.setattr(
        recipe_module, "configure_storage", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        recipe_module, "resolve_unique_db_path", lambda **_kwargs: "/tmp/test.duckdb"
    )
    monkeypatch.setattr(
        recipe_module, "build_license_wide_sql_params", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        recipe_module,
        "build_dim_addon_feature_flags",
        lambda *_args, **_kwargs: events.append("dim_addon_feature_flags")
        or "dim_addon_feature_flags",
    )
    monkeypatch.setattr(
        recipe_module,
        "build_dim_category_to_capability",
        lambda *_args, **_kwargs: events.append("dim_category_to_capability")
        or "dim_category_to_capability",
    )
    monkeypatch.setattr(
        recipe_module,
        "build_dim_dev_activity_event_classification",
        lambda *_args, **_kwargs: events.append("dim_dev_activity_event_classification")
        or "dim_dev_activity_event_classification",
    )
    monkeypatch.setattr(
        recipe_module,
        "build_fact_dev_activity_events",
        lambda *_args, **_kwargs: events.append("fact_dev_activity_events")
        or "fact_dev_activity_events",
    )
    monkeypatch.setattr(
        recipe_module,
        "build_fact_user_activity_daily",
        lambda *_args, **_kwargs: events.append("fact_user_activity_daily")
        or "fact_user_activity_daily",
    )
    monkeypatch.setattr(
        recipe_module,
        "build_fact_user_activity_project_daily",
        lambda *_args, **_kwargs: events.append("fact_user_activity_project_daily")
        or "fact_user_activity_project_daily",
    )
    monkeypatch.setattr(
        recipe_module,
        "build_fact_formal_mau_daily",
        lambda *_args, **_kwargs: events.append("fact_formal_mau_daily")
        or "fact_formal_mau_daily",
    )
    monkeypatch.setattr(
        recipe_module,
        "build_fact_license_utilization_daily",
        lambda *_args, **_kwargs: events.append("fact_license_utilization_daily")
        or "fact_license_utilization_daily",
    )
    monkeypatch.setattr(
        recipe_module,
        "collect_user_activity_quality_report",
        lambda *_args, **_kwargs: {"ok": True},
    )
    monkeypatch.setattr(
        recipe_module,
        "collect_license_utilization_quality_report",
        lambda *_args, **_kwargs: {"ok": True},
    )
    monkeypatch.setattr(
        recipe_module,
        "build_fact_object_activity_events",
        lambda *_args, **_kwargs: events.append("fact_object_activity_events")
        or "fact_object_activity_events",
    )
    monkeypatch.setattr(
        recipe_module,
        "build_base_dataiku_products_registry",
        lambda *_args, **_kwargs: events.append("base_dataiku_products_registry")
        or "base_dataiku_products_registry",
    )
    monkeypatch.setattr(recipe_module, "log_timed_phase", _record_timed_phase)
    monkeypatch.setattr(recipe_module, "read_manifest", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        recipe_module,
        "stamp_manifest_updated_at",
        lambda *_args, **_kwargs: events.append("stamp_manifest"),
    )
    monkeypatch.setattr(
        recipe_module,
        "write_manifest",
        lambda *_args, **_kwargs: events.append("write_manifest"),
    )
    monkeypatch.setattr(
        recipe_module,
        "list_table_names",
        lambda *_args, **_kwargs: [
            "fact_user_activity_daily",
            "fact_user_activity_project_daily",
        ],
    )
    monkeypatch.setattr(
        recipe_module,
        "group_gold_tables_by_prefix",
        lambda names: {
            "base_tables": [],
            "dim_tables": [],
            "agg_tables": [],
            "fact_tables": list(names),
        },
    )
    monkeypatch.setattr(
        recipe_module, "gold_destination_for_table", lambda name: f"gold/{name}"
    )
    monkeypatch.setattr(
        recipe_module, "log_pre_unload_debug", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        recipe_module, "load_dev_toolbox_modules", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        recipe_module, "load_object_activity_modules", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        recipe_module,
        "_create_event_mapping_module_view",
        lambda *_args, **_kwargs: None,
    )


def test_create_gold_silver_read_sql_is_strict_without_union_by_name():
    conn = _RecordingConn()

    create_silver_view(conn=conn, ctx=_Ctx(), category="users", module="user_activity")
    _create_event_mapping_module_view(
        conn, ctx=_Ctx(), module="dev_toolbox", view_name="v_event_mapping__dev_toolbox"
    )

    combined_sql = "\n".join(conn.sql).lower()
    assert "read_parquet" in combined_sql
    assert "union_by_name" not in combined_sql


def test_schema_failure_is_recorded_and_later_builders_still_run(
    monkeypatch, recipe_module
):
    events: list[str] = []
    _install_common_recipe_stubs(monkeypatch, recipe_module, events=events)
    specs = [
        (Path("base_failed.yaml"), _Spec("base_failed", [], "users", "user_activity")),
        (Path("base_later.yaml"), _Spec("base_later", [])),
    ]
    monkeypatch.setattr(
        recipe_module, "resolve_gold_spec_build_order", lambda *_args, **_kwargs: specs
    )

    def create_view(**_kwargs):
        raise RuntimeError("schema mismatch: column x")

    def apply_spec(_conn, spec):
        events.append(f"apply:{spec.name}")

    monkeypatch.setattr(recipe_module, "create_silver_view", create_view)
    monkeypatch.setattr(recipe_module, "apply_gold_spec", apply_spec)
    monkeypatch.setattr(
        recipe_module,
        "unload_gold_tables_with_details",
        lambda *_args, **_kwargs: (["fact_user_activity_daily"], [], []),
    )

    with pytest.raises(recipe_module.CreateGoldBestEffortError) as exc_info:
        recipe_module.run()

    assert "GOLD completed best-effort with" in str(exc_info.value)
    assert "highest impact:" in str(exc_info.value)
    assert "base_failed" in str(exc_info.value)
    assert "apply:base_later" in events
    records = exc_info.value.result["failure_records"]
    assert records[0]["stage"] == "silver_view"
    assert records[0]["target"] == "base_failed"
    assert records[0]["category"] == "users"
    assert records[0]["module"] == "user_activity"
    assert records[0]["error_type"] == "RuntimeError"
    assert "schema mismatch" in records[0]["error_message"]


def test_unload_failure_records_detail_and_continues(monkeypatch):
    calls: list[str] = []

    def unload_table(*_args, table_name: str, **_kwargs):
        calls.append(table_name)
        if table_name == "base_bad":
            raise ValueError("copy failed")

    monkeypatch.setattr(unload, "unload_gold_table", unload_table)

    unloaded_tables, failed_tables, failures = unload.unload_gold_tables_with_details(
        object(),
        gold_ctx=object(),
        gold_folder_lookup="gold_data",
        table_names=["base_ok", "base_bad", "base_later"],
        unload_behavior="dataiku",
    )

    assert calls == ["base_ok", "base_bad", "base_later"]
    assert unloaded_tables == ["base_ok", "base_later"]
    assert failed_tables == ["base_bad"]
    assert failures[0].table == "base_bad"
    assert failures[0].error_type == "ValueError"
    assert failures[0].error_message == "copy failed"


def test_recipe_raises_once_after_later_build_and_unload_attempts(
    monkeypatch, recipe_module
):
    events: list[str] = []
    _install_common_recipe_stubs(monkeypatch, recipe_module, events=events)
    monkeypatch.setattr(
        recipe_module, "resolve_gold_spec_build_order", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        recipe_module, "apply_gold_spec", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        recipe_module,
        "build_fact_user_activity_daily",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("daily failed")),
    )

    def unload_with_details(*_args, **_kwargs):
        events.append("unload_attempted")
        return (
            ["fact_user_activity_project_daily"],
            ["fact_user_activity_daily"],
            [
                SimpleNamespace(
                    table="fact_user_activity_daily",
                    error_type="OSError",
                    error_message="upload failed",
                )
            ],
        )

    monkeypatch.setattr(
        recipe_module, "unload_gold_tables_with_details", unload_with_details
    )

    with pytest.raises(recipe_module.CreateGoldBestEffortError) as exc_info:
        recipe_module.run()

    assert "unload_attempted" in events
    message = str(exc_info.value)
    assert "GOLD completed best-effort with 2 failures" in message
    assert "highest impact: 3 (Critical)" in message
    assert "fact_user_activity_daily" in message
    assert exc_info.value.result["has_failures"] is True
    assert exc_info.value.result["failed_tables"] == ["fact_user_activity_daily"]


def test_no_failure_run_returns_compatible_fields(monkeypatch, recipe_module):
    events: list[str] = []
    _install_common_recipe_stubs(monkeypatch, recipe_module, events=events)
    monkeypatch.setattr(
        recipe_module, "resolve_gold_spec_build_order", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        recipe_module, "apply_gold_spec", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        recipe_module,
        "unload_gold_tables_with_details",
        lambda *_args, **_kwargs: (
            ["fact_user_activity_daily", "fact_user_activity_project_daily"],
            [],
            [],
        ),
    )

    result = recipe_module.run()

    assert result["ok"] is True
    assert result["has_failures"] is False
    assert result["failure_records"] == []
    assert result["failed_tables"] == []


def test_dependency_blocked_work_is_reported_not_attempted(monkeypatch, recipe_module):
    events: list[str] = []
    _install_common_recipe_stubs(monkeypatch, recipe_module, events=events)
    specs = [
        (Path("base_failed.yaml"), _Spec("base_failed", [])),
        (Path("base_blocked.yaml"), _Spec("base_blocked", ["base_failed"])),
    ]
    monkeypatch.setattr(
        recipe_module, "resolve_gold_spec_build_order", lambda *_args, **_kwargs: specs
    )

    def apply_spec(_conn, spec):
        events.append(f"apply:{spec.name}")
        if spec.name == "base_failed":
            raise RuntimeError("upstream failed")

    monkeypatch.setattr(recipe_module, "apply_gold_spec", apply_spec)
    monkeypatch.setattr(
        recipe_module,
        "unload_gold_tables_with_details",
        lambda *_args, **_kwargs: ([], [], []),
    )

    with pytest.raises(recipe_module.CreateGoldBestEffortError) as exc_info:
        recipe_module.run()

    assert "apply:base_blocked" not in events
    blocked = [
        record
        for record in exc_info.value.result["failure_records"]
        if record["target"] == "base_blocked"
    ]
    assert blocked[0]["stage"] == "dependency_skip"
    assert blocked[0]["dependency"] == "base_failed"


def test_failed_unload_does_not_persist_manifest_or_mutate_original(
    monkeypatch, recipe_module
):
    events: list[str] = []
    manifest = {"watermarks": {"fact_user_activity_daily": "old"}, "updated_at": "old"}
    _install_common_recipe_stubs(monkeypatch, recipe_module, events=events)
    monkeypatch.setattr(
        recipe_module, "resolve_gold_spec_build_order", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        recipe_module, "read_manifest", lambda *_args, **_kwargs: manifest
    )
    monkeypatch.setattr(
        recipe_module,
        "unload_gold_tables_with_details",
        lambda *_args, manifest=None, **_kwargs: (
            manifest.__setitem__("watermarks", {"fact_user_activity_daily": "new"})
            or [],
            ["fact_user_activity_daily"],
            [
                SimpleNamespace(
                    table="fact_user_activity_daily",
                    error_type="RuntimeError",
                    error_message="failed",
                )
            ],
        ),
    )
    original = copy.deepcopy(manifest)

    with pytest.raises(recipe_module.CreateGoldBestEffortError):
        recipe_module.run()

    assert "write_manifest" not in events
    assert manifest == original
