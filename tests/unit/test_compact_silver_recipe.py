from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest


RECIPE_PATH = Path("custom-recipes/compact-silver-layer/recipe.py")
RECIPE_JSON_PATH = Path("custom-recipes/compact-silver-layer/recipe.json")
RUNNABLE_PATH = Path("python-runnables/compact-silver-layer/runnable.py")
COORDINATOR_PATH = Path("python-lib/data_collection/audit_logs_modules/compact_silver_coordinator.py")


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_recipe_module():
    sys.path.insert(0, str(Path("tests/stubs").resolve()))
    sys.path.insert(0, str(Path("python-lib").resolve()))
    return _load_module("compact_silver_recipe_test_module", RECIPE_PATH)


def _run_result(*, status: str = "success") -> SimpleNamespace:
    storage_ctx = SimpleNamespace(
        folder_id="resolved-folder-id",
        connection_name="resolved-connection",
        connection_type="EC2",
    )
    selected_batch = SimpleNamespace(
        total_matched_paths=11,
        filtered_matching_paths=8,
        skipped_compact_outputs=1,
        excluded_recent_paths=3,
        eligible_paths=4,
        cutoff_date=pd.Timestamp("2026-08-24", tz="UTC").date(),
        selected_partitions=[SimpleNamespace(year="2026", month="08", day="23"), SimpleNamespace(year="2026", month="08", day="22")],
    )
    outcomes = [
        SimpleNamespace(
            day_scope="2026/08/23",
            replay_mode="event_mapping_replay",
            status="succeeded" if status == "success" else "no_mapped_output_retained",
            files_read=2,
            raw_rows=4,
            rows_after_drop_duplicates=3,
            input_rows=3,
            input_columns=5,
            rehydrated_rows=3,
            rehydrated_columns=47,
            mapper_rows=3 if status == "success" else 0,
            mapper_columns=13,
            mapper_groups=1 if status == "success" else 0,
            plan_count=1 if status == "success" else 0,
            written_count=1 if status == "success" else 0,
            verified_count=1 if status == "success" else 0,
            deleted_count=2 if status == "success" else 0,
            retained_count=0 if status == "success" else 2,
            run_epoch_ms=1786510805000,
            message="ok" if status == "success" else "current mapper produced no output",
        ),
        SimpleNamespace(
            day_scope="2026/08/22",
            replay_mode="event_mapping_replay",
            status="succeeded",
            files_read=2,
            raw_rows=4,
            rows_after_drop_duplicates=3,
            input_rows=3,
            input_columns=5,
            rehydrated_rows=3,
            rehydrated_columns=47,
            mapper_rows=3,
            mapper_columns=13,
            mapper_groups=1,
            plan_count=1,
            written_count=1,
            verified_count=1,
            deleted_count=2,
            retained_count=0,
            run_epoch_ms=1786510805001,
            message="ok",
        ),
    ]
    return SimpleNamespace(
        storage_ctx=storage_ctx,
        provider_label="AWS/S3",
        selected_batch=selected_batch,
        outcomes=outcomes,
        execution_mode="joblib_threads",
    )


def test_recipe_manifest_uses_pulse_primary_and_explicit_flow_roles():
    payload = json.loads(RECIPE_JSON_PATH.read_text(encoding="utf-8"))

    assert payload["inputRoles"] == [
        {
            "name": "source_partitioned_data",
            "label": "Source Partitioned Data Folder",
            "description": "Managed folder selected in the Flow for in-place compact SILVER mutation under the existing write → verify → delete safety contract.",
            "arity": "UNARY",
            "required": True,
            "acceptsDataset": False,
            "acceptsManagedFolder": True,
        }
    ]
    assert payload["outputRoles"] == [
        {
            "name": "compact_silver_audit",
            "label": "Compact SILVER Audit Dataset",
            "description": "Dataset written once per recipe run with bounded compact SILVER audit records; compacted parquet files remain in the selected managed folder.",
            "arity": "UNARY",
            "required": True,
            "acceptsDataset": True,
            "acceptsManagedFolder": False,
        }
    ]
    assert payload["params"][0] == {
        "type": "PRESET",
        "name": "pulse_primary",
        "label": "Choose PULSE Primary Configuration",
        "parameterSetId": "params-dashboard-instance",
        "mandatory": True,
    }


@pytest.fixture()
def recipe_module():
    return _load_recipe_module()


def test_recipe_resolves_preset_from_plugin_config_and_mode_from_recipe_config(monkeypatch, recipe_module):
    seen = {}

    monkeypatch.setattr(recipe_module.dataiku, "default_project_key", lambda: "TEST_PROJECT")
    monkeypatch.setattr(recipe_module, "get_plugin_config", lambda: {"pulse_primary": {"do_parallel": False, "cores": 3, "batch_size": 11}})
    monkeypatch.setattr(recipe_module, "get_recipe_config", lambda: {"normalize_silver": True})
    monkeypatch.setattr(recipe_module, "get_input_names_for_role", lambda role: ["partitioned_data_role_name"])
    monkeypatch.setattr(recipe_module, "get_output_names_for_role", lambda role: ["audit_dataset_name"])
    monkeypatch.setattr(recipe_module, "get_dss_execution_environment", lambda: "local")

    class FakeDataset:
        def __init__(self, name):
            seen["dataset_name"] = name

        def write_with_schema(self, df):
            seen["audit_df"] = df.copy()

    monkeypatch.setattr(recipe_module.dataiku, "Dataset", FakeDataset)

    def fake_run_compact(config):
        seen["config"] = config
        return _run_result(status="success")

    monkeypatch.setattr(recipe_module, "run_compact_silver", fake_run_compact)

    result = recipe_module.run()

    assert seen["config"].folder_lookup == "partitioned_data_role_name"
    assert seen["config"].normalize_silver_mode is True
    assert seen["config"].do_parallel is False
    assert seen["config"].n_jobs == 3
    assert seen["config"].batch_size == 11
    assert seen["dataset_name"] == "audit_dataset_name"
    assert result["source_folder_lookup"] == "partitioned_data_role_name"
    assert result["audit_dataset"] == "audit_dataset_name"


def test_recipe_writes_bounded_audit_dataframe_without_paths_or_secrets(monkeypatch, recipe_module):
    seen = {}

    monkeypatch.setattr(recipe_module.dataiku, "default_project_key", lambda: "TEST_PROJECT")
    monkeypatch.setattr(recipe_module, "get_plugin_config", lambda: {"pulse_primary": {"do_parallel": True, "cores": 2, "batch_size": 25}})
    monkeypatch.setattr(recipe_module, "get_recipe_config", lambda: {"normalize_silver": True})
    monkeypatch.setattr(recipe_module, "get_input_names_for_role", lambda role: ["partitioned_data"])
    monkeypatch.setattr(recipe_module, "get_output_names_for_role", lambda role: ["compact_silver_audit_ds"])
    monkeypatch.setattr(recipe_module, "get_dss_execution_environment", lambda: "container")
    monkeypatch.setattr(recipe_module, "run_compact_silver", lambda config: _run_result(status="success"))

    class FakeDataset:
        def __init__(self, name):
            self.name = name

        def write_with_schema(self, df):
            seen["audit_df"] = df.copy()

    monkeypatch.setattr(recipe_module.dataiku, "Dataset", FakeDataset)

    recipe_module.run()

    audit_df = seen["audit_df"]
    assert list(audit_df.columns) == [
        "run_ts_utc",
        "run_id",
        "record_type",
        "project_key",
        "source_folder_lookup",
        "source_folder_id",
        "connection_type",
        "connection_name",
        "execution_environment",
        "filter_scope",
        "utc_cutoff_date",
        "parallel_enabled",
        "requested_workers",
        "selected_partition_count",
        "selected_day",
        "selected_days",
        "replay_mode",
        "terminal_status",
        "files_read",
        "raw_rows",
        "rows_after_drop_duplicates",
        "input_rows",
        "input_columns",
        "rehydrated_rows",
        "rehydrated_columns",
        "mapper_rows",
        "mapper_columns",
        "mapper_groups",
        "normalized_plan_count",
        "written_count",
        "verified_count",
        "deleted_count",
        "retained_count",
        "run_epoch_ms",
        "message",
    ]
    assert len(audit_df) == 3
    rendered = {str(value) for row in audit_df.to_dict(orient="records") for value in row.values()}
    assert "bucket/root/silver/category=event_mapping" not in rendered
    assert "source-a.parquet" not in rendered
    assert "top-secret" not in rendered
    assert "also-secret" not in rendered


def test_recipe_partial_outcome_writes_audit_then_fails(monkeypatch, recipe_module):
    seen = {}

    monkeypatch.setattr(recipe_module.dataiku, "default_project_key", lambda: "TEST_PROJECT")
    monkeypatch.setattr(recipe_module, "get_plugin_config", lambda: {"pulse_primary": {"do_parallel": True, "cores": 2, "batch_size": 25}})
    monkeypatch.setattr(recipe_module, "get_recipe_config", lambda: {"normalize_silver": True})
    monkeypatch.setattr(recipe_module, "get_input_names_for_role", lambda role: ["partitioned_data"])
    monkeypatch.setattr(recipe_module, "get_output_names_for_role", lambda role: ["compact_silver_audit_ds"])
    monkeypatch.setattr(recipe_module, "get_dss_execution_environment", lambda: "container")
    monkeypatch.setattr(recipe_module, "run_compact_silver", lambda config: _run_result(status="partial"))

    class FakeDataset:
        def __init__(self, name):
            self.name = name

        def write_with_schema(self, df):
            seen["audit_df"] = df.copy()

    monkeypatch.setattr(recipe_module.dataiku, "Dataset", FakeDataset)

    with pytest.raises(RuntimeError, match="non-success partition outcomes"):
        recipe_module.run()

    assert "audit_df" in seen
    assert list(seen["audit_df"]["terminal_status"]) == ["partial", "no_mapped_output_retained", "succeeded"]


def test_macro_and_recipe_both_import_shared_coordinator():
    recipe_text = RECIPE_PATH.read_text(encoding="utf-8")
    runnable_text = RUNNABLE_PATH.read_text(encoding="utf-8")
    coordinator_text = COORDINATOR_PATH.read_text(encoding="utf-8")

    assert "from data_collection.audit_logs_modules.compact_silver_coordinator import CompactRunConfig, run_compact_silver" in recipe_text
    assert "from data_collection.audit_logs_modules.compact_silver_coordinator import (" in runnable_text
    assert "def run_compact_silver(config: CompactRunConfig) -> CompactRunResult:" in coordinator_text
