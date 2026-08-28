from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from joblib import Parallel, delayed

from data_collection.audit_logs_modules.event_mapping_replay import CompactPartitionOutcome, process_compact_selected_partition
from data_collection.helper import DSSFolderTarget, chunked
from shared_duckdb.context import build_storage_context
from shared_storage_discovery import SelectedPartitionBatch, SelectedPartitionPaths, select_latest_partition_paths_batch

logger = logging.getLogger(__name__)


PROVIDER_LABELS: dict[str, str] = {
    "EC2": "AWS/S3",
    "Azure": "Azure Blob Storage",
    "GCS": "Google Cloud Storage",
}


@dataclass(frozen=True)
class CompactRunConfig:
    project_key: str
    folder_lookup: str
    relative_prefix: str
    partition_filters: dict[str, str]
    minimum_age_days: int
    selected_partition_count: int
    normalize_silver_mode: bool
    do_parallel: bool
    n_jobs: int
    batch_size: int


@dataclass(frozen=True)
class CompactRunResult:
    storage_ctx: Any
    provider_label: str | None
    selected_batch: SelectedPartitionBatch
    outcomes: list[CompactPartitionOutcome]
    execution_mode: str


def _process_partition_job(
    *,
    storage_ctx: Any,
    target: DSSFolderTarget,
    selected_partition: SelectedPartitionPaths,
    normalize_silver_mode: bool,
) -> CompactPartitionOutcome:
    try:
        return process_compact_selected_partition(
            storage_ctx=storage_ctx,
            target=target,
            selected_partition=selected_partition,
            normalize_silver_mode=normalize_silver_mode,
        )
    except Exception as exc:
        return CompactPartitionOutcome(
            year=selected_partition.year,
            month=selected_partition.month,
            day=selected_partition.day,
            replay_mode="event_mapping_replay" if normalize_silver_mode else "generic_compaction",
            status="failed",
            message=repr(exc),
            run_epoch_ms=0,
            files_read=0,
            raw_rows=0,
            rows_after_drop_duplicates=0,
            output_column_count=0,
            input_rows=0,
            input_columns=0,
            plan_count=0,
            retained_count=len(selected_partition.relative_paths),
        )


def run_partition_jobs(
    *,
    storage_ctx: Any,
    target: DSSFolderTarget,
    selected_partitions: list[SelectedPartitionPaths],
    normalize_silver_mode: bool,
    do_parallel: bool,
    n_jobs: int,
    batch_size: int,
) -> tuple[str, list[CompactPartitionOutcome]]:
    outcomes: list[CompactPartitionOutcome] = []
    worker_count = n_jobs if do_parallel else 1
    execution_mode = "joblib_threads" if worker_count > 1 else "sequential"

    for partition_batch in chunked(selected_partitions, batch_size):
        if worker_count <= 1:
            batch_outcomes = [
                _process_partition_job(
                    storage_ctx=storage_ctx,
                    target=target,
                    selected_partition=selected_partition,
                    normalize_silver_mode=normalize_silver_mode,
                )
                for selected_partition in partition_batch
            ]
        else:
            batch_outcomes = Parallel(n_jobs=worker_count, prefer="threads")(
                delayed(_process_partition_job)(
                    storage_ctx=storage_ctx,
                    target=target,
                    selected_partition=selected_partition,
                    normalize_silver_mode=normalize_silver_mode,
                )
                for selected_partition in partition_batch
            )
        outcomes.extend(batch_outcomes)
    return execution_mode, outcomes


def run_compact_silver(config: CompactRunConfig) -> CompactRunResult:
    storage_ctx = build_storage_context(project_key=config.project_key, folder_lookup=config.folder_lookup)
    selected_batch = select_latest_partition_paths_batch(
        storage_ctx,
        relative_prefix=config.relative_prefix,
        suffix=".parquet",
        partition_filters=config.partition_filters,
        partition_count=config.selected_partition_count,
        minimum_age_days=config.minimum_age_days,
    )
    if selected_batch.filtered_matching_paths <= 0:
        raise ValueError("No SILVER parquet files matched the requested exact compact filter scope")

    target = DSSFolderTarget(project_key=config.project_key, folder_lookup=config.folder_lookup)
    execution_mode, outcomes = run_partition_jobs(
        storage_ctx=storage_ctx,
        target=target,
        selected_partitions=selected_batch.selected_partitions,
        normalize_silver_mode=config.normalize_silver_mode,
        do_parallel=config.do_parallel,
        n_jobs=config.n_jobs,
        batch_size=config.batch_size,
    )
    return CompactRunResult(
        storage_ctx=storage_ctx,
        provider_label=PROVIDER_LABELS.get(storage_ctx.connection_type),
        selected_batch=selected_batch,
        outcomes=outcomes,
        execution_mode=execution_mode,
    )


__all__ = [
    "CompactRunConfig",
    "CompactRunResult",
    "PROVIDER_LABELS",
    "run_compact_silver",
    "run_partition_jobs",
]
