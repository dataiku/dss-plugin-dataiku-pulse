from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Literal

from joblib import Parallel, delayed

from data_collection.audit_logs_modules.compact_silver_queue import CompactSilverQueue, QueueSummary
from data_collection.audit_logs_modules.event_mapping_replay import CompactPartitionOutcome, process_compact_selected_partition
from data_collection.helper import DSSFolderTarget, chunked
from shared_duckdb.context import build_storage_context
from shared_storage_discovery import (
    SelectedPartitionBatch,
    SelectedPartitionPaths,
    select_all_eligible_partition_paths_batch,
    select_latest_partition_paths_batch,
)

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
    normalize_silver_mode: bool
    param_set: dict[str, Any]
    execution_environment: str
    batch_size: int
    selection_mode: Literal["latest_up_to_capacity", "all_eligible_filtered"] = "latest_up_to_capacity"


@dataclass(frozen=True)
class CompactRunResult:
    storage_ctx: Any
    provider_label: str | None
    selected_batch: SelectedPartitionBatch
    outcomes: list[CompactPartitionOutcome]
    execution_mode: str
    worker_resolution: "WorkerResolution"
    dispatch_batch_size: int
    selection_mode: str


@dataclass(frozen=True)
class CompactStreamRunResult:
    storage_ctx: Any
    provider_label: str | None
    selected_batch: SelectedPartitionBatch
    execution_mode: str
    worker_resolution: "WorkerResolution"
    dispatch_batch_size: int
    selection_mode: str
    queue_summary: QueueSummary


@dataclass(frozen=True)
class WorkerResolution:
    execution_environment: str
    resolution_source: str
    python_visible_cpu_count: int | None
    configured_cores: int | None
    parallel_enabled: bool
    resolved_n_jobs: int
    partition_cap: int


def resolve_worker_resolution(*, param_set: dict[str, Any], execution_environment: str) -> WorkerResolution:
    if execution_environment != "local":
        visible_cores = os.cpu_count() or 1
        resolved_n_jobs = max(1, visible_cores - 1)
        return WorkerResolution(
            execution_environment=execution_environment,
            resolution_source="container_auto",
            python_visible_cpu_count=visible_cores,
            configured_cores=None,
            parallel_enabled=True,
            resolved_n_jobs=resolved_n_jobs,
            partition_cap=resolved_n_jobs,
        )

    default_cores = max((os.cpu_count() or 2) - 1, 1)
    safe_default_cores = min(default_cores, 4)
    do_parallel = bool(param_set.get("do_parallel", True))
    configured_cores = max(1, int(param_set.get("cores", safe_default_cores)))
    resolved_n_jobs = configured_cores if do_parallel else 1
    return WorkerResolution(
        execution_environment=execution_environment,
        resolution_source="local_preset",
        python_visible_cpu_count=os.cpu_count(),
        configured_cores=configured_cores,
        parallel_enabled=do_parallel,
        resolved_n_jobs=resolved_n_jobs,
        partition_cap=configured_cores,
    )


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
        logger.exception("Compact SILVER partition worker failed for %s", selected_partition.partition_scope)
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


def _selected_batch_from_queue_summary(summary: QueueSummary) -> SelectedPartitionBatch:
    return SelectedPartitionBatch(
        total_matched_paths=summary.total_matched_paths,
        filtered_matching_paths=summary.filtered_matching_paths,
        skipped_compact_outputs=summary.skipped_compact_outputs,
        excluded_recent_paths=summary.excluded_recent_paths,
        eligible_paths=summary.eligible_paths,
        cutoff_date=summary.cutoff_date,
        minimum_age_days=summary.minimum_age_days,
        selected_partitions=[],
    )


def _queue_status_for_outcome(outcome: CompactPartitionOutcome) -> str:
    if outcome.status == "succeeded":
        return "succeeded"
    if outcome.retained_count > 0:
        return "retained"
    return "failed"


def run_compact_silver_streaming(
    config: CompactRunConfig,
    *,
    on_outcomes: Callable[[CompactStreamRunResult, list[SelectedPartitionPaths], list[CompactPartitionOutcome]], None],
) -> CompactStreamRunResult:
    worker_resolution = resolve_worker_resolution(
        param_set=config.param_set,
        execution_environment=config.execution_environment,
    )
    storage_ctx = build_storage_context(project_key=config.project_key, folder_lookup=config.folder_lookup)
    if config.selection_mode != "all_eligible_filtered":
        raise ValueError(f"Streaming compact execution does not support selection_mode={config.selection_mode!r}")

    queue = CompactSilverQueue.create()
    try:
        queue_summary = queue.populate_from_discovery(
            storage_ctx=storage_ctx,
            relative_prefix=config.relative_prefix,
            suffix=".parquet",
            partition_filters=config.partition_filters,
            minimum_age_days=config.minimum_age_days,
        )
        dispatch_batch_size = worker_resolution.resolved_n_jobs
        target = DSSFolderTarget(project_key=config.project_key, folder_lookup=config.folder_lookup)
        execution_mode = "joblib_threads" if worker_resolution.parallel_enabled and worker_resolution.resolved_n_jobs > 1 else "sequential"
        stream_result = CompactStreamRunResult(
            storage_ctx=storage_ctx,
            provider_label=PROVIDER_LABELS.get(storage_ctx.connection_type),
            selected_batch=_selected_batch_from_queue_summary(queue_summary),
            execution_mode=execution_mode,
            worker_resolution=worker_resolution,
            dispatch_batch_size=dispatch_batch_size,
            selection_mode=config.selection_mode,
            queue_summary=queue_summary,
        )

        while True:
            queue_batch = queue.next_partition_batch(batch_size=dispatch_batch_size)
            if not queue_batch.selected_partitions:
                break
            _batch_execution_mode, outcomes = run_partition_jobs(
                storage_ctx=storage_ctx,
                target=target,
                selected_partitions=queue_batch.selected_partitions,
                normalize_silver_mode=config.normalize_silver_mode,
                do_parallel=worker_resolution.parallel_enabled,
                n_jobs=worker_resolution.resolved_n_jobs,
                batch_size=dispatch_batch_size,
            )
            for partition, outcome in zip(queue_batch.selected_partitions, outcomes, strict=False):
                queue.mark_partition_status(partition=partition, status=_queue_status_for_outcome(outcome))
            on_outcomes(stream_result, queue_batch.selected_partitions, outcomes)

        return stream_result
    finally:
        queue.close()


def run_compact_silver(config: CompactRunConfig) -> CompactRunResult:
    worker_resolution = resolve_worker_resolution(
        param_set=config.param_set,
        execution_environment=config.execution_environment,
    )
    storage_ctx = build_storage_context(project_key=config.project_key, folder_lookup=config.folder_lookup)
    if config.selection_mode == "all_eligible_filtered":
        selected_batch = select_all_eligible_partition_paths_batch(
            storage_ctx,
            relative_prefix=config.relative_prefix,
            suffix=".parquet",
            partition_filters=config.partition_filters,
            minimum_age_days=config.minimum_age_days,
        )
        dispatch_batch_size = worker_resolution.partition_cap
    else:
        selected_batch = select_latest_partition_paths_batch(
            storage_ctx,
            relative_prefix=config.relative_prefix,
            suffix=".parquet",
            partition_filters=config.partition_filters,
            partition_count=worker_resolution.partition_cap,
            minimum_age_days=config.minimum_age_days,
        )
        dispatch_batch_size = config.batch_size
    if selected_batch.filtered_matching_paths <= 0:
        raise ValueError("No SILVER parquet files matched the requested exact compact filter scope")

    target = DSSFolderTarget(project_key=config.project_key, folder_lookup=config.folder_lookup)
    execution_mode, outcomes = run_partition_jobs(
        storage_ctx=storage_ctx,
        target=target,
        selected_partitions=selected_batch.selected_partitions,
        normalize_silver_mode=config.normalize_silver_mode,
        do_parallel=worker_resolution.parallel_enabled,
        n_jobs=worker_resolution.resolved_n_jobs,
        batch_size=dispatch_batch_size,
    )
    return CompactRunResult(
        storage_ctx=storage_ctx,
        provider_label=PROVIDER_LABELS.get(storage_ctx.connection_type),
        selected_batch=selected_batch,
        outcomes=outcomes,
        execution_mode=execution_mode,
        worker_resolution=worker_resolution,
        dispatch_batch_size=dispatch_batch_size,
        selection_mode=config.selection_mode,
    )


__all__ = [
    "CompactRunConfig",
    "CompactRunResult",
    "CompactStreamRunResult",
    "PROVIDER_LABELS",
    "WorkerResolution",
    "resolve_worker_resolution",
    "run_compact_silver",
    "run_compact_silver_streaming",
    "run_partition_jobs",
]
