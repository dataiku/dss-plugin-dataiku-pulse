from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import os

import pandas as pd
from pathlib import Path
from typing import Any, Dict, List, Optional

from dataikuapi.dss.project import DSSProject

from .introspection import get_noarg_list_methods

from data_collection.data_normalizer import check_silver_dq, normalize_silver
from data_collection.exclusion_config import load_exclusions
from data_collection.helper import (
    DSSFolderTarget,
    OutputLayout,
    build_error_row,
    find_timestamp_column,
    raw_to_dataframe,
    upload_json,
    upload_json_gzip,
    upload_parquet,
)


@dataclass(frozen=True)
class CollectResult:
    project_key: str
    collected: List[str]
    errors: Dict[str, str]


def collect_project_list_methods(
    *,
    project: DSSProject,
    project_key: str,
    output_base_dir: Path,
    instance_name: str,
    run_ts: str,
    run_date: date,
    since: datetime | None = None,
    output_folder_target: DSSFolderTarget = DSSFolderTarget(project_key="DATA_COLLECTION"),
) -> CollectResult:
    """Collect all no-arg list_* outputs for a project handle."""

    layout = OutputLayout(base_dir=output_base_dir, module="project_metadata")
    methods = get_noarg_list_methods(project)
    excluded = set(load_exclusions("projects_data").excluded_methods)

    collected: List[str] = []
    errors: Dict[str, str] = {}

    for method_name, fn in sorted(methods.items()):
        if method_name in excluded:
            continue
        prefix = f"{layout.prefix_base(method_name)}_"

        raw_path = layout.project_data_path(
            "raw",
            method_name,
            instance_name,
            run_date,
            project_key,
            "json.gz",
        )
        raw_error_path = layout.project_data_path(
            "raw_errors",
            method_name,
            instance_name,
            run_date,
            project_key,
            "json",
        )
        silver_path = layout.project_data_path(
            "silver",
            method_name,
            instance_name,
            run_date,
            project_key,
            "parquet",
        )
        silver_fail_path = layout.project_data_path(
            "silver_fail",
            method_name,
            instance_name,
            run_date,
            project_key,
            "parquet",
        )
        silver_fail_reason_path = layout.project_data_path(
            "silver_fail",
            method_name,
            instance_name,
            run_date,
            project_key,
            "dq.json",
        )

        try:
            payload = fn()

            # If list_* returns an empty payload, skip without writing anything.
            if payload is None:
                continue
            if isinstance(payload, (list, tuple, set)) and len(payload) == 0:
                continue
            if isinstance(payload, dict) and len(payload) == 0:
                continue

            raw_df = raw_to_dataframe(payload, prefix=prefix)
            if raw_df.shape[0] == 0:
                # Nothing to persist.
                continue

            # Apply row-level delta filtering when a timestamp column is available.
            # If filtering produces no rows, treat it as "no change" and skip writing.
            filtered_payload = payload
            if since is not None:
                from data_collection.helper import filter_payload_by_delta

                maybe_filtered = filter_payload_by_delta(payload=payload, raw_df=raw_df, since=since)
                if maybe_filtered is not None:
                    # Delta filtering was applied.
                    if isinstance(maybe_filtered, list) and len(maybe_filtered) == 0:
                        continue
                    filtered_payload = maybe_filtered
                else:
                    # No timestamp columns detected: capture a small sample so we can
                    # review and potentially improve the heuristic.
                    sample_path = layout.project_data_path(
                        "raw_errors",
                        method_name,
                        instance_name,
                        run_date,
                        project_key,
                        "missing_timestamps.json",
                    )
                    upload_json(
                        target=output_folder_target,
                        output_path=sample_path,
                        output_base_dir=output_base_dir,
                        payload={
                            "method_name": method_name,
                            "columns": list(raw_df.columns),
                            "rows": int(raw_df.shape[0]),
                            "sample": raw_df.head(50).to_dict("records"),
                        },
                    )

            # RAW: dump the API payload as compressed JSON.
            upload_json_gzip(
                target=output_folder_target,
                output_path=raw_path,
                output_base_dir=output_base_dir,
                payload=filtered_payload,
            )

            # SILVER: normalize + write typed parquet.
            category = layout.category_name(method_name)
            silver_df = normalize_silver(
                df=raw_df,
                instance_name=instance_name,
                run_ts=run_ts,
                category=category,
                module=layout.module,
            )
            dq = check_silver_dq(silver_df)
            if dq.ok:
                upload_parquet(
                    target=output_folder_target,
                    output_path=silver_path,
                    output_base_dir=output_base_dir,
                    df=silver_df,
                    compression="snappy",
                )
            else:
                upload_parquet(
                    target=output_folder_target,
                    output_path=silver_fail_path,
                    output_base_dir=output_base_dir,
                    df=silver_df,
                    compression="snappy",
                )
                upload_json(
                    target=output_folder_target,
                    output_path=silver_fail_reason_path,
                    output_base_dir=output_base_dir,
                    payload={
                        "instance_name": instance_name,
                        "project_key": project_key,
                        "run_ts": run_ts,
                        "method_name": method_name,
                        "rows": int(silver_df.shape[0]),
                        "cols": int(silver_df.shape[1]),
                        "dq_errors": dq.errors,
                    },
                )

            collected.append(method_name)
        except Exception as e:
            errors[method_name] = repr(e)

            err_df = build_error_row(
                error=e,
                instance_name=instance_name,
                project_key=project_key,
                run_ts=run_ts,
            )

            # Persist errors.
            upload_json(
                target=output_folder_target,
                output_path=raw_error_path,
                output_base_dir=output_base_dir,
                payload={
                    "instance_name": instance_name,
                    "project_key": project_key,
                    "run_ts": run_ts,
                    "method_name": method_name,
                    "error": repr(e),
                },
            )
            upload_parquet(
                target=output_folder_target,
                output_path=silver_path,
                output_base_dir=output_base_dir,
                df=err_df,
                write_empty=True,
                compression="snappy",
            )

    return CollectResult(project_key=project_key, collected=collected, errors=errors)
