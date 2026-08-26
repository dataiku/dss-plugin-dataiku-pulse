from __future__ import annotations

import logging
import time
from typing import Any

from dataiku.runnables import ResultTable, Runnable

from shared_duckdb.context import build_storage_context
from shared_storage_discovery import count_managed_folder_paths


logger = logging.getLogger(__name__)


PROVIDER_LABELS: dict[str, str] = {
    "EC2": "AWS/S3",
    "Azure": "Azure Blob Storage",
    "GCS": "Google Cloud Storage",
}

EVENT_MAPPING_PREFIX = "silver/category=event_mapping/"


def _new_result_table() -> ResultTable:
    rt = ResultTable()
    rt.add_column(1, "step", "STRING")
    rt.add_column(2, "value", "STRING")
    rt.add_column(3, "scope", "STRING")
    rt.add_column(4, "status", "STRING")
    rt.add_column(5, "details", "STRING")
    return rt


def _build_result_table(*, project_key: str, folder_lookup: str) -> ResultTable:
    storage_ctx = build_storage_context(project_key=project_key, folder_lookup=folder_lookup)
    provider_label = PROVIDER_LABELS.get(storage_ctx.connection_type)
    status = "ok" if provider_label else "unsupported"
    rt = _new_result_table()
    rt.add_record([
        "Resolve Folder",
        storage_ctx.folder_id,
        folder_lookup,
        "info",
        "resolved managed-folder ID",
    ])
    rt.add_record([
        "Connection Name",
        storage_ctx.connection_name,
        folder_lookup,
        "info",
        "resolved DSS connection name",
    ])
    rt.add_record([
        "Connection Type",
        provider_label or "unsupported",
        folder_lookup,
        status,
        f"raw DSS type: {storage_ctx.connection_type}",
    ])

    logger.info(
        "Compact silver native discovery started folder=%s provider=%s prefix=%s",
        folder_lookup,
        storage_ctx.connection_type,
        EVENT_MAPPING_PREFIX,
    )
    started_at = time.monotonic()
    parquet_count = count_managed_folder_paths(
        storage_ctx,
        relative_prefix=EVENT_MAPPING_PREFIX,
        suffix=".parquet",
    )
    elapsed = time.monotonic() - started_at
    logger.info(
        "Compact silver native discovery completed count=%s prefix=%s elapsed=%.1fs",
        parquet_count,
        EVENT_MAPPING_PREFIX,
        elapsed,
    )
    rt.add_record([
        "All Parquet Found",
        str(parquet_count),
        "silver/category=event_mapping/**/*.parquet",
        "info",
        f"native full-prefix scan; elapsed={elapsed:.1f}s",
    ])
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
