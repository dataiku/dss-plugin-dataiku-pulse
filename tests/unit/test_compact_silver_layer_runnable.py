from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


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


def test_run_uses_partitioned_data_lookup_and_exact_count_scan(monkeypatch):
    module = _load_runnable_module()
    seen: dict[str, object] = {}

    def fake_build_storage_context(*, project_key: str, folder_lookup: str):
        seen["project_key"] = project_key
        seen["folder_lookup"] = folder_lookup
        return _storage_context("EC2")

    def fake_count(storage_ctx, *, relative_prefix: str, suffix: str | None = None):
        seen["count_args"] = (storage_ctx, relative_prefix, suffix)
        return 42

    monkeypatch.setattr(module, "build_storage_context", fake_build_storage_context)
    monkeypatch.setattr(module, "count_managed_folder_paths", fake_count)

    runnable = module.MyRunnable("DASHBOARD_PROJECT", {}, {})
    result = runnable.run(progress_callback=None)

    assert seen["project_key"] == "DASHBOARD_PROJECT"
    assert seen["folder_lookup"] == "partitioned_data"
    assert seen["count_args"][1:] == ("silver/category=event_mapping/", ".parquet")
    assert result.columns == [
        (1, "step", "STRING"),
        (2, "value", "STRING"),
        (3, "scope", "STRING"),
        (4, "status", "STRING"),
        (5, "details", "STRING"),
    ]
    assert result.records[0] == [
        "Resolve Folder",
        "resolved-folder-id",
        "partitioned_data",
        "info",
        "resolved managed-folder ID",
    ]
    assert result.records[1] == [
        "Connection Name",
        "resolved-connection",
        "partitioned_data",
        "info",
        "resolved DSS connection name",
    ]
    assert result.records[2] == [
        "Connection Type",
        "AWS/S3",
        "partitioned_data",
        "ok",
        "raw DSS type: EC2",
    ]
    assert result.records[3][0:4] == [
        "All Parquet Found",
        "42",
        "silver/category=event_mapping/**/*.parquet",
        "info",
    ]
    assert "native full-prefix scan; elapsed=" in result.records[3][4]


def test_result_table_maps_supported_provider_labels(monkeypatch):
    module = _load_runnable_module()

    expected = {
        "EC2": "AWS/S3",
        "Azure": "Azure Blob Storage",
        "GCS": "Google Cloud Storage",
    }

    for connection_type, provider_label in expected.items():
        monkeypatch.setattr(module, "build_storage_context", lambda **kwargs: _storage_context(connection_type))
        monkeypatch.setattr(module, "count_managed_folder_paths", lambda *args, **kwargs: 0)
        result = module._build_result_table(project_key="PROJ", folder_lookup="partitioned_data")
        assert result.records[2] == [
            "Connection Type",
            provider_label,
            "partitioned_data",
            "ok",
            f"raw DSS type: {connection_type}",
        ]


def test_unknown_provider_is_visibly_unsupported(monkeypatch):
    module = _load_runnable_module()
    monkeypatch.setattr(module, "build_storage_context", lambda **kwargs: _storage_context("LocalFS"))
    monkeypatch.setattr(module, "count_managed_folder_paths", lambda *args, **kwargs: 3)

    result = module._build_result_table(project_key="PROJ", folder_lookup="partitioned_data")

    assert result.records[2] == [
        "Connection Type",
        "unsupported",
        "partitioned_data",
        "unsupported",
        "raw DSS type: LocalFS",
    ]


def test_runnable_does_not_render_credential_bearing_fields_or_import_provider_sdks(monkeypatch):
    module = _load_runnable_module()
    monkeypatch.setattr(module, "build_storage_context", lambda **kwargs: _storage_context("EC2"))
    monkeypatch.setattr(module, "count_managed_folder_paths", lambda *args, **kwargs: 1)

    result = module._build_result_table(project_key="PROJ", folder_lookup="partitioned_data")

    rendered_values = {str(value) for row in result.records for value in row}
    assert "secret-bucket" not in rendered_values
    assert "redacted/root" not in rendered_values
    assert "top-secret" not in rendered_values
    assert "also-secret" not in rendered_values
    assert "boto3" not in module.__dict__
    assert "azure.storage.blob" not in sys.modules
    assert "google.cloud" not in sys.modules
