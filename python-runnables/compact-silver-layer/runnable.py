from __future__ import annotations

import logging
import os
import time
from typing import Any

from dataiku.runnables import ResultTable, Runnable

from data_collection.audit_logs_modules.compact_silver_coordinator import (
    CompactRunConfig,
    CompactRunResult,
    run_compact_silver,
)
from data_collection.audit_logs_modules.event_mapping_replay import CompactPartitionOutcome
from shared_runtime_logging import suppress_inherited_provider_debug_logging


logger = logging.getLogger(__name__)


EVENT_MAPPING_PREFIX = "silver/category=event_mapping/"
MINIMUM_AGE_DAYS = 3
SELECTED_PARTITION_COUNT = 2
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


def _format_plan_metrics(metrics) -> str:
    if not metrics:
        return "no normalized output plans"
    return "; ".join(
        f"{metric.module_name}:rows={metric.rows},columns={metric.columns},dq_ok={str(metric.dq_ok).lower()}"
        for metric in metrics
    )


def _selected_partition_labels(selected_partitions) -> str:
    return ", ".join(_format_day_scope(item.year, item.month, item.day) for item in selected_partitions)


def _result_status(status: str) -> str:
    return "success" if status == "succeeded" else status


def _aggregate_execution_status(outcomes: list[CompactPartitionOutcome]) -> str:
    if outcomes and all(outcome.status == "succeeded" for outcome in outcomes):
        return "success"
    return "partial"


def _add_partition_result_rows(rt: ResultTable, outcome: CompactPartitionOutcome) -> None:
    rt.add_record([
        "Native S3 Day Read",
        f"rows={outcome.input_rows}, columns={outcome.output_column_count}",
        outcome.day_scope,
        "info",
        (
            f"files={outcome.files_read}; raw_rows={outcome.raw_rows}; "
            f"rows_after_drop_duplicates={outcome.rows_after_drop_duplicates}"
        ),
    ])
    rt.add_record([
        "Input DataFrame",
        f"rows={outcome.input_rows}, columns={outcome.input_columns}",
        outcome.day_scope,
        "info",
        f"files read={outcome.files_read}; deduped reader result",
    ])
    rt.add_record([
        "Replay Mode",
        outcome.replay_mode,
        outcome.day_scope,
        "info",
        f"run_epoch_ms={outcome.run_epoch_ms}; status={outcome.status}",
    ])
    if outcome.replay_mode == "event_mapping_replay":
        rt.add_record([
            "Rehydrated DataFrame",
            f"rows={outcome.rehydrated_rows}, columns={outcome.rehydrated_columns}",
            outcome.day_scope,
            "info",
            "SILVER extras unpacked",
        ])
        rt.add_record([
            "Mapper Output",
            f"rows={outcome.mapper_rows}, columns={outcome.mapper_columns}, groups={outcome.mapper_groups}",
            outcome.day_scope,
            "info",
            "unchanged mapper output",
        ])
    rt.add_record([
        "Normalized Output",
        f"plans={outcome.plan_count}",
        outcome.day_scope,
        "info",
        _format_plan_metrics(outcome.metrics),
    ])
    rt.add_record([
        "Replacement Writes",
        f"written={outcome.written_count}, verified={outcome.verified_count}",
        str(outcome.run_epoch_ms),
        _result_status(outcome.status),
        outcome.message,
    ])
    rt.add_record([
        "Source Deletion",
        f"deleted={outcome.deleted_count}, retained={outcome.retained_count}",
        outcome.day_scope,
        _result_status(outcome.status),
        outcome.message,
    ])


def _build_result_table(
    *,
    project_key: str,
    folder_lookup: str,
    normalize_silver_mode: bool,
    do_parallel: bool,
    n_jobs: int,
    batch_size: int,
) -> ResultTable:
    logger.info("Compact silver native streaming scan started folder=%s prefix=%s", folder_lookup, EVENT_MAPPING_PREFIX)
    started_at = time.monotonic()
    run_result: CompactRunResult = run_compact_silver(
        CompactRunConfig(
            project_key=project_key,
            folder_lookup=folder_lookup,
            relative_prefix=EVENT_MAPPING_PREFIX,
            partition_filters=PHASE3_FILTERS,
            minimum_age_days=MINIMUM_AGE_DAYS,
            selected_partition_count=SELECTED_PARTITION_COUNT,
            normalize_silver_mode=normalize_silver_mode,
            do_parallel=do_parallel,
            n_jobs=n_jobs,
            batch_size=batch_size,
        )
    )
    elapsed = time.monotonic() - started_at

    storage_ctx = run_result.storage_ctx
    selected_batch = run_result.selected_batch
    provider_label = run_result.provider_label
    selected_labels = _selected_partition_labels(selected_batch.selected_partitions)
    logger.info(
        "Compact silver execution completed scanned=%s filtered=%s excluded_recent=%s eligible=%s selected_partitions=%s mode=%s elapsed=%.1fs",
        selected_batch.total_matched_paths,
        selected_batch.filtered_matching_paths,
        selected_batch.excluded_recent_paths,
        selected_batch.eligible_paths,
        selected_labels,
        run_result.execution_mode,
        elapsed,
    )

    rt = _new_result_table()
    rt.add_record(["Resolve Folder", storage_ctx.folder_id, folder_lookup, "info", "resolved managed-folder ID"])
    rt.add_record(["Connection Name", storage_ctx.connection_name, folder_lookup, "info", "resolved DSS connection name"])
    rt.add_record([
        "Connection Type",
        provider_label or "unsupported",
        folder_lookup,
        "ok" if provider_label else "unsupported",
        f"raw DSS type: {storage_ctx.connection_type}",
    ])
    rt.add_record([
        "All Parquet Found",
        str(selected_batch.total_matched_paths),
        "silver/category=event_mapping/**/*.parquet",
        "info",
        f"native streaming full-prefix scan; elapsed={elapsed:.1f}s",
    ])
    rt.add_record([
        "Full DataFrame",
        "not built",
        "full native discovery index",
        "info",
        "intentionally deferred: exceeds macro memory at this scale",
    ])
    rt.add_record([
        "Filtered Subset",
        str(selected_batch.filtered_matching_paths),
        PHASE3_FILTER_SCOPE,
        "info",
        "includes all matching dates before age guard",
    ])
    rt.add_record([
        "Recent Partitions Excluded",
        str(selected_batch.excluded_recent_paths),
        f"UTC dates >= {selected_batch.cutoff_date.isoformat()}",
        "info",
        f"minimum_age_days={selected_batch.minimum_age_days}; cutoff_date={selected_batch.cutoff_date.isoformat()}",
    ])
    rt.add_record([
        "Eligible Subset",
        str(selected_batch.eligible_paths),
        f"UTC dates < {selected_batch.cutoff_date.isoformat()}",
        "info",
        "streaming; no full index",
    ])
    rt.add_record([
        "Selected Partitions",
        str(len(selected_batch.selected_partitions)),
        PHASE3_FILTER_SCOPE,
        "info",
        f"newest to oldest: {selected_labels}",
    ])
    rt.add_record([
        "Skipped Compact Outputs",
        str(selected_batch.skipped_compact_outputs),
        PHASE3_FILTER_SCOPE,
        "info",
        "existing compact_silver-* sources excluded from selection",
    ])

    for outcome in run_result.outcomes:
        _add_partition_result_rows(rt, outcome)

    total_written = sum(outcome.written_count for outcome in run_result.outcomes)
    total_verified = sum(outcome.verified_count for outcome in run_result.outcomes)
    total_deleted = sum(outcome.deleted_count for outcome in run_result.outcomes)
    total_retained = sum(outcome.retained_count for outcome in run_result.outcomes)
    rt.add_record([
        "Partition Totals",
        f"partitions={len(run_result.outcomes)}",
        PHASE3_FILTER_SCOPE,
        _aggregate_execution_status(run_result.outcomes),
        f"written={total_written}; verified={total_verified}; deleted={total_deleted}; retained={total_retained}",
    ])
    return rt


class MyRunnable(Runnable):
    """Compact SILVER development runnable."""

    def __init__(
        self,
        project_key: str,
        config: dict[str, Any] | None,
        plugin_config: dict[str, Any] | None,
    ):
        self.project_key = project_key
        self.config = config or {}
        self.plugin_config = plugin_config or {}
        self.param_set = self.plugin_config.get("pulse_primary", {}) or {}

        self.do_parallel = bool(self.param_set.get("do_parallel", True))
        default_cores = max((os.cpu_count() or 2) - 1, 1)
        safe_default_cores = min(default_cores, 4)
        self.n_jobs = max(1, int(self.param_set.get("cores", safe_default_cores)))
        self.batch_size = int(self.param_set.get("batch_size", 25))

    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        suppress_inherited_provider_debug_logging()
        normalize_silver_mode = bool(self.config.get("normalize_silver", False))
        return _build_result_table(
            project_key=self.project_key,
            folder_lookup="partitioned_data",
            normalize_silver_mode=normalize_silver_mode,
            do_parallel=self.do_parallel,
            n_jobs=self.n_jobs,
            batch_size=self.batch_size,
        )
