from __future__ import annotations

from typing import Any

from dataiku.runnables import ResultTable, Runnable

from shared_duckdb.context import build_storage_context


PROVIDER_LABELS: dict[str, str] = {
    "EC2": "AWS/S3",
    "Azure": "Azure Blob Storage",
    "GCS": "Google Cloud Storage",
}


def _build_result_table(*, project_key: str, folder_lookup: str) -> ResultTable:
    storage_ctx = build_storage_context(project_key=project_key, folder_lookup=folder_lookup)
    provider_label = PROVIDER_LABELS.get(storage_ctx.connection_type)
    status = "ok" if provider_label else "unsupported"
    message = (
        f"Resolved managed folder via {provider_label}"
        if provider_label
        else f"Unsupported managed-folder provider type: {storage_ctx.connection_type}"
    )

    rt = ResultTable()
    rt.add_column(1, "status", "STRING")
    rt.add_column(2, "message", "STRING")
    rt.add_column(3, "managed_folder_lookup", "STRING")
    rt.add_column(4, "managed_folder_id", "STRING")
    rt.add_column(5, "connection_name", "STRING")
    rt.add_column(6, "connection_type", "STRING")
    rt.add_column(7, "provider_label", "STRING")
    rt.add_record(
        [
            status,
            message,
            folder_lookup,
            storage_ctx.folder_id,
            storage_ctx.connection_name,
            storage_ctx.connection_type,
            provider_label or "unsupported",
        ]
    )
    return rt


class MyRunnable(Runnable):
    """Compact Silver Layer Phase 1 observability runnable."""

    def __init__(
        self,
        project_key: str,
        config: dict[str, Any] | None,
        plugin_config: dict[str, Any] | None,
    ):
        self.project_key = project_key
        self.config = config or {}
        self.plugin_config = plugin_config or {}

    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        return _build_result_table(
            project_key=self.project_key,
            folder_lookup="partitioned_data",
        )
