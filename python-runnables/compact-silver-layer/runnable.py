from __future__ import annotations

import logging
import time
from typing import Any

from dataiku.runnables import ResultTable, Runnable

from shared_duckdb.context import build_storage_context
from shared_storage_discovery import (
    build_managed_folder_path_index,
    filter_path_index,
    select_latest_partition_day,
)
from shared_storage_parquet_s3 import read_s3_parquet_files


logger = logging.getLogger(__name__)


PROVIDER_LABELS: dict[str, str] = {
    "EC2": "AWS/S3",
    "Azure": "Azure Blob Storage",
    "GCS": "Google Cloud Storage",
}

EVENT_MAPPING_PREFIX = "silver/category=event_mapping/"
PHASE3_FILTERS = {
    "category": "event_mapping",
    "module": "administration",
    "instance_name": "mazzei_pulse",
}
PHASE3_FILTER_SCOPE = "category=event_mapping; module=administration; instance_name=mazzei_pulse"


def _new_result_table() -> ResultTable:
    rt = ResultTable()
    rt.add_column(1, "step", "STRING")
    rt.add_column(2, "value", "STRING")
    rt.add_column(3, "scope", "STRING")
    rt.add_column(4, "status", "STRING")
    rt.add_column(5, "details", "STRING")
    return rt


def _format_day_scope(year: str, month: str, day: str) -> str:
    return f"{year}/{month}/{day}"


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
        "Compact silver native index scan started folder=%s provider=%s prefix=%s",
        folder_lookup,
        storage_ctx.connection_type,
        EVENT_MAPPING_PREFIX,
    )
    started_at = time.monotonic()
    path_index_df = build_managed_folder_path_index(
        storage_ctx,
        relative_prefix=EVENT_MAPPING_PREFIX,
        suffix=".parquet",
    )
    elapsed = time.monotonic() - started_at
    logger.info(
        "Compact silver native index scan completed rows=%s prefix=%s elapsed=%.1fs",
        len(path_index_df),
        EVENT_MAPPING_PREFIX,
        elapsed,
    )
    rt.add_record([
        "All Parquet Found",
        str(len(path_index_df)),
        "silver/category=event_mapping/**/*.parquet",
        "info",
        f"native full-prefix scan; elapsed={elapsed:.1f}s",
    ])
    rt.add_record([
        "DataFrame Built",
        f"rows={len(path_index_df)}, columns={len(path_index_df.columns)}",
        "full native discovery index",
        "info",
        f"columns={', '.join(path_index_df.columns.tolist())}",
    ])

    filtered_df = filter_path_index(path_index_df, **PHASE3_FILTERS)
    rt.add_record([
        "Filtered Subset",
        f"rows={len(filtered_df)}, columns={len(filtered_df.columns)}",
        PHASE3_FILTER_SCOPE,
        "info",
        "exact Phase 3 development filter",
    ])
    if filtered_df.empty:
        raise ValueError(f"No SILVER parquet files matched the Phase 3 development filter: {PHASE3_FILTER_SCOPE}")

    selected_year, selected_month, selected_day = select_latest_partition_day(filtered_df)
    logger.info(
        "Compact silver selected partition year=%s month=%s day=%s scope=%s",
        selected_year,
        selected_month,
        selected_day,
        PHASE3_FILTER_SCOPE,
    )
    rt.add_record([
        "Selected Day Test",
        _format_day_scope(selected_year, selected_month, selected_day),
        PHASE3_FILTER_SCOPE,
        "info",
        "deterministic latest matching day",
    ])

    selected_day_df = filter_path_index(
        filtered_df,
        year=selected_year,
        month=selected_month,
        day=selected_day,
    )
    if selected_day_df.empty:
        raise ValueError(
            f"No SILVER parquet files matched the selected Phase 3 day: {_format_day_scope(selected_year, selected_month, selected_day)}"
        )

    selected_full_paths = selected_day_df["full_path"].tolist()
    logger.info(
        "Compact silver native S3 day read started day=%s files=%s",
        _format_day_scope(selected_year, selected_month, selected_day),
        len(selected_full_paths),
    )
    read_started_at = time.monotonic()
    selected_day_data = read_s3_parquet_files(storage_ctx, full_paths=selected_full_paths)
    read_elapsed = time.monotonic() - read_started_at
    logger.info(
        "Compact silver native S3 day read completed day=%s files=%s raw_rows=%s deduped_rows=%s columns=%s elapsed=%.1fs",
        _format_day_scope(selected_year, selected_month, selected_day),
        selected_day_data.attrs.get("files_read", 0),
        selected_day_data.attrs.get("raw_rows", 0),
        selected_day_data.attrs.get("rows_after_drop_duplicates", 0),
        selected_day_data.attrs.get("output_column_count", 0),
        read_elapsed,
    )
    rt.add_record([
        "Native S3 Day Read",
        f"rows={len(selected_day_data)}, columns={len(selected_day_data.columns)}",
        _format_day_scope(selected_year, selected_month, selected_day),
        "info",
        (
            f"files={selected_day_data.attrs.get('files_read', 0)}; "
            f"raw_rows={selected_day_data.attrs.get('raw_rows', 0)}; "
            f"rows_after_drop_duplicates={selected_day_data.attrs.get('rows_after_drop_duplicates', 0)}; "
            f"elapsed={read_elapsed:.1f}s"
        ),
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
