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


def test_run_uses_partitioned_data_lookup_for_project_key(monkeypatch):
    module = _load_runnable_module()
    seen: dict[str, str] = {}

    def fake_build_storage_context(*, project_key: str, folder_lookup: str):
        seen["project_key"] = project_key
        seen["folder_lookup"] = folder_lookup
        return _storage_context("EC2")

    monkeypatch.setattr(module, "build_storage_context", fake_build_storage_context)

    runnable = module.MyRunnable("DASHBOARD_PROJECT", {}, {})
    result = runnable.run(progress_callback=None)

    assert seen == {"project_key": "DASHBOARD_PROJECT", "folder_lookup": "partitioned_data"}
    assert result.records == [[
        "ok",
        "Resolved managed folder via AWS/S3",
        "partitioned_data",
        "resolved-folder-id",
        "resolved-connection",
        "EC2",
        "AWS/S3",
    ]]


def test_result_table_maps_supported_provider_labels(monkeypatch):
    module = _load_runnable_module()

    expected = {
        "EC2": "AWS/S3",
        "Azure": "Azure Blob Storage",
        "GCS": "Google Cloud Storage",
    }

    for connection_type, provider_label in expected.items():
        monkeypatch.setattr(module, "build_storage_context", lambda **kwargs: _storage_context(connection_type))
        result = module._build_result_table(project_key="PROJ", folder_lookup="partitioned_data")
        assert result.records == [[
            "ok",
            f"Resolved managed folder via {provider_label}",
            "partitioned_data",
            "resolved-folder-id",
            "resolved-connection",
            connection_type,
            provider_label,
        ]]


def test_unknown_provider_is_visibly_unsupported(monkeypatch):
    module = _load_runnable_module()
    monkeypatch.setattr(module, "build_storage_context", lambda **kwargs: _storage_context("LocalFS"))

    result = module._build_result_table(project_key="PROJ", folder_lookup="partitioned_data")

    assert result.records == [[
        "unsupported",
        "Unsupported managed-folder provider type: LocalFS",
        "partitioned_data",
        "resolved-folder-id",
        "resolved-connection",
        "LocalFS",
        "unsupported",
    ]]


def test_result_table_does_not_render_credential_bearing_fields(monkeypatch):
    module = _load_runnable_module()
    monkeypatch.setattr(module, "build_storage_context", lambda **kwargs: _storage_context("EC2"))

    result = module._build_result_table(project_key="PROJ", folder_lookup="partitioned_data")

    rendered_values = {str(value) for row in result.records for value in row}
    assert "secret-bucket" not in rendered_values
    assert "redacted/root" not in rendered_values
    assert "top-secret" not in rendered_values
    assert "also-secret" not in rendered_values
