from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from joblib import Parallel, delayed

from data_collection.audit_logs_modules.compact_silver_queue import (
    CompactSilverQueue,
    QueueSummary,
)
from data_collection.audit_logs_modules.event_mapping_replay import (
    CompactPartitionOutcome,
    process_compact_selected_partition,
)
from data_collection.helper import DSSFolderTarget, chunked
from shared_duckdb.context import build_storage_context
from shared_storage_discovery import (
    SelectedPartitionBatch,
    SelectedPartitionPaths,
    iter_managed_folder_child_prefixes,
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
    selection_mode: Literal["latest_up_to_capacity", "all_eligible_filtered"] = (
        "latest_up_to_capacity"
    )


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
    queue_memory_limit_setting: str | None = None


@dataclass(frozen=True)
class WorkerResolution:
    execution_environment: str
    resolution_source: str
    python_visible_cpu_count: int | None
    configured_cores: int | None
    parallel_enabled: bool
    resolved_n_jobs: int
    partition_cap: int
    uncapped_auto_worker_count: int | None = None


def resolve_worker_resolution(
    *, param_set: dict[str, Any], execution_environment: str
) -> WorkerResolution:
    if execution_environment != "local":
        visible_cores = os.cpu_count() or 1
        uncapped_auto_worker_count = max(1, visible_cores - 1)
        resolved_n_jobs = 1
        return WorkerResolution(
            execution_environment=execution_environment,
            resolution_source="container_auto_capped",
            python_visible_cpu_count=visible_cores,
            configured_cores=None,
            parallel_enabled=True,
            resolved_n_jobs=resolved_n_jobs,
            partition_cap=resolved_n_jobs,
            uncapped_auto_worker_count=uncapped_auto_worker_count,
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
        uncapped_auto_worker_count=None,
    )


def _process_rss_mb() -> int | None:
    try:
        with open("/proc/self/status", encoding="utf-8") as status_file:
            for line in status_file:
                if not line.startswith("VmRSS:"):
                    continue
                parts = line.split()
                if len(parts) < 2:
                    return None
                rss_kb = int(parts[1])
                if rss_kb < 0:
                    return None
                return rss_kb // 1024
    except (OSError, ValueError):
        return None
    return None


def _selected_partitions_source_file_count(
    selected_partitions: list[SelectedPartitionPaths],
) -> int:
    return sum(len(partition.relative_paths) for partition in selected_partitions)


def _process_partition_job(
    *,
    storage_ctx: Any,
    storage_ctx_factory: Callable[[], Any] | None,
    target: DSSFolderTarget,
    selected_partition: SelectedPartitionPaths,
    normalize_silver_mode: bool,
) -> CompactPartitionOutcome:
    try:
        return process_compact_selected_partition(
            storage_ctx=storage_ctx,
            storage_ctx_factory=storage_ctx_factory,
            target=target,
            selected_partition=selected_partition,
            normalize_silver_mode=normalize_silver_mode,
        )
    except Exception as exc:
        logger.exception(
            "Compact SILVER partition worker failed for %s",
            selected_partition.partition_scope,
        )
        return CompactPartitionOutcome(
            year=selected_partition.year,
            month=selected_partition.month,
            day=selected_partition.day,
            replay_mode=(
                "event_mapping_replay"
                if normalize_silver_mode
                else "generic_compaction"
            ),
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
    storage_ctx_factory: Callable[[], Any] | None = None,
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
                    storage_ctx_factory=storage_ctx_factory,
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
                    storage_ctx_factory=storage_ctx_factory,
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


def _add_queue_summaries(left: QueueSummary, right: QueueSummary) -> QueueSummary:
    if (
        left.cutoff_date != right.cutoff_date
        or left.minimum_age_days != right.minimum_age_days
    ):
        raise ValueError(
            "Cannot aggregate Compact SILVER queue summaries with different age cutoffs"
        )
    return QueueSummary(
        total_matched_paths=left.total_matched_paths + right.total_matched_paths,
        filtered_matching_paths=left.filtered_matching_paths
        + right.filtered_matching_paths,
        skipped_compact_outputs=left.skipped_compact_outputs
        + right.skipped_compact_outputs,
        excluded_recent_paths=left.excluded_recent_paths + right.excluded_recent_paths,
        eligible_paths=left.eligible_paths + right.eligible_paths,
        eligible_partition_count=left.eligible_partition_count
        + right.eligible_partition_count,
        cutoff_date=left.cutoff_date,
        minimum_age_days=left.minimum_age_days,
    )


def _queue_status_for_outcome(outcome: CompactPartitionOutcome) -> str:
    if outcome.status == "succeeded":
        return "succeeded"
    if outcome.retained_count > 0:
        return "retained"
    return "failed"


def _filter_module_manifest_for_config(module_manifest: list[Any], *, config: CompactRunConfig) -> list[Any]:
    module_filter = str(config.partition_filters.get("module") or "").strip()
    if not module_filter:
        return module_manifest
    return [entry for entry in module_manifest if entry.module == module_filter]


def run_compact_silver_streaming(
    config: CompactRunConfig,
    *,
    on_outcomes: Callable[
        [
            CompactStreamRunResult,
            list[SelectedPartitionPaths],
            list[CompactPartitionOutcome],
        ],
        None,
    ],
) -> CompactStreamRunResult:
    worker_resolution = resolve_worker_resolution(
        param_set=config.param_set,
        execution_environment=config.execution_environment,
    )
    storage_ctx = build_storage_context(
        project_key=config.project_key, folder_lookup=config.folder_lookup
    )
    if config.selection_mode != "all_eligible_filtered":
        raise ValueError(
            f"Streaming compact execution does not support selection_mode={config.selection_mode!r}"
        )

    queue = CompactSilverQueue.create()
    try:
        logger.info("Compact SILVER module discovery started")
        module_prefixes = list(
            iter_managed_folder_child_prefixes(
                build_storage_context(
                    project_key=config.project_key, folder_lookup=config.folder_lookup
                ),
                relative_prefix=config.relative_prefix,
                expected_partition_key="module",
            )
        )
        module_manifest = queue.replace_module_manifest(module_prefixes=module_prefixes)
        module_manifest = _filter_module_manifest_for_config(
            module_manifest, config=config
        )
        logger.info(
            "Compact SILVER module discovery completed module_count=%s",
            len(module_manifest),
        )
        if not module_manifest:
            raise ValueError(
                "No module prefixes matched the requested Compact SILVER category scope"
            )

        dispatch_batch_size = worker_resolution.resolved_n_jobs
        target = DSSFolderTarget(
            project_key=config.project_key, folder_lookup=config.folder_lookup
        )
        execution_mode = (
            "joblib_threads"
            if worker_resolution.parallel_enabled
            and worker_resolution.resolved_n_jobs > 1
            else "sequential"
        )
        queue_summary: QueueSummary | None = None
        utc_today = datetime.now(timezone.utc).date()
        initial_summary = QueueSummary(
            total_matched_paths=0,
            filtered_matching_paths=0,
            skipped_compact_outputs=0,
            excluded_recent_paths=0,
            eligible_paths=0,
            eligible_partition_count=0,
            cutoff_date=utc_today,
            minimum_age_days=config.minimum_age_days,
        )
        stream_result = CompactStreamRunResult(
            storage_ctx=storage_ctx,
            provider_label=PROVIDER_LABELS.get(storage_ctx.connection_type),
            selected_batch=_selected_batch_from_queue_summary(initial_summary),
            execution_mode=execution_mode,
            worker_resolution=worker_resolution,
            dispatch_batch_size=dispatch_batch_size,
            selection_mode=config.selection_mode,
            queue_summary=initial_summary,
            queue_memory_limit_setting=queue.runtime.memory_limit_setting,
        )

        category_succeeded = 0
        category_retained = 0
        category_failed = 0
        for module_entry in module_manifest:
            queue.mark_module_status(module=module_entry.module, status="listing")
            module_storage_ctx = build_storage_context(
                project_key=config.project_key, folder_lookup=config.folder_lookup
            )
            listing_started = time.monotonic()
            logger.info(
                "Compact SILVER module listing started module=%s", module_entry.module
            )
            module_summary = queue.populate_from_discovery(
                storage_ctx=module_storage_ctx,
                relative_prefix=module_entry.relative_prefix,
                suffix=".parquet",
                partition_filters={
                    **config.partition_filters,
                    "module": module_entry.module,
                },
                minimum_age_days=config.minimum_age_days,
                utc_today=utc_today,
                raise_on_empty=False,
            )
            queue_summary = (
                module_summary
                if queue_summary is None
                else _add_queue_summaries(queue_summary, module_summary)
            )
            stream_result = CompactStreamRunResult(
                storage_ctx=storage_ctx,
                provider_label=PROVIDER_LABELS.get(storage_ctx.connection_type),
                selected_batch=_selected_batch_from_queue_summary(queue_summary),
                execution_mode=execution_mode,
                worker_resolution=worker_resolution,
                dispatch_batch_size=dispatch_batch_size,
                selection_mode=config.selection_mode,
                queue_summary=queue_summary,
                queue_memory_limit_setting=queue.runtime.memory_limit_setting,
            )
            logger.info(
                "Compact SILVER module listing completed module=%s elapsed_seconds=%.3f eligible_paths=%s eligible_partitions=%s",
                module_entry.module,
                time.monotonic() - listing_started,
                module_summary.eligible_paths,
                module_summary.eligible_partition_count,
            )
            if module_summary.eligible_paths <= 0:
                queue.mark_module_status(module=module_entry.module, status="succeeded")
                queue.release_module_paths(module=module_entry.module)
                continue

            module_succeeded = 0
            module_retained = 0
            module_failed = 0
            module_batch_sequence = 0
            queue.mark_module_status(module=module_entry.module, status="processing")
            while True:
                queue_batch = queue.next_partition_batch(batch_size=dispatch_batch_size)
                if not queue_batch.selected_partitions:
                    break
                module_batch_sequence += 1
                batch_started = time.monotonic()
                batch_partition_count = len(queue_batch.selected_partitions)
                batch_source_file_count = _selected_partitions_source_file_count(
                    queue_batch.selected_partitions
                )
                module_completed_before = (
                    module_succeeded + module_retained + module_failed
                )
                logger.info(
                    "Compact SILVER batch started module=%s batch_sequence=%s partition_count=%s module_partitions_completed=%s module_partitions_remaining=%s source_file_count=%s elapsed_seconds=%.3f rss_mb=%s",
                    module_entry.module,
                    module_batch_sequence,
                    batch_partition_count,
                    module_completed_before,
                    queue.remaining_pending_partition_count(),
                    batch_source_file_count,
                    0.0,
                    _process_rss_mb(),
                )
                _batch_execution_mode, outcomes = run_partition_jobs(
                    storage_ctx=module_storage_ctx,
                    storage_ctx_factory=lambda: build_storage_context(
                        project_key=config.project_key,
                        folder_lookup=config.folder_lookup,
                    ),
                    target=target,
                    selected_partitions=queue_batch.selected_partitions,
                    normalize_silver_mode=config.normalize_silver_mode,
                    do_parallel=worker_resolution.parallel_enabled,
                    n_jobs=worker_resolution.resolved_n_jobs,
                    batch_size=dispatch_batch_size,
                )
                if len(outcomes) != len(queue_batch.selected_partitions):
                    raise ValueError(
                        "Compact SILVER streaming batch returned mismatched partition/outcome counts: "
                        f"partitions={len(queue_batch.selected_partitions)} outcomes={len(outcomes)}"
                    )
                for partition, outcome in zip(
                    queue_batch.selected_partitions, outcomes, strict=True
                ):
                    status = _queue_status_for_outcome(outcome)
                    queue.mark_partition_status(partition=partition, status=status)
                    if status == "succeeded":
                        module_succeeded += 1
                    elif status == "retained":
                        module_retained += 1
                    else:
                        module_failed += 1
                module_completed_after = (
                    module_succeeded + module_retained + module_failed
                )
                logger.info(
                    "Compact SILVER batch completed module=%s batch_sequence=%s partition_count=%s module_partitions_completed=%s module_partitions_remaining=%s source_file_count=%s elapsed_seconds=%.3f rss_mb=%s",
                    module_entry.module,
                    module_batch_sequence,
                    batch_partition_count,
                    module_completed_after,
                    queue.remaining_pending_partition_count(),
                    batch_source_file_count,
                    time.monotonic() - batch_started,
                    _process_rss_mb(),
                )
                on_outcomes(stream_result, queue_batch.selected_partitions, outcomes)

            category_succeeded += module_succeeded
            category_retained += module_retained
            category_failed += module_failed
            terminal_status = (
                "failed"
                if module_failed
                else "retained" if module_retained else "succeeded"
            )
            queue.mark_module_status(module=module_entry.module, status=terminal_status)
            logger.info(
                "Compact SILVER module processing completed module=%s succeeded=%s retained=%s failed=%s",
                module_entry.module,
                module_succeeded,
                module_retained,
                module_failed,
            )
            queue.release_module_paths(module=module_entry.module)

        if queue_summary is None:
            raise ValueError(
                "No managed-folder paths matched the requested exact partition filters"
            )
        if (
            queue_summary.filtered_matching_paths > 0
            and queue_summary.eligible_paths <= 0
        ):
            raise ValueError(
                "All exact-filter matches are excluded by "
                f"minimum_age_days={config.minimum_age_days}; cutoff_date={queue_summary.cutoff_date.isoformat()}"
            )
        if queue_summary.eligible_paths <= 0:
            raise ValueError(
                "No managed-folder paths matched the requested exact partition filters"
            )
        logger.info(
            "Compact SILVER category summary modules=%s eligible_paths=%s eligible_partitions=%s succeeded=%s retained=%s failed=%s",
            len(module_manifest),
            queue_summary.eligible_paths,
            queue_summary.eligible_partition_count,
            category_succeeded,
            category_retained,
            category_failed,
        )

        return stream_result
    finally:
        queue.close()


def run_compact_silver(config: CompactRunConfig) -> CompactRunResult:
    worker_resolution = resolve_worker_resolution(
        param_set=config.param_set,
        execution_environment=config.execution_environment,
    )
    storage_ctx = build_storage_context(
        project_key=config.project_key, folder_lookup=config.folder_lookup
    )
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
        raise ValueError(
            "No SILVER parquet files matched the requested exact compact filter scope"
        )

    target = DSSFolderTarget(
        project_key=config.project_key, folder_lookup=config.folder_lookup
    )
    execution_mode, outcomes = run_partition_jobs(
        storage_ctx=storage_ctx,
        storage_ctx_factory=lambda: build_storage_context(
            project_key=config.project_key,
            folder_lookup=config.folder_lookup,
        ),
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
