from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest


def _load_runnable_module():
    path = Path(__file__).resolve().parents[2] / "python-runnables" / "compact-silver-layer" / "runnable.py"
    spec = importlib.util.spec_from_file_location("compact_silver_layer_runnable", path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _storage_context(connection_type: str) -> SimpleNamespace:
    return SimpleNamespace(
        folder_id="resolved-folder-id",
        connection_name="resolved-connection",
        connection_type=connection_type,
        folder_lookup="partitioned_data",
        folder_root="redacted/root",
        bucket_or_container="secret-bucket",
        blob_header="s3",
        cached_connection_info={
            "type": connection_type,
            "params": {"secretKey": "top-secret", "accessKey": "also-secret"},
        },
    )


def _selected_record(relative_path: str, full_path: str) -> SimpleNamespace:
    return SimpleNamespace(
        relative_path=relative_path,
        full_path=full_path,
        base_name=Path(relative_path).name,
        layer="silver",
        category="event_mapping",
        module="administration",
        instance_name="mazzei_pulse",
        year="2026",
        month="08",
        day="23",
    )


def _selected_day_paths() -> SimpleNamespace:
    records = [
        _selected_record(
            "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/source-a.parquet",
            "bucket/root/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/source-a.parquet",
        ),
        _selected_record(
            "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/source-b.parquet",
            "bucket/root/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/source-b.parquet",
        ),
    ]
    return SimpleNamespace(
        total_matched_paths=7,
        filtered_matching_paths=5,
        skipped_compact_outputs=1,
        excluded_recent_paths=3,
        eligible_paths=2,
        cutoff_date=date(2026, 8, 24),
        minimum_age_days=3,
        year="2026",
        month="08",
        day="23",
        selected_records=records,
        full_paths=[record.full_path for record in records],
        relative_paths=[record.relative_path for record in records],
    )


def _plan_summary(mode: str) -> SimpleNamespace:
    return SimpleNamespace(
        mode=mode,
        run_epoch_ms=1786510805000,
        input_rows=10,
        input_columns=5,
        rehydrated_rows=10 if mode == "event_mapping_replay" else None,
        rehydrated_columns=47 if mode == "event_mapping_replay" else None,
        mapper_rows=10 if mode == "event_mapping_replay" else None,
        mapper_columns=13 if mode == "event_mapping_replay" else None,
        mapper_groups=2 if mode == "event_mapping_replay" else 0,
        metrics=(
            SimpleNamespace(module_name="administration", rows=10, columns=13, dq_ok=True, dq_errors=()),
            SimpleNamespace(module_name="dataset", rows=4, columns=11, dq_ok=True, dq_errors=()),
        ) if mode == "event_mapping_replay" else (SimpleNamespace(module_name="administration", rows=10, columns=5, dq_ok=True, dq_errors=()),),
    )


def _apply_result(status: str = "succeeded") -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        written_paths=(
            "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/compact_silver-1786510805000-0001.parquet",
        ),
        verified_paths=(
            "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/compact_silver-1786510805000-0001.parquet",
        ) if status in {"succeeded", "delete_failed"} else (),
        cleanup_paths=(),
        deleted_source_paths=(
            "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/source-a.parquet",
            "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/source-b.parquet",
        ) if status == "succeeded" else (),
        retained_source_paths=(
            "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/source-b.parquet",
        ) if status == "delete_failed" else (),
        dq_errors=(),
        message="ok" if status == "succeeded" else "delete failed after verified writes",
    )


def test_run_uses_streaming_selector_preserves_paths_and_generic_mode_from_config(monkeypatch):
    module = _load_runnable_module()
    seen: dict[str, object] = {"suppressed": 0}

    def fake_suppress():
        seen["suppressed"] += 1

    def fake_build_storage_context(*, project_key: str, folder_lookup: str):
        seen["project_key"] = project_key
        seen["folder_lookup"] = folder_lookup
        return _storage_context("EC2")

    def fake_selector(storage_ctx, *, relative_prefix: str, suffix: str | None = None, partition_filters: dict[str, str], minimum_age_days: int):
        seen["selector_args"] = (relative_prefix, suffix, partition_filters, minimum_age_days)
        return _selected_day_paths()

    def fake_read(storage_ctx, *, full_paths: list[str]):
        seen["read_paths"] = list(full_paths)
        out = pd.DataFrame([{"a": 1, "b": 2}, {"a": 1, "b": 2}]).drop_duplicates()
        out.attrs["files_read"] = len(full_paths)
        out.attrs["raw_rows"] = 2
        out.attrs["rows_after_drop_duplicates"] = 1
        out.attrs["output_column_count"] = len(out.columns)
        return out

    def fake_plan(*, selected_records, selected_df, normalize_silver_mode, run_epoch_ms):
        seen["plan"] = {
            "selected_records": selected_records,
            "shape": selected_df.shape,
            "normalize_silver_mode": normalize_silver_mode,
            "run_epoch_ms": run_epoch_ms,
        }
        return [SimpleNamespace(output_path=Path(_apply_result().written_paths[0]), silver_df=selected_df, dq=SimpleNamespace(ok=True, errors=[]), module_name="administration", event_date=date(2026, 8, 23))], _plan_summary("generic_compaction")

    def fake_apply(*, target, folder, source_relative_paths, plans):
        seen["apply"] = {
            "target": target,
            "folder": folder,
            "source_relative_paths": list(source_relative_paths),
            "plan_paths": [str(plan.output_path) for plan in plans],
        }
        return _apply_result("succeeded")

    monkeypatch.setattr(module, "suppress_inherited_provider_debug_logging", fake_suppress)
    monkeypatch.setattr(module, "build_storage_context", fake_build_storage_context)
    monkeypatch.setattr(module, "select_latest_partition_paths", fake_selector)
    monkeypatch.setattr(module, "read_s3_parquet_files", fake_read)
    monkeypatch.setattr(module, "next_compact_save_epoch_ms", lambda: 1786510805000)
    monkeypatch.setattr(module, "plan_compact_selected_day", fake_plan)
    monkeypatch.setattr(module, "get_managed_folder_handle", lambda target: "FOLDER_HANDLE")
    monkeypatch.setattr(module, "apply_compact_replacement_plans", fake_apply)

    runnable = module.MyRunnable("DASHBOARD_PROJECT", {}, {})
    result = runnable.run(progress_callback=None)

    assert seen["suppressed"] == 1
    assert seen["selector_args"] == (
        "silver/category=event_mapping/",
        ".parquet",
        {
            "category": "event_mapping",
            "module": "administration",
            "instance_name": "mazzei_pulse",
        },
        3,
    )
    assert seen["read_paths"] == [record.full_path for record in _selected_day_paths().selected_records]
    assert seen["plan"]["normalize_silver_mode"] is False
    assert seen["apply"]["source_relative_paths"] == [record.relative_path for record in _selected_day_paths().selected_records]
    assert [record[0] for record in result.records] == [
        "Resolve Folder",
        "Connection Name",
        "Connection Type",
        "All Parquet Found",
        "Full DataFrame",
        "Filtered Subset",
        "Recent Partitions Excluded",
        "Eligible Subset",
        "Selected Day Test",
        "Skipped Compact Outputs",
        "Native S3 Day Read",
        "Input DataFrame",
        "Replay Mode",
        "Normalized Output",
        "Replacement Writes",
        "Source Deletion",
    ]
    assert result.records[9] == [
        "Skipped Compact Outputs",
        "1",
        "category=event_mapping; module=administration; instance_name=mazzei_pulse",
        "info",
        "existing compact_silver-* sources excluded from selection",
    ]
    assert result.records[12] == [
        "Replay Mode",
        "generic_compaction",
        "2026/08/23",
        "info",
        "normalize_silver=false from self.config",
    ]
    assert result.records[13][0:4] == [
        "Normalized Output",
        "plans=1",
        "2026/08/23",
        "info",
    ]
    assert result.records[14] == [
        "Replacement Writes",
        "written=1, verified=1",
        "1786510805000",
        "success",
        "ok",
    ]
    assert result.records[15] == [
        "Source Deletion",
        "deleted=2, retained=0",
        "2026/08/23",
        "success",
        "ok",
    ]


def test_run_uses_event_mapping_mode_when_config_true(monkeypatch):
    module = _load_runnable_module()
    monkeypatch.setattr(module, "suppress_inherited_provider_debug_logging", lambda: None)
    monkeypatch.setattr(module, "build_storage_context", lambda **kwargs: _storage_context("EC2"))
    monkeypatch.setattr(module, "select_latest_partition_paths", lambda *args, **kwargs: _selected_day_paths())
    monkeypatch.setattr(module, "read_s3_parquet_files", lambda *args, **kwargs: pd.DataFrame([{"a": 1}, {"a": 1}]).drop_duplicates())
    monkeypatch.setattr(module, "next_compact_save_epoch_ms", lambda: 1786510805000)
    monkeypatch.setattr(
        module,
        "plan_compact_selected_day",
        lambda **kwargs: (
            [SimpleNamespace(output_path=Path(_apply_result().written_paths[0]), silver_df=pd.DataFrame([{"a": 1}]), dq=SimpleNamespace(ok=True, errors=[]), module_name="administration", event_date=date(2026, 8, 23))],
            _plan_summary("event_mapping_replay"),
        ),
    )
    monkeypatch.setattr(module, "get_managed_folder_handle", lambda target: "FOLDER_HANDLE")
    monkeypatch.setattr(module, "apply_compact_replacement_plans", lambda **kwargs: _apply_result("succeeded"))

    runnable = module.MyRunnable("P", {"normalize_silver": True}, {})
    result = runnable.run(progress_callback=None)

    assert result.records[12] == [
        "Replay Mode",
        "event_mapping_replay",
        "2026/08/23",
        "info",
        "normalize_silver=true from self.config",
    ]
    assert result.records[13] == [
        "Rehydrated DataFrame",
        "rows=10, columns=47",
        "2026/08/23",
        "info",
        "SILVER extras unpacked",
    ]
    assert result.records[14] == [
        "Mapper Output",
        "rows=10, columns=13, groups=2",
        "2026/08/23",
        "info",
        "unchanged mapper output",
    ]
    assert result.records[15][0:4] == [
        "Normalized Output",
        "plans=1",
        "2026/08/23",
        "info",
    ]


def test_all_recent_matches_fail_before_s3_reader(monkeypatch):
    module = _load_runnable_module()
    monkeypatch.setattr(module, "suppress_inherited_provider_debug_logging", lambda: None)
    monkeypatch.setattr(module, "build_storage_context", lambda **kwargs: _storage_context("EC2"))
    monkeypatch.setattr(
        module,
        "select_latest_partition_paths",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("All exact-filter matches are excluded by minimum_age_days=3; cutoff_date=2026-08-24")),
    )
    monkeypatch.setattr(module, "read_s3_parquet_files", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("reader must not be called")))

    with pytest.raises(ValueError, match="All exact-filter matches are excluded by minimum_age_days=3; cutoff_date=2026-08-24"):
        module._build_result_table(project_key="PROJ", folder_lookup="partitioned_data", normalize_silver_mode=False)


def test_unknown_provider_is_visibly_unsupported(monkeypatch):
    module = _load_runnable_module()
    monkeypatch.setattr(module, "suppress_inherited_provider_debug_logging", lambda: None)
    monkeypatch.setattr(module, "build_storage_context", lambda **kwargs: _storage_context("LocalFS"))
    monkeypatch.setattr(module, "select_latest_partition_paths", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("Unsupported managed-folder provider for native discovery: LocalFS")))

    with pytest.raises(RuntimeError, match="Unsupported managed-folder provider"):
        module._build_result_table(project_key="PROJ", folder_lookup="partitioned_data", normalize_silver_mode=False)


def test_runnable_results_do_not_render_paths_or_secret_values(monkeypatch):
    module = _load_runnable_module()
    monkeypatch.setattr(module, "suppress_inherited_provider_debug_logging", lambda: None)
    monkeypatch.setattr(module, "build_storage_context", lambda **kwargs: _storage_context("EC2"))
    monkeypatch.setattr(module, "select_latest_partition_paths", lambda *args, **kwargs: _selected_day_paths())

    def fake_read(*args, **kwargs):
        out = pd.DataFrame([{"a": 1}])
        out.attrs["files_read"] = 2
        out.attrs["raw_rows"] = 1
        out.attrs["rows_after_drop_duplicates"] = 1
        out.attrs["output_column_count"] = 1
        return out

    monkeypatch.setattr(module, "read_s3_parquet_files", fake_read)
    monkeypatch.setattr(module, "next_compact_save_epoch_ms", lambda: 1786510805000)
    monkeypatch.setattr(
        module,
        "plan_compact_selected_day",
        lambda **kwargs: ([SimpleNamespace(output_path=Path(_apply_result().written_paths[0]), silver_df=pd.DataFrame([{"a": 1}]), dq=SimpleNamespace(ok=True, errors=[]), module_name="administration", event_date=date(2026, 8, 23))], _plan_summary("generic_compaction")),
    )
    monkeypatch.setattr(module, "get_managed_folder_handle", lambda target: "FOLDER_HANDLE")
    monkeypatch.setattr(module, "apply_compact_replacement_plans", lambda **kwargs: _apply_result("succeeded"))

    result = module._build_result_table(project_key="PROJ", folder_lookup="partitioned_data", normalize_silver_mode=False)

    rendered_values = {str(value) for row in result.records for value in row}
    assert "secret-bucket" not in rendered_values
    assert "redacted/root" not in rendered_values
    assert "top-secret" not in rendered_values
    assert "also-secret" not in rendered_values
    assert "bucket/root/silver/category=event_mapping" not in rendered_values
    assert "source-a.parquet" not in rendered_values
    assert "source-b.parquet" not in rendered_values
    assert "boto3" not in module.__dict__
    assert "azure.storage.blob" not in sys.modules
    assert "google.cloud" not in sys.modules
