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
        worker_resolution=SimpleNamespace(
            execution_environment="local",
            resolution_source="local_preset",
            python_visible_cpu_count=8,
            configured_cores=2,
            parallel_enabled=True,
            resolved_n_jobs=2,
            partition_cap=2,
        ),
    )


def test_recipe_manifest_uses_pulse_primary_and_explicit_flow_roles():
    payload = json.loads(RECIPE_JSON_PATH.read_text(encoding="utf-8"))

    assert payload["inputRoles"] == []
    assert payload["outputRoles"] == [
        {
            "name": "compact_silver_audit",
            "label": "Compact SILVER Audit Dataset",
            "description": "Dataset written once per recipe run with bounded compact SILVER audit records; compacted parquet files remain in the directly resolved partitioned-data managed folder.",
            "arity": "UNARY",
            "required": True,
            "acceptsDataset": True,
        }
    ]
    assert {
        "type": "PRESET",
        "name": "pulse_primary",
        "label": "Choose PULSE Primary Configuration",
        "parameterSetId": "params-dashboard-instance",
        "mandatory": True,
    } in payload["params"]


@pytest.fixture()
def recipe_module():
    return _load_recipe_module()


def test_recipe_resolves_preset_from_plugin_config_and_mode_from_recipe_config(monkeypatch, recipe_module):
    seen = {}

    monkeypatch.setattr(recipe_module.dataiku, "default_project_key", lambda: "TEST_PROJECT")
    monkeypatch.setattr(recipe_module, "get_plugin_config", lambda: {"pulse_primary": {"pulse_partitioned_data": "configured_partitioned_data", "do_parallel": False, "cores": 3, "batch_size": 11}})
    monkeypatch.setattr(recipe_module, "get_recipe_config", lambda: {"normalize_silver": True})
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

    assert seen["config"].folder_lookup == "configured_partitioned_data"
    assert seen["config"].normalize_silver_mode is True
    assert seen["config"].param_set == {
        "pulse_partitioned_data": "configured_partitioned_data",
        "do_parallel": False,
        "cores": 3,
        "batch_size": 11,
    }
    assert seen["config"].execution_environment == "local"
    assert seen["config"].batch_size == 11
    assert seen["dataset_name"] == "audit_dataset_name"
    assert result["source_folder_lookup"] == "configured_partitioned_data"
    assert result["audit_dataset"] == "audit_dataset_name"


def test_recipe_uses_default_partitioned_data_lookup_when_preset_value_missing(monkeypatch, recipe_module):
    seen = {}

    monkeypatch.setattr(recipe_module.dataiku, "default_project_key", lambda: "TEST_PROJECT")
    monkeypatch.setattr(recipe_module, "get_plugin_config", lambda: {"pulse_primary": {"do_parallel": False, "cores": 2, "batch_size": 25}})
    monkeypatch.setattr(recipe_module, "get_recipe_config", lambda: {"normalize_silver": False})
    monkeypatch.setattr(recipe_module, "get_output_names_for_role", lambda role: ["audit_dataset_name"])
    monkeypatch.setattr(recipe_module, "get_dss_execution_environment", lambda: "local")

    class FakeDataset:
        def __init__(self, name):
            self.name = name

        def write_with_schema(self, df):
            seen["audit_df"] = df.copy()

    monkeypatch.setattr(recipe_module.dataiku, "Dataset", FakeDataset)

    def fake_run_compact(config):
        seen["folder_lookup"] = config.folder_lookup
        return _run_result(status="success")

    monkeypatch.setattr(recipe_module, "run_compact_silver", fake_run_compact)

    result = recipe_module.run()

    assert seen["folder_lookup"] == "partitioned_data"
    assert result["source_folder_lookup"] == "partitioned_data"


def test_recipe_container_run_ignores_invalid_preset_worker_settings(monkeypatch, recipe_module):
    seen = {}

    monkeypatch.setattr(recipe_module.dataiku, "default_project_key", lambda: "TEST_PROJECT")
    monkeypatch.setattr(
        recipe_module,
        "get_plugin_config",
        lambda: {"pulse_primary": {"pulse_partitioned_data": "partitioned_data", "do_parallel": False, "cores": "invalid", "batch_size": 11}},
    )
    monkeypatch.setattr(recipe_module, "get_recipe_config", lambda: {"normalize_silver": True})
    monkeypatch.setattr(recipe_module, "get_output_names_for_role", lambda role: ["audit_dataset_name"])
    monkeypatch.setattr(recipe_module, "get_dss_execution_environment", lambda: "container-name")

    container_run_result = _run_result(status="success")
    container_run_result.worker_resolution = SimpleNamespace(
        execution_environment="container-name",
        resolution_source="container_auto",
        python_visible_cpu_count=8,
        configured_cores=None,
        parallel_enabled=True,
        resolved_n_jobs=7,
        partition_cap=7,
    )

    def fake_run_compact(config):
        seen["config"] = config
        return container_run_result

    monkeypatch.setattr(recipe_module, "run_compact_silver", fake_run_compact)

    class FakeDataset:
        def __init__(self, name):
            seen["dataset_name"] = name

        def write_with_schema(self, df):
            seen["audit_df"] = df.copy()

    monkeypatch.setattr(recipe_module.dataiku, "Dataset", FakeDataset)

    result = recipe_module.run()

    assert seen["config"].execution_environment == "container-name"
    assert seen["config"].batch_size == 11
    assert result["parallel_enabled"] is True
    assert result["requested_workers"] == 7
    assert result["partition_cap"] == 7
    assert set(seen["audit_df"]["worker_resolution_source"]) == {"container_auto"}


def test_recipe_batch_size_resolution_remains_unchanged(monkeypatch, recipe_module):
    monkeypatch.setattr(recipe_module.dataiku, "default_project_key", lambda: "TEST_PROJECT")
    monkeypatch.setattr(recipe_module, "get_plugin_config", lambda: {"pulse_primary": {"batch_size": 17}})
    monkeypatch.setattr(recipe_module, "get_recipe_config", lambda: {"normalize_silver": False})
    monkeypatch.setattr(recipe_module, "get_output_names_for_role", lambda role: ["audit_dataset_name"])
    monkeypatch.setattr(recipe_module, "get_dss_execution_environment", lambda: "local")

    seen = {}

    def fake_run_compact(config):
        seen["batch_size"] = config.batch_size
        return _run_result(status="success")

    monkeypatch.setattr(recipe_module, "run_compact_silver", fake_run_compact)

    class FakeDataset:
        def __init__(self, name):
            self.name = name

        def write_with_schema(self, df):
            seen["audit_df"] = df.copy()

    monkeypatch.setattr(recipe_module.dataiku, "Dataset", FakeDataset)

    recipe_module.run()

    assert seen["batch_size"] == 17


def test_recipe_writes_bounded_audit_dataframe_without_paths_or_secrets(monkeypatch, recipe_module):
    seen = {}

    monkeypatch.setattr(recipe_module.dataiku, "default_project_key", lambda: "TEST_PROJECT")
    monkeypatch.setattr(recipe_module, "get_plugin_config", lambda: {"pulse_primary": {"pulse_partitioned_data": "partitioned_data", "do_parallel": True, "cores": 2, "batch_size": 25}})
    monkeypatch.setattr(recipe_module, "get_recipe_config", lambda: {"normalize_silver": True})
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
        "worker_resolution_source",
        "python_visible_cpu_count",
        "configured_cores",
        "filter_scope",
        "utc_cutoff_date",
        "parallel_enabled",
        "requested_workers",
        "partition_cap",
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
    assert set(audit_df["worker_resolution_source"]) == {"local_preset"}
    assert set(audit_df["python_visible_cpu_count"]) == {8}
    assert set(audit_df["configured_cores"].dropna()) == {2}
    assert set(audit_df["partition_cap"]) == {2}


def test_recipe_audit_records_container_override_capacity(monkeypatch, recipe_module):
    seen = {}

    monkeypatch.setattr(recipe_module.dataiku, "default_project_key", lambda: "TEST_PROJECT")
    monkeypatch.setattr(recipe_module, "get_plugin_config", lambda: {"pulse_primary": {"pulse_partitioned_data": "partitioned_data", "do_parallel": False, "cores": 2, "batch_size": 25}})
    monkeypatch.setattr(recipe_module, "get_recipe_config", lambda: {"normalize_silver": True})
    monkeypatch.setattr(recipe_module, "get_output_names_for_role", lambda role: ["compact_silver_audit_ds"])
    monkeypatch.setattr(recipe_module, "get_dss_execution_environment", lambda: "container-name")

    container_run_result = _run_result(status="success")
    container_run_result.worker_resolution = SimpleNamespace(
        execution_environment="container-name",
        resolution_source="container_auto",
        python_visible_cpu_count=8,
        configured_cores=None,
        parallel_enabled=True,
        resolved_n_jobs=7,
        partition_cap=7,
    )
    monkeypatch.setattr(recipe_module, "run_compact_silver", lambda config: container_run_result)

    class FakeDataset:
        def __init__(self, name):
            self.name = name

        def write_with_schema(self, df):
            seen["audit_df"] = df.copy()

    monkeypatch.setattr(recipe_module.dataiku, "Dataset", FakeDataset)

    result = recipe_module.run()

    assert result["parallel_enabled"] is True
    assert result["requested_workers"] == 7
    assert result["partition_cap"] == 7
    assert set(seen["audit_df"]["worker_resolution_source"]) == {"container_auto"}
    assert set(seen["audit_df"]["partition_cap"]) == {7}


def test_recipe_partial_outcome_writes_audit_then_fails(monkeypatch, recipe_module):
    seen = {}

    monkeypatch.setattr(recipe_module.dataiku, "default_project_key", lambda: "TEST_PROJECT")
    monkeypatch.setattr(recipe_module, "get_plugin_config", lambda: {"pulse_primary": {"pulse_partitioned_data": "partitioned_data", "do_parallel": True, "cores": 2, "batch_size": 25}})
    monkeypatch.setattr(recipe_module, "get_recipe_config", lambda: {"normalize_silver": True})
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


def test_recipe_has_no_managed_folder_input_role_lookup():
    recipe_text = RECIPE_PATH.read_text(encoding="utf-8")

    assert "get_input_names_for_role" not in recipe_text
    assert "source_folder_lookup = str(param_set.get(\"pulse_partitioned_data\") or \"partitioned_data\")" in recipe_text
