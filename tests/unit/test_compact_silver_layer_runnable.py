from __future__ import annotations

import importlib.util
import sys
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


def _path_index_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "full_path": "bucket/root/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=04/day=24/file1.parquet",
                "header_path": "bucket/root",
                "layer": "silver",
                "category": "event_mapping",
                "module": "administration",
                "instance_name": "mazzei_pulse",
                "year": "2026",
                "month": "04",
                "day": "24",
                "base_name": "file1.parquet",
            },
            {
                "full_path": "bucket/root/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=04/day=24/file2.parquet",
                "header_path": "bucket/root",
                "layer": "silver",
                "category": "event_mapping",
                "module": "administration",
                "instance_name": "mazzei_pulse",
                "year": "2026",
                "month": "04",
                "day": "24",
                "base_name": "file2.parquet",
            },
            {
                "full_path": "bucket/root/silver/category=event_mapping/module=administration/instance_name=other/year=2027/month=05/day=01/file3.parquet",
                "header_path": "bucket/root",
                "layer": "silver",
                "category": "event_mapping",
                "module": "administration",
                "instance_name": "other",
                "year": "2027",
                "month": "05",
                "day": "01",
                "base_name": "file3.parquet",
            },
        ]
    )


def test_run_uses_one_index_scan_and_exact_phase3_filters(monkeypatch):
    module = _load_runnable_module()
    seen: dict[str, object] = {}

    def fake_build_storage_context(*, project_key: str, folder_lookup: str):
        seen["project_key"] = project_key
        seen["folder_lookup"] = folder_lookup
        return _storage_context("EC2")

    def fake_build_index(storage_ctx, *, relative_prefix: str, suffix: str | None = None):
        seen["build_index_args"] = (storage_ctx, relative_prefix, suffix)
        return _path_index_df()

    def fake_filter(df, **filters):
        seen.setdefault("filters", []).append(filters)
        filtered = df.copy()
        for key, value in filters.items():
            filtered = filtered.loc[filtered[key] == value]
        return filtered.reset_index(drop=True)

    def fake_select_day(df):
        seen["select_day_rows"] = len(df)
        return ("2026", "04", "24")

    def fake_read(storage_ctx, *, full_paths: list[str]):
        seen["read_paths"] = list(full_paths)
        out = pd.DataFrame([{"a": 1, "b": 2}, {"a": 1, "b": 2}]).drop_duplicates()
        out.attrs["files_read"] = len(full_paths)
        out.attrs["raw_rows"] = 2
        out.attrs["rows_after_drop_duplicates"] = 1
        out.attrs["output_column_count"] = len(out.columns)
        return out

    monkeypatch.setattr(module, "build_storage_context", fake_build_storage_context)
    monkeypatch.setattr(module, "build_managed_folder_path_index", fake_build_index)
    monkeypatch.setattr(module, "filter_path_index", fake_filter)
    monkeypatch.setattr(module, "select_latest_partition_day", fake_select_day)
    monkeypatch.setattr(module, "read_s3_parquet_files", fake_read)

    runnable = module.MyRunnable("DASHBOARD_PROJECT", {}, {})
    result = runnable.run(progress_callback=None)

    assert seen["project_key"] == "DASHBOARD_PROJECT"
    assert seen["folder_lookup"] == "partitioned_data"
    assert seen["build_index_args"][1:] == ("silver/category=event_mapping/", ".parquet")
    assert seen["filters"][0] == {
        "category": "event_mapping",
        "module": "administration",
        "instance_name": "mazzei_pulse",
    }
    assert seen["filters"][1] == {"year": "2026", "month": "04", "day": "24"}
    assert seen["select_day_rows"] == 2
    assert seen["read_paths"] == [
        "bucket/root/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=04/day=24/file1.parquet",
        "bucket/root/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=04/day=24/file2.parquet",
    ]
    assert result.columns == [
        (1, "step", "STRING"),
        (2, "value", "STRING"),
        (3, "scope", "STRING"),
        (4, "status", "STRING"),
        (5, "details", "STRING"),
    ]
    assert [record[0] for record in result.records] == [
        "Resolve Folder",
        "Connection Name",
        "Connection Type",
        "All Parquet Found",
        "DataFrame Built",
        "Filtered Subset",
        "Selected Day Test",
        "Native S3 Day Read",
    ]
    assert result.records[3] == [
        "All Parquet Found",
        "3",
        "silver/category=event_mapping/**/*.parquet",
        "info",
        result.records[3][4],
    ]
    assert result.records[4] == [
        "DataFrame Built",
        "rows=3, columns=10",
        "full native discovery index",
        "info",
        result.records[4][4],
    ]
    assert "full_path" in result.records[4][4]
    assert result.records[5] == [
        "Filtered Subset",
        "rows=2, columns=10",
        "category=event_mapping; module=administration; instance_name=mazzei_pulse",
        "info",
        "exact Phase 3 development filter",
    ]
    assert result.records[6] == [
        "Selected Day Test",
        "2026/04/24",
        "category=event_mapping; module=administration; instance_name=mazzei_pulse",
        "info",
        "deterministic latest matching day",
    ]
    assert result.records[7][0:4] == [
        "Native S3 Day Read",
        "rows=1, columns=2",
        "2026/04/24",
        "info",
    ]
    assert "files=2; raw_rows=2; rows_after_drop_duplicates=1;" in result.records[7][4]


def test_result_table_maps_supported_provider_labels(monkeypatch):
    module = _load_runnable_module()

    expected = {
        "EC2": "AWS/S3",
        "Azure": "Azure Blob Storage",
        "GCS": "Google Cloud Storage",
    }

    for connection_type, provider_label in expected.items():
        monkeypatch.setattr(module, "build_storage_context", lambda **kwargs: _storage_context(connection_type))
        monkeypatch.setattr(module, "build_managed_folder_path_index", lambda *args, **kwargs: pd.DataFrame(columns=["full_path", "header_path", "layer", "category", "module", "instance_name", "year", "month", "day", "base_name"]))
        monkeypatch.setattr(module, "filter_path_index", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("No SILVER parquet files matched the Phase 3 development filter")))
        try:
            module._build_result_table(project_key="PROJ", folder_lookup="partitioned_data")
        except ValueError:
            pass
        else:
            raise AssertionError("expected Phase 3 filter failure")

        rt = module._new_result_table()
        rt.add_record([
            "Connection Type",
            provider_label,
            "partitioned_data",
            "ok",
            f"raw DSS type: {connection_type}",
        ])
        assert rt.records[0][1] == provider_label


def test_unknown_provider_is_visibly_unsupported(monkeypatch):
    module = _load_runnable_module()
    monkeypatch.setattr(module, "build_storage_context", lambda **kwargs: _storage_context("LocalFS"))
    monkeypatch.setattr(module, "build_managed_folder_path_index", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("Unsupported managed-folder provider for native discovery: LocalFS")))

    with pytest.raises(RuntimeError, match="Unsupported managed-folder provider"):
        module._build_result_table(project_key="PROJ", folder_lookup="partitioned_data")


def test_runnable_does_not_render_credential_bearing_fields_or_import_provider_sdks(monkeypatch):
    module = _load_runnable_module()
    monkeypatch.setattr(module, "build_storage_context", lambda **kwargs: _storage_context("EC2"))
    monkeypatch.setattr(module, "build_managed_folder_path_index", lambda *args, **kwargs: _path_index_df().iloc[0:1].reset_index(drop=True))
    monkeypatch.setattr(module, "filter_path_index", lambda df, **kwargs: df)
    monkeypatch.setattr(module, "select_latest_partition_day", lambda df: ("2026", "04", "24"))
    monkeypatch.setattr(module, "read_s3_parquet_files", lambda *args, **kwargs: pd.DataFrame([{"a": 1}]))

    result = module._build_result_table(project_key="PROJ", folder_lookup="partitioned_data")

    rendered_values = {str(value) for row in result.records for value in row}
    assert "secret-bucket" not in rendered_values
    assert "redacted/root" not in rendered_values
    assert "top-secret" not in rendered_values
    assert "also-secret" not in rendered_values
    assert "bucket/root/silver/category=event_mapping" not in rendered_values
    assert "boto3" not in module.__dict__
    assert "azure.storage.blob" not in sys.modules
    assert "google.cloud" not in sys.modules
