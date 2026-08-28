from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import dataiku
from dataiku.customrecipe import (
    get_output_names_for_role,
    get_plugin_config,
    get_recipe_config,
)

from data_collection.audit_logs_modules.compact_silver_coordinator import CompactRunConfig, run_compact_silver
from shared_runtime_logging import suppress_inherited_provider_debug_logging


EVENT_MAPPING_PREFIX = "silver/category=event_mapping/"
MINIMUM_AGE_DAYS = 3
SELECTED_PARTITION_COUNT = 2
PHASE3_FILTERS = {
    "category": "event_mapping",
    "module": "administration",
    "instance_name": "mazzei_pulse",
}
PHASE3_FILTER_SCOPE = "category=event_mapping; module=administration; instance_name=mazzei_pulse"
OUTPUT_ROLE = "compact_silver_audit"


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


def _resolve_parallel_settings(param_set: dict[str, Any]) -> tuple[bool, int, int]:
    do_parallel = bool(param_set.get("do_parallel", True))
    default_cores = max((os.cpu_count() or 2) - 1, 1)
    safe_default_cores = min(default_cores, 4)
    n_jobs = max(1, int(param_set.get("cores", safe_default_cores)))
    batch_size = int(param_set.get("batch_size", 25))
    return do_parallel, n_jobs, batch_size


def _build_audit_dataframe(
    *,
    project_key: str,
    folder_lookup: str,
    execution_environment: str,
    run_result,
    normalize_silver_mode: bool,
    do_parallel: bool,
    n_jobs: int,
) -> pd.DataFrame:
    run_started_at = datetime.now(timezone.utc).isoformat()
    storage_ctx = run_result.storage_ctx
    selected_batch = run_result.selected_batch
    selected_days = ",".join(
        f"{item.year}/{item.month}/{item.day}" for item in selected_batch.selected_partitions
    )

    rows: list[dict[str, Any]] = [
        {
            "run_ts_utc": run_started_at,
            "run_id": run_started_at,
            "record_type": "run_summary",
            "project_key": project_key,
            "source_folder_lookup": folder_lookup,
            "source_folder_id": storage_ctx.folder_id,
            "connection_type": storage_ctx.connection_type,
            "connection_name": storage_ctx.connection_name,
            "execution_environment": execution_environment,
            "filter_scope": PHASE3_FILTER_SCOPE,
            "utc_cutoff_date": selected_batch.cutoff_date.isoformat(),
            "parallel_enabled": do_parallel,
            "requested_workers": n_jobs,
            "selected_partition_count": len(selected_batch.selected_partitions),
            "selected_day": None,
            "selected_days": selected_days,
            "replay_mode": "event_mapping_replay" if normalize_silver_mode else "generic_compaction",
            "terminal_status": "success" if all(outcome.status == "succeeded" for outcome in run_result.outcomes) else "partial",
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
            "written_count": sum(outcome.written_count for outcome in run_result.outcomes),
            "verified_count": sum(outcome.verified_count for outcome in run_result.outcomes),
            "deleted_count": sum(outcome.deleted_count for outcome in run_result.outcomes),
            "retained_count": sum(outcome.retained_count for outcome in run_result.outcomes),
            "run_epoch_ms": None,
            "message": (
                f"mode={run_result.execution_mode}; scanned={selected_batch.total_matched_paths}; "
                f"filtered={selected_batch.filtered_matching_paths}; eligible={selected_batch.eligible_paths}; "
                f"skipped_compact_outputs={selected_batch.skipped_compact_outputs}"
            ),
        }
    ]

    for outcome in run_result.outcomes:
        rows.append(
            {
                "run_ts_utc": run_started_at,
                "run_id": run_started_at,
                "record_type": "partition_outcome",
                "project_key": project_key,
                "source_folder_lookup": folder_lookup,
                "source_folder_id": storage_ctx.folder_id,
                "connection_type": storage_ctx.connection_type,
                "connection_name": storage_ctx.connection_name,
                "execution_environment": execution_environment,
                "filter_scope": PHASE3_FILTER_SCOPE,
                "utc_cutoff_date": selected_batch.cutoff_date.isoformat(),
                "parallel_enabled": do_parallel,
                "requested_workers": n_jobs,
                "selected_partition_count": len(selected_batch.selected_partitions),
                "selected_day": outcome.day_scope,
                "selected_days": selected_days,
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

    columns = [
        "run_ts_utc",
        "run_id",
        "record_type",
        "project_key",
        "source_folder_lookup",
        "source_folder_id",
        "connection_type",
        "connection_name",
        "execution_environment",
        "filter_scope",
        "utc_cutoff_date",
        "parallel_enabled",
        "requested_workers",
        "selected_partition_count",
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
    return pd.DataFrame(rows, columns=columns)


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
    do_parallel, n_jobs, batch_size = _resolve_parallel_settings(param_set)

    run_result = run_compact_silver(
        CompactRunConfig(
            project_key=project_key,
            folder_lookup=source_folder_lookup,
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
    audit_df = _build_audit_dataframe(
        project_key=project_key,
        folder_lookup=source_folder_lookup,
        execution_environment=get_dss_execution_environment(),
        run_result=run_result,
        normalize_silver_mode=normalize_silver_mode,
        do_parallel=do_parallel,
        n_jobs=n_jobs,
    )
    dataiku.Dataset(audit_dataset_name).write_with_schema(audit_df)

    failed_days = [outcome.day_scope for outcome in run_result.outcomes if outcome.status != "succeeded"]
    if failed_days:
        raise RuntimeError(f"Compact SILVER recipe completed with non-success partition outcomes: {', '.join(failed_days)}")

    return {
        "project_key": project_key,
        "source_folder_lookup": source_folder_lookup,
        "audit_dataset": audit_dataset_name,
        "selected_partition_count": len(run_result.selected_batch.selected_partitions),
        "execution_environment": get_dss_execution_environment(),
        "parallel_enabled": do_parallel,
        "requested_workers": n_jobs,
        "normalize_silver": normalize_silver_mode,
        "connection_type": run_result.storage_ctx.connection_type,
        "connection_name": run_result.storage_ctx.connection_name,
    }


if __name__ == "__main__":
    run()
