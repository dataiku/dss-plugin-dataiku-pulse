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


def _selected_day_paths() -> SimpleNamespace:
    return SimpleNamespace(
        total_matched_paths=7,
        filtered_matching_paths=5,
        excluded_recent_paths=3,
        eligible_paths=2,
        cutoff_date=date(2026, 8, 24),
        minimum_age_days=3,
        year="2026",
        month="08",
        day="23",
        full_paths=[
            "bucket/root/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/file1.parquet",
            "bucket/root/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/file2.parquet",
        ],
    )


def test_run_uses_streaming_selector_once_and_fixed_three_day_guard(monkeypatch):
    module = _load_runnable_module()
    seen: dict[str, object] = {"suppressed": 0, "selector_calls": 0}
    info_logs: list[str] = []

    def fake_suppress():
        seen["suppressed"] += 1

    def fake_build_storage_context(*, project_key: str, folder_lookup: str):
        seen["project_key"] = project_key
        seen["folder_lookup"] = folder_lookup
        return _storage_context("EC2")

    def fake_selector(storage_ctx, *, relative_prefix: str, suffix: str | None = None, partition_filters: dict[str, str], minimum_age_days: int):
        seen["selector_calls"] += 1
        seen["selector_args"] = (storage_ctx, relative_prefix, suffix, partition_filters, minimum_age_days)
        return _selected_day_paths()

    def fail_old_count(*args, **kwargs):
        raise AssertionError("count API must not be called")

    def fail_old_index(*args, **kwargs):
        raise AssertionError("full index DataFrame must not be built")

    def fake_read(storage_ctx, *, full_paths: list[str]):
        seen["read_paths"] = list(full_paths)
        out = pd.DataFrame([{"a": 1, "b": 2}, {"a": 1, "b": 2}]).drop_duplicates()
        out.attrs["files_read"] = len(full_paths)
        out.attrs["raw_rows"] = 2
        out.attrs["rows_after_drop_duplicates"] = 1
        out.attrs["output_column_count"] = len(out.columns)
        return out

    monkeypatch.setattr(module, "suppress_inherited_provider_debug_logging", fake_suppress)
    monkeypatch.setattr(module, "build_storage_context", fake_build_storage_context)
    monkeypatch.setattr(module, "select_latest_partition_paths", fake_selector)
    monkeypatch.setattr(module, "read_s3_parquet_files", fake_read)
    monkeypatch.setattr(module, "count_managed_folder_paths", fail_old_count, raising=False)
    monkeypatch.setattr(module, "build_managed_folder_path_index", fail_old_index, raising=False)
    monkeypatch.setattr(module.logger, "info", lambda msg, *args: info_logs.append(msg % args if args else msg))

    runnable = module.MyRunnable("DASHBOARD_PROJECT", {}, {})
    result = runnable.run(progress_callback=None)

    assert seen["suppressed"] == 1
    assert seen["project_key"] == "DASHBOARD_PROJECT"
    assert seen["folder_lookup"] == "partitioned_data"
    assert seen["selector_calls"] == 1
    assert seen["selector_args"][1:] == (
        "silver/category=event_mapping/",
        ".parquet",
        {
            "category": "event_mapping",
            "module": "administration",
            "instance_name": "mazzei_pulse",
        },
        3,
    )
    assert seen["read_paths"] == _selected_day_paths().full_paths
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
        "Native S3 Day Read",
    ]
    assert result.records[3][0:4] == [
        "All Parquet Found",
        "7",
        "silver/category=event_mapping/**/*.parquet",
        "info",
    ]
    assert result.records[4] == [
        "Full DataFrame",
        "not built",
        "full native discovery index",
        "info",
        "intentionally deferred: exceeds macro memory at this scale",
    ]
    assert result.records[5] == [
        "Filtered Subset",
        "5",
        "category=event_mapping; module=administration; instance_name=mazzei_pulse",
        "info",
        "includes all matching dates before age guard",
    ]
    assert result.records[6] == [
        "Recent Partitions Excluded",
        "3",
        "UTC dates >= 2026-08-24",
        "info",
        "minimum_age_days=3; cutoff_date=2026-08-24",
    ]
    assert result.records[7] == [
        "Eligible Subset",
        "2",
        "UTC dates < 2026-08-24",
        "info",
        "streaming; no full index",
    ]
    assert result.records[8] == [
        "Selected Day Test",
        "2026/08/23",
        "category=event_mapping; module=administration; instance_name=mazzei_pulse",
        "info",
        "latest eligible numeric day; retained files=2",
    ]
    assert result.records[9][0:4] == [
        "Native S3 Day Read",
        "rows=1, columns=2",
        "2026/08/23",
        "info",
    ]
    assert "files=2; raw_rows=2; rows_after_drop_duplicates=1;" in result.records[9][4]
    assert any(
        "Compact silver native streaming scan completed scanned=7 filtered=5 excluded_recent=3 eligible=2 cutoff_date=2026-08-24 selected_day=2026/08/23 retained_files=2 elapsed="
        in line
        for line in info_logs
    )


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
        module._build_result_table(project_key="PROJ", folder_lookup="partitioned_data")


def test_unknown_provider_is_visibly_unsupported(monkeypatch):
    module = _load_runnable_module()
    monkeypatch.setattr(module, "suppress_inherited_provider_debug_logging", lambda: None)
    monkeypatch.setattr(module, "build_storage_context", lambda **kwargs: _storage_context("LocalFS"))
    monkeypatch.setattr(module, "select_latest_partition_paths", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("Unsupported managed-folder provider for native discovery: LocalFS")))

    with pytest.raises(RuntimeError, match="Unsupported managed-folder provider"):
        module._build_result_table(project_key="PROJ", folder_lookup="partitioned_data")


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

    result = module._build_result_table(project_key="PROJ", folder_lookup="partitioned_data")

    rendered_values = {str(value) for row in result.records for value in row}
    assert "secret-bucket" not in rendered_values
    assert "redacted/root" not in rendered_values
    assert "top-secret" not in rendered_values
    assert "also-secret" not in rendered_values
    assert "bucket/root/silver/category=event_mapping" not in rendered_values
    assert "file1.parquet" not in rendered_values
    assert "file2.parquet" not in rendered_values
    assert "boto3" not in module.__dict__
    assert "azure.storage.blob" not in sys.modules
    assert "google.cloud" not in sys.modules
