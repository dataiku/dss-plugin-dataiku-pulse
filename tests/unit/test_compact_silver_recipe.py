from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
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


@pytest.fixture()
def recipe_module():
    return _load_recipe_module()


def _stream_result() -> SimpleNamespace:
    storage_ctx = SimpleNamespace(
        folder_id="resolved-folder-id",
        connection_name="resolved-connection",
        connection_type="EC2",
    )
    return SimpleNamespace(
        storage_ctx=storage_ctx,
        provider_label="AWS/S3",
        selected_batch=SimpleNamespace(selected_partitions=[]),
        execution_mode="joblib_threads",
        selection_mode="all_eligible_filtered",
        dispatch_batch_size=2,
        queue_summary=SimpleNamespace(
            total_matched_paths=11,
            filtered_matching_paths=8,
            skipped_compact_outputs=1,
            excluded_recent_paths=3,
            eligible_paths=4,
            eligible_partition_count=2,
            cutoff_date=pd.Timestamp("2026-08-24", tz="UTC").date(),
            minimum_age_days=1,
        ),
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


def _selected_partition(*, module: str, instance_name: str, day: str) -> SimpleNamespace:
    return SimpleNamespace(
        category="event_mapping",
        module=module,
        instance_name=instance_name,
        year="2026",
        month="08",
        day=day,
        partition_scope=(
            f"category=event_mapping; module={module}; "
            f"instance_name={instance_name}; date=2026/08/{day}"
        ),
    )


def _outcome(*, day: str, status: str = "succeeded", message: str = "ok", run_epoch_ms: int = 1786510805000) -> SimpleNamespace:
    success = status == "succeeded"
    return SimpleNamespace(
        year="2026",
        month="08",
        day=day,
        day_scope=f"2026/08/{day}",
        replay_mode="event_mapping_replay",
        status=status,
        files_read=2,
        raw_rows=4,
        rows_after_drop_duplicates=3,
        input_rows=3,
        input_columns=5,
        rehydrated_rows=3,
        rehydrated_columns=47,
        mapper_rows=3 if success else 0,
        mapper_columns=13,
        mapper_groups=1 if success else 0,
        plan_count=1 if success else 0,
        written_count=1 if success else 0,
        verified_count=1 if success else 0,
        deleted_count=2 if success else 0,
        retained_count=0 if success else 2,
        run_epoch_ms=run_epoch_ms,
        message=message,
    )


def _make_streaming_dataset(seen: dict[str, object]):
    class FakeWriter:
        def write_dataframe(self, df):
            seen.setdefault("batches", []).append(df.copy())

        def close(self):
            seen["writer_closed"] = True

    class FakeDataset:
        def __init__(self, name):
            seen["dataset_name"] = name
            self.writer = FakeWriter()

        def write_schema_from_dataframe(self, df):
            seen["schema_df"] = df.copy()

        def get_writer(self):
            return self.writer

    return FakeDataset


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


def test_recipe_resolves_preset_from_plugin_config_and_mode_from_recipe_config(monkeypatch, recipe_module):
    seen: dict[str, object] = {}

    monkeypatch.setattr(recipe_module.dataiku, "default_project_key", lambda: "TEST_PROJECT")
    monkeypatch.setattr(recipe_module, "get_plugin_config", lambda: {"pulse_primary": {"pulse_partitioned_data": "configured_partitioned_data", "do_parallel": False, "cores": 3, "batch_size": 11}})
    monkeypatch.setattr(recipe_module, "get_recipe_config", lambda: {"normalize_silver": True})
    monkeypatch.setattr(recipe_module, "get_output_names_for_role", lambda role: ["audit_dataset_name"])
    monkeypatch.setattr(recipe_module, "get_dss_execution_environment", lambda: "local")
    monkeypatch.setattr(recipe_module.dataiku, "Dataset", _make_streaming_dataset(seen))

    def fake_run_compact(config, *, on_outcomes):
        seen["config"] = config
        stream_result = _stream_result()
        on_outcomes(
            stream_result,
            [
                _selected_partition(module="administration", instance_name="mazzei_pulse", day="23"),
                _selected_partition(module="containers", instance_name="tam-global", day="22"),
            ],
            [_outcome(day="23"), _outcome(day="22", run_epoch_ms=1786510805001)],
        )
        return stream_result

    monkeypatch.setattr(recipe_module, "run_compact_silver_streaming", fake_run_compact)

    result = recipe_module.run()

    assert seen["config"].folder_lookup == "configured_partitioned_data"
    assert seen["config"].normalize_silver_mode is True
    assert seen["config"].param_set == {
        "pulse_partitioned_data": "configured_partitioned_data",
        "do_parallel": False,
        "cores": 3,
        "batch_size": 11,
    }
    assert seen["config"].partition_filters == {"category": "event_mapping"}
    assert seen["config"].execution_environment == "local"
    assert seen["config"].batch_size == 11
    assert seen["config"].selection_mode == "all_eligible_filtered"
    assert seen["dataset_name"] == "audit_dataset_name"
    assert result["source_folder_lookup"] == "configured_partitioned_data"
    assert result["audit_dataset"] == "audit_dataset_name"


def test_recipe_uses_default_partitioned_data_lookup_when_preset_value_missing(monkeypatch, recipe_module):
    seen: dict[str, object] = {}

    monkeypatch.setattr(recipe_module.dataiku, "default_project_key", lambda: "TEST_PROJECT")
    monkeypatch.setattr(recipe_module, "get_plugin_config", lambda: {"pulse_primary": {"do_parallel": False, "cores": 2, "batch_size": 25}})
    monkeypatch.setattr(recipe_module, "get_recipe_config", lambda: {"normalize_silver": False})
    monkeypatch.setattr(recipe_module, "get_output_names_for_role", lambda role: ["audit_dataset_name"])
    monkeypatch.setattr(recipe_module, "get_dss_execution_environment", lambda: "local")
    monkeypatch.setattr(recipe_module.dataiku, "Dataset", _make_streaming_dataset(seen))

    def fake_run_compact(config, *, on_outcomes):
        seen["folder_lookup"] = config.folder_lookup
        stream_result = _stream_result()
        on_outcomes(stream_result, [_selected_partition(module="administration", instance_name="mazzei_pulse", day="23")], [_outcome(day="23")])
        return stream_result

    monkeypatch.setattr(recipe_module, "run_compact_silver_streaming", fake_run_compact)

    result = recipe_module.run()

    assert seen["folder_lookup"] == "partitioned_data"
    assert result["source_folder_lookup"] == "partitioned_data"


def test_recipe_container_run_ignores_invalid_preset_worker_settings(monkeypatch, recipe_module):
    seen: dict[str, object] = {}

    monkeypatch.setattr(recipe_module.dataiku, "default_project_key", lambda: "TEST_PROJECT")
    monkeypatch.setattr(recipe_module, "get_plugin_config", lambda: {"pulse_primary": {"pulse_partitioned_data": "partitioned_data", "do_parallel": False, "cores": "invalid", "batch_size": 25}})
    monkeypatch.setattr(recipe_module, "get_recipe_config", lambda: {"normalize_silver": True})
    monkeypatch.setattr(recipe_module, "get_output_names_for_role", lambda role: ["compact_silver_audit_ds"])
    monkeypatch.setattr(recipe_module, "get_dss_execution_environment", lambda: "container-name")
    monkeypatch.setattr(recipe_module.dataiku, "Dataset", _make_streaming_dataset(seen))

    def fake_run_compact(config, *, on_outcomes):
        seen["config"] = config
        stream_result = _stream_result()
        stream_result.worker_resolution = SimpleNamespace(
            execution_environment="container-name",
            resolution_source="container_auto",
            python_visible_cpu_count=8,
            configured_cores=None,
            parallel_enabled=True,
            resolved_n_jobs=7,
            partition_cap=7,
        )
        stream_result.dispatch_batch_size = 7
        on_outcomes(stream_result, [_selected_partition(module="administration", instance_name="mazzei_pulse", day="23")], [_outcome(day="23")])
        return stream_result

    monkeypatch.setattr(recipe_module, "run_compact_silver_streaming", fake_run_compact)

    result = recipe_module.run()

    assert seen["config"].batch_size == 25
    assert result["parallel_enabled"] is True
    assert result["requested_workers"] == 7
    assert result["partition_cap"] == 7
    assert result["dispatch_batch_size"] == 7


def test_recipe_builds_bounded_streamed_audit_batches(monkeypatch, recipe_module):
    seen: dict[str, object] = {}

    monkeypatch.setattr(recipe_module.dataiku, "default_project_key", lambda: "TEST_PROJECT")
    monkeypatch.setattr(recipe_module, "get_plugin_config", lambda: {"pulse_primary": {"pulse_partitioned_data": "partitioned_data", "batch_size": 25}})
    monkeypatch.setattr(recipe_module, "get_recipe_config", lambda: {"normalize_silver": True})
    monkeypatch.setattr(recipe_module, "get_output_names_for_role", lambda role: ["compact_silver_audit_ds"])
    monkeypatch.setattr(recipe_module, "get_dss_execution_environment", lambda: "container-name")
    monkeypatch.setattr(recipe_module.dataiku, "Dataset", _make_streaming_dataset(seen))

    def fake_run_compact(config, *, on_outcomes):
        stream_result = _stream_result()
        on_outcomes(
            stream_result,
            [
                _selected_partition(module="administration", instance_name="alpha", day="23"),
                _selected_partition(module="containers", instance_name="beta", day="22"),
            ],
            [_outcome(day="23"), _outcome(day="22", run_epoch_ms=1786510805001)],
        )
        return stream_result

    monkeypatch.setattr(recipe_module, "run_compact_silver_streaming", fake_run_compact)

    result = recipe_module.run()

    assert result["processed_partition_count"] == 2
    assert list(seen["schema_df"].columns) == recipe_module.AUDIT_COLUMNS
    assert len(seen["batches"]) == 2
    outcome_df = seen["batches"][0]
    summary_df = seen["batches"][1]
    assert list(outcome_df["record_type"]) == ["partition_outcome", "partition_outcome"]
    assert outcome_df["selected_days"].isna().all()
    assert set(outcome_df["selected_partition_scope"]) == {
        "category=event_mapping; module=administration; instance_name=alpha; date=2026/08/23",
        "category=event_mapping; module=containers; instance_name=beta; date=2026/08/22",
    }
    assert list(summary_df["record_type"]) == ["run_summary"]
    assert summary_df.iloc[0]["selected_days"] == (
        "category=event_mapping; module=administration; instance_name=alpha; date=2026/08/23, "
        "category=event_mapping; module=containers; instance_name=beta; date=2026/08/22"
    )
    assert seen["writer_closed"] is True


def test_recipe_summary_preview_is_bounded_and_outcome_rows_do_not_repeat_full_selection_list(monkeypatch, recipe_module):
    seen: dict[str, object] = {}

    monkeypatch.setattr(recipe_module.dataiku, "default_project_key", lambda: "TEST_PROJECT")
    monkeypatch.setattr(recipe_module, "get_plugin_config", lambda: {"pulse_primary": {"pulse_partitioned_data": "partitioned_data", "batch_size": 25}})
    monkeypatch.setattr(recipe_module, "get_recipe_config", lambda: {"normalize_silver": True})
    monkeypatch.setattr(recipe_module, "get_output_names_for_role", lambda role: ["compact_silver_audit_ds"])
    monkeypatch.setattr(recipe_module, "get_dss_execution_environment", lambda: "container-name")
    monkeypatch.setattr(recipe_module.dataiku, "Dataset", _make_streaming_dataset(seen))

    partitions = [
        _selected_partition(module=f"module-{index}", instance_name=f"instance-{index}", day=f"{23 - index:02d}")
        for index in range(6)
    ]
    outcomes = [_outcome(day=f"{23 - index:02d}", run_epoch_ms=1786510805000 + index) for index in range(6)]

    def fake_run_compact(config, *, on_outcomes):
        stream_result = _stream_result()
        on_outcomes(stream_result, partitions[:3], outcomes[:3])
        on_outcomes(stream_result, partitions[3:], outcomes[3:])
        return stream_result

    monkeypatch.setattr(recipe_module, "run_compact_silver_streaming", fake_run_compact)

    recipe_module.run()

    outcome_df = pd.concat(seen["batches"][:-1], ignore_index=True)
    summary_row = seen["batches"][-1].iloc[0]
    expected_preview = (
        "category=event_mapping; module=module-0; instance_name=instance-0; date=2026/08/23, "
        "category=event_mapping; module=module-1; instance_name=instance-1; date=2026/08/22, "
        "category=event_mapping; module=module-2; instance_name=instance-2; date=2026/08/21, "
        "category=event_mapping; module=module-3; instance_name=instance-3; date=2026/08/20, "
        "category=event_mapping; module=module-4; instance_name=instance-4; date=2026/08/19 ... (+1 more)"
    )
    assert summary_row["selected_days"] == expected_preview
    assert outcome_df["selected_days"].isna().all()
    assert set(outcome_df["selected_partition_scope"]) == {partition.partition_scope for partition in partitions}


def test_recipe_audit_records_container_override_capacity(monkeypatch, recipe_module):
    seen: dict[str, object] = {}

    monkeypatch.setattr(recipe_module.dataiku, "default_project_key", lambda: "TEST_PROJECT")
    monkeypatch.setattr(recipe_module, "get_plugin_config", lambda: {"pulse_primary": {"pulse_partitioned_data": "partitioned_data", "do_parallel": False, "cores": 2, "batch_size": 25}})
    monkeypatch.setattr(recipe_module, "get_recipe_config", lambda: {"normalize_silver": True})
    monkeypatch.setattr(recipe_module, "get_output_names_for_role", lambda role: ["compact_silver_audit_ds"])
    monkeypatch.setattr(recipe_module, "get_dss_execution_environment", lambda: "container-name")
    monkeypatch.setattr(recipe_module.dataiku, "Dataset", _make_streaming_dataset(seen))

    def fake_run_compact(config, *, on_outcomes):
        stream_result = _stream_result()
        stream_result.worker_resolution = SimpleNamespace(
            execution_environment="container-name",
            resolution_source="container_auto",
            python_visible_cpu_count=8,
            configured_cores=None,
            parallel_enabled=True,
            resolved_n_jobs=7,
            partition_cap=7,
        )
        stream_result.dispatch_batch_size = 7
        on_outcomes(stream_result, [_selected_partition(module="administration", instance_name="alpha", day="23")], [_outcome(day="23")])
        return stream_result

    monkeypatch.setattr(recipe_module, "run_compact_silver_streaming", fake_run_compact)

    result = recipe_module.run()

    audit_df = pd.concat(seen["batches"], ignore_index=True)
    assert result["parallel_enabled"] is True
    assert result["requested_workers"] == 7
    assert result["partition_cap"] == 7
    assert result["selection_mode"] == "all_eligible_filtered"
    assert result["eligible_partition_count"] == 2
    assert result["processed_partition_count"] == 1
    assert result["dispatch_batch_size"] == 7
    assert set(audit_df["worker_resolution_source"]) == {"container_auto"}
    assert set(audit_df["partition_cap"]) == {7}


def test_macro_remains_capacity_limited_default_mode():
    coordinator_text = COORDINATOR_PATH.read_text(encoding="utf-8")
    runnable_text = RUNNABLE_PATH.read_text(encoding="utf-8")

    assert 'selection_mode: Literal["latest_up_to_capacity", "all_eligible_filtered"] = "latest_up_to_capacity"' in coordinator_text
    assert 'selection_mode="all_eligible_filtered"' not in runnable_text


def test_recipe_partial_outcome_writes_audit_then_fails(monkeypatch, recipe_module):
    seen: dict[str, object] = {}

    monkeypatch.setattr(recipe_module.dataiku, "default_project_key", lambda: "TEST_PROJECT")
    monkeypatch.setattr(recipe_module, "get_plugin_config", lambda: {"pulse_primary": {"pulse_partitioned_data": "partitioned_data", "batch_size": 25}})
    monkeypatch.setattr(recipe_module, "get_recipe_config", lambda: {"normalize_silver": True})
    monkeypatch.setattr(recipe_module, "get_output_names_for_role", lambda role: ["compact_silver_audit_ds"])
    monkeypatch.setattr(recipe_module, "get_dss_execution_environment", lambda: "container")
    monkeypatch.setattr(recipe_module.dataiku, "Dataset", _make_streaming_dataset(seen))

    def fake_run_compact(config, *, on_outcomes):
        stream_result = _stream_result()
        on_outcomes(
            stream_result,
            [
                _selected_partition(module="administration", instance_name="alpha", day="23"),
                _selected_partition(module="containers", instance_name="beta", day="22"),
            ],
            [
                _outcome(day="23", status="no_mapped_output_retained", message="current mapper produced no output"),
                _outcome(day="22"),
            ],
        )
        return stream_result

    monkeypatch.setattr(recipe_module, "run_compact_silver_streaming", fake_run_compact)

    with pytest.raises(RuntimeError, match="non-success partition outcomes"):
        recipe_module.run()

    audit_df = pd.concat(seen["batches"], ignore_index=True)
    assert list(audit_df["terminal_status"]) == ["no_mapped_output_retained", "succeeded", "partial"]


def test_macro_and_recipe_both_import_shared_coordinator():
    recipe_text = RECIPE_PATH.read_text(encoding="utf-8")
    runnable_text = RUNNABLE_PATH.read_text(encoding="utf-8")
    coordinator_text = COORDINATOR_PATH.read_text(encoding="utf-8")

    assert "from data_collection.audit_logs_modules.compact_silver_coordinator import CompactRunConfig, CompactStreamRunResult, run_compact_silver_streaming" in recipe_text
    assert "from data_collection.audit_logs_modules.compact_silver_coordinator import (" in runnable_text
    assert "def run_compact_silver(config: CompactRunConfig) -> CompactRunResult:" in coordinator_text
