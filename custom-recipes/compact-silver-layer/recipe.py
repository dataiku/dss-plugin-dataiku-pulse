from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import dataiku
from dataiku.customrecipe import get_output_names_for_role, get_plugin_config, get_recipe_config

from data_collection.audit_logs_modules.compact_silver_coordinator import CompactRunConfig, CompactStreamRunResult, run_compact_silver_streaming
from data_collection.audit_logs_modules.event_mapping_replay import CompactPartitionOutcome
from shared_runtime_logging import suppress_inherited_provider_debug_logging


EVENT_MAPPING_PREFIX = "silver/category=event_mapping/"
MINIMUM_AGE_DAYS = 1
PHASE3_FILTERS = {
    "category": "event_mapping",
}
PHASE3_FILTER_SCOPE = "category=event_mapping"
OUTPUT_ROLE = "compact_silver_audit"
SELECTED_SCOPE_PREVIEW_LIMIT = 5
AUDIT_COLUMNS = [
    "run_ts_utc",
    "run_id",
    "record_type",
    "project_key",
    "source_folder_lookup",
    "source_folder_id",
    "connection_type",
    "connection_name",
    "execution_environment",
    "worker_resolution_source",
    "python_visible_cpu_count",
    "configured_cores",
    "filter_scope",
    "utc_cutoff_date",
    "parallel_enabled",
    "requested_workers",
    "partition_cap",
    "selection_mode",
    "eligible_partition_count",
    "processed_partition_count",
    "dispatch_batch_size",
    "selected_partition_count",
    "selected_partition_scope",
    "selected_day",
    "selected_days",
    "replay_mode",
    "terminal_status",
    "files_read",
    "raw_rows",
    "rows_after_drop_duplicates",
    "input_rows",
    "input_columns",
    "rehydrated_rows",
    "rehydrated_columns",
    "mapper_rows",
    "mapper_columns",
    "mapper_groups",
    "normalized_plan_count",
    "written_count",
    "verified_count",
    "deleted_count",
    "retained_count",
    "run_epoch_ms",
    "message",
]


def get_dss_execution_environment() -> str:
    if os.environ.get("DKU_CONTAINER_EXEC") == "1":
        return str(os.environ.get("DKU_CONTAINER_EXEC_NAME") or "container")
    return "local"


def _resolve_single_role_name(*, role_name: str, names: list[str], expected_kind: str) -> str:
    if len(names) != 1:
        raise ValueError(f"Expected exactly one {expected_kind} for role {role_name!r}, got {len(names)}")
    name = str(names[0]).strip()
    if not name:
        raise ValueError(f"Resolved empty {expected_kind} name for role {role_name!r}")
    return name


def _resolve_batch_size(param_set: dict[str, Any]) -> int:
    return int(param_set.get("batch_size", 25))


def _format_selected_partition_preview(selected_partitions: list[Any], *, total_selected_count: int | None = None) -> str | None:
    scopes = [str(getattr(item, "partition_scope", "")).strip() for item in selected_partitions]
    scopes = [scope for scope in scopes if scope]
    if not scopes:
        return None
    preview = scopes[:SELECTED_SCOPE_PREVIEW_LIMIT]
    total_count = max(len(scopes), int(total_selected_count or 0))
    if total_count <= SELECTED_SCOPE_PREVIEW_LIMIT:
        return ", ".join(preview)
    omitted = total_count - SELECTED_SCOPE_PREVIEW_LIMIT
    return f"{', '.join(preview)} ... (+{omitted} more)"


def _new_audit_row_base(
    *,
    project_key: str,
    folder_lookup: str,
    execution_environment: str,
    storage_ctx: Any,
    worker_resolution: Any,
    filter_scope: str,
    queue_summary: Any,
    selection_mode: str,
    processed_partition_count: int,
    dispatch_batch_size: int,
) -> dict[str, Any]:
    return {
        "project_key": project_key,
        "source_folder_lookup": folder_lookup,
        "source_folder_id": storage_ctx.folder_id,
        "connection_type": storage_ctx.connection_type,
        "connection_name": storage_ctx.connection_name,
        "execution_environment": execution_environment,
        "worker_resolution_source": worker_resolution.resolution_source,
        "python_visible_cpu_count": worker_resolution.python_visible_cpu_count,
        "configured_cores": worker_resolution.configured_cores,
        "filter_scope": filter_scope,
        "utc_cutoff_date": queue_summary.cutoff_date.isoformat(),
        "parallel_enabled": worker_resolution.parallel_enabled,
        "requested_workers": worker_resolution.resolved_n_jobs,
        "partition_cap": worker_resolution.partition_cap,
        "selection_mode": selection_mode,
        "eligible_partition_count": queue_summary.eligible_partition_count,
        "processed_partition_count": processed_partition_count,
        "dispatch_batch_size": dispatch_batch_size,
        "selected_partition_count": queue_summary.eligible_partition_count,
    }


def _build_partition_outcome_rows(
    *,
    run_started_at: str,
    project_key: str,
    folder_lookup: str,
    execution_environment: str,
    stream_result: CompactStreamRunResult,
    selected_partitions: list[Any],
    outcomes: list[CompactPartitionOutcome],
) -> list[dict[str, Any]]:
    partition_by_day = {
        (str(partition.year), str(partition.month), str(partition.day)): partition
        for partition in selected_partitions
    }
    base = _new_audit_row_base(
        project_key=project_key,
        folder_lookup=folder_lookup,
        execution_environment=execution_environment,
        storage_ctx=stream_result.storage_ctx,
        worker_resolution=stream_result.worker_resolution,
        filter_scope=PHASE3_FILTER_SCOPE,
        queue_summary=stream_result.queue_summary,
        selection_mode=stream_result.selection_mode,
        processed_partition_count=0,
        dispatch_batch_size=stream_result.dispatch_batch_size,
    )
    rows: list[dict[str, Any]] = []
    for outcome in outcomes:
        partition = partition_by_day[(str(outcome.year), str(outcome.month), str(outcome.day))]
        rows.append(
            {
                **base,
                "run_ts_utc": run_started_at,
                "run_id": run_started_at,
                "record_type": "partition_outcome",
                "selected_partition_scope": partition.partition_scope,
                "selected_day": outcome.day_scope,
                "selected_days": None,
                "replay_mode": outcome.replay_mode,
                "terminal_status": outcome.status,
                "files_read": outcome.files_read,
                "raw_rows": outcome.raw_rows,
                "rows_after_drop_duplicates": outcome.rows_after_drop_duplicates,
                "input_rows": outcome.input_rows,
                "input_columns": outcome.input_columns,
                "rehydrated_rows": outcome.rehydrated_rows,
                "rehydrated_columns": outcome.rehydrated_columns,
                "mapper_rows": outcome.mapper_rows,
                "mapper_columns": outcome.mapper_columns,
                "mapper_groups": outcome.mapper_groups,
                "normalized_plan_count": outcome.plan_count,
                "written_count": outcome.written_count,
                "verified_count": outcome.verified_count,
                "deleted_count": outcome.deleted_count,
                "retained_count": outcome.retained_count,
                "run_epoch_ms": outcome.run_epoch_ms,
                "message": outcome.message,
            }
        )
    return rows


def _build_summary_row(
    *,
    run_started_at: str,
    project_key: str,
    folder_lookup: str,
    execution_environment: str,
    stream_result: CompactStreamRunResult,
    normalize_silver_mode: bool,
    processed_partition_count: int,
    aggregate_counts: dict[str, int],
    preview_partitions: list[Any],
) -> dict[str, Any]:
    base = _new_audit_row_base(
        project_key=project_key,
        folder_lookup=folder_lookup,
        execution_environment=execution_environment,
        storage_ctx=stream_result.storage_ctx,
        worker_resolution=stream_result.worker_resolution,
        filter_scope=PHASE3_FILTER_SCOPE,
        queue_summary=stream_result.queue_summary,
        selection_mode=stream_result.selection_mode,
        processed_partition_count=processed_partition_count,
        dispatch_batch_size=stream_result.dispatch_batch_size,
    )
    return {
        **base,
        "run_ts_utc": run_started_at,
        "run_id": run_started_at,
        "record_type": "run_summary",
        "selected_partition_scope": None,
        "selected_day": None,
        "selected_days": _format_selected_partition_preview(
            preview_partitions,
            total_selected_count=max(
                stream_result.queue_summary.eligible_partition_count,
                processed_partition_count,
            ),
        ),
        "replay_mode": "event_mapping_replay" if normalize_silver_mode else "generic_compaction",
        "terminal_status": "success" if aggregate_counts["failed_outcomes"] == 0 else "partial",
        "files_read": None,
        "raw_rows": None,
        "rows_after_drop_duplicates": None,
        "input_rows": None,
        "input_columns": None,
        "rehydrated_rows": None,
        "rehydrated_columns": None,
        "mapper_rows": None,
        "mapper_columns": None,
        "mapper_groups": None,
        "normalized_plan_count": None,
        "written_count": aggregate_counts["written_count"],
        "verified_count": aggregate_counts["verified_count"],
        "deleted_count": aggregate_counts["deleted_count"],
        "retained_count": aggregate_counts["retained_count"],
        "run_epoch_ms": None,
        "message": (
            f"mode={stream_result.execution_mode}; scanned={stream_result.queue_summary.total_matched_paths}; "
            f"filtered={stream_result.queue_summary.filtered_matching_paths}; eligible={stream_result.queue_summary.eligible_paths}; "
            f"eligible_partitions={stream_result.queue_summary.eligible_partition_count}; skipped_compact_outputs={stream_result.queue_summary.skipped_compact_outputs}"
        ),
    }


def _rows_to_dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=AUDIT_COLUMNS)


def _open_audit_writer(dataset: Any, schema_df: pd.DataFrame):
    if hasattr(dataset, "write_schema_from_dataframe"):
        dataset.write_schema_from_dataframe(schema_df)
    elif hasattr(dataset, "write_with_schema"):
        dataset.write_with_schema(schema_df.iloc[0:0].copy())
    else:
        raise TypeError("Dataset does not support schema initialization for streamed audit output")

    if hasattr(dataset, "get_writer"):
        return dataset.get_writer()
    raise TypeError("Dataset does not support streamed writer access")


def _write_audit_batch(writer: Any, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    batch_df = _rows_to_dataframe(rows)
    if hasattr(writer, "write_dataframe"):
        writer.write_dataframe(batch_df)
        return
    if hasattr(writer, "write_row_dict"):
        for row in rows:
            writer.write_row_dict(row)
        return
    if hasattr(writer, "write_tuple"):
        for row in rows:
            writer.write_tuple(tuple(row.get(column) for column in AUDIT_COLUMNS))
        return
    raise TypeError("Dataset writer does not support streamed compact audit output")


def _stream_audit_batch(
    *,
    writer: Any,
    run_started_at: str,
    project_key: str,
    folder_lookup: str,
    execution_environment: str,
    stream_result: CompactStreamRunResult,
    selected_partitions: list[Any],
    outcomes: list[CompactPartitionOutcome],
    preview_partitions: list[Any],
    aggregate_counts: dict[str, int],
    failed_days: list[str],
) -> None:
    if len(preview_partitions) < SELECTED_SCOPE_PREVIEW_LIMIT:
        preview_partitions.extend(selected_partitions[: max(0, SELECTED_SCOPE_PREVIEW_LIMIT - len(preview_partitions))])
    aggregate_counts["processed_partition_count"] += len(outcomes)
    for outcome in outcomes:
        aggregate_counts["written_count"] += outcome.written_count
        aggregate_counts["verified_count"] += outcome.verified_count
        aggregate_counts["deleted_count"] += outcome.deleted_count
        aggregate_counts["retained_count"] += outcome.retained_count
        if outcome.status != "succeeded":
            aggregate_counts["failed_outcomes"] += 1
            failed_days.append(outcome.day_scope)
    _write_audit_batch(
        writer,
        _build_partition_outcome_rows(
            run_started_at=run_started_at,
            project_key=project_key,
            folder_lookup=folder_lookup,
            execution_environment=execution_environment,
            stream_result=stream_result,
            selected_partitions=selected_partitions,
            outcomes=outcomes,
        ),
    )


def run():
    suppress_inherited_provider_debug_logging()

    project_key = dataiku.default_project_key()
    plugin_config = get_plugin_config() or {}
    recipe_config = get_recipe_config() or {}
    param_set = plugin_config.get("pulse_primary", {}) or {}
    normalize_silver_mode = bool(recipe_config.get("normalize_silver", False))

    source_folder_lookup = str(param_set.get("pulse_partitioned_data") or "partitioned_data")
    audit_dataset_name = _resolve_single_role_name(
        role_name=OUTPUT_ROLE,
        names=get_output_names_for_role(OUTPUT_ROLE),
        expected_kind="dataset output",
    )
    execution_environment = get_dss_execution_environment()
    batch_size = _resolve_batch_size(param_set)

    dataset = dataiku.Dataset(audit_dataset_name)
    writer = _open_audit_writer(dataset, pd.DataFrame(columns=AUDIT_COLUMNS))
    run_started_at = datetime.now(timezone.utc).isoformat()
    preview_partitions: list[Any] = []
    aggregate_counts = {
        "processed_partition_count": 0,
        "written_count": 0,
        "verified_count": 0,
        "deleted_count": 0,
        "retained_count": 0,
        "failed_outcomes": 0,
    }
    failed_days: list[str] = []

    try:
        stream_result = run_compact_silver_streaming(
            CompactRunConfig(
                project_key=project_key,
                folder_lookup=source_folder_lookup,
                relative_prefix=EVENT_MAPPING_PREFIX,
                partition_filters=PHASE3_FILTERS,
                minimum_age_days=MINIMUM_AGE_DAYS,
                normalize_silver_mode=normalize_silver_mode,
                param_set=param_set,
                execution_environment=execution_environment,
                batch_size=batch_size,
                selection_mode="all_eligible_filtered",
            ),
            on_outcomes=lambda stream_result, selected_partitions, outcomes: _stream_audit_batch(
                writer=writer,
                run_started_at=run_started_at,
                project_key=project_key,
                folder_lookup=source_folder_lookup,
                execution_environment=execution_environment,
                stream_result=stream_result,
                selected_partitions=selected_partitions,
                outcomes=outcomes,
                preview_partitions=preview_partitions,
                aggregate_counts=aggregate_counts,
                failed_days=failed_days,
            ),
        )
        _write_audit_batch(
            writer,
            [
                _build_summary_row(
                    run_started_at=run_started_at,
                    project_key=project_key,
                    folder_lookup=source_folder_lookup,
                    execution_environment=execution_environment,
                    stream_result=stream_result,
                    normalize_silver_mode=normalize_silver_mode,
                    processed_partition_count=aggregate_counts["processed_partition_count"],
                    aggregate_counts=aggregate_counts,
                    preview_partitions=preview_partitions,
                )
            ],
        )
    finally:
        if hasattr(writer, "close"):
            writer.close()

    if failed_days:
        raise RuntimeError(f"Compact SILVER recipe completed with non-success partition outcomes: {', '.join(failed_days)}")

    return {
        "project_key": project_key,
        "source_folder_lookup": source_folder_lookup,
        "audit_dataset": audit_dataset_name,
        "selection_mode": stream_result.selection_mode,
        "eligible_partition_count": stream_result.queue_summary.eligible_partition_count,
        "processed_partition_count": aggregate_counts["processed_partition_count"],
        "execution_environment": execution_environment,
        "parallel_enabled": stream_result.worker_resolution.parallel_enabled,
        "requested_workers": stream_result.worker_resolution.resolved_n_jobs,
        "partition_cap": stream_result.worker_resolution.partition_cap,
        "dispatch_batch_size": stream_result.dispatch_batch_size,
        "normalize_silver": normalize_silver_mode,
        "connection_type": stream_result.storage_ctx.connection_type,
        "connection_name": stream_result.storage_ctx.connection_name,
    }


if __name__ == "__main__":
    run()
