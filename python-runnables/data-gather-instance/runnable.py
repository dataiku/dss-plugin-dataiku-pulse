# This file is the actual code for the Python runnable data-gather-instance
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import dataiku
import pandas as pd
from dataiku.runnables import ResultTable, Runnable
from data_collection.helper import PulseMacroContext, build_context

from data_collection.data_collection.instance import get_instance_name
from data_collection.data_collection.introspection import get_noarg_list_methods
from data_collection.data_normalizer import check_silver_dq, normalize_silver
from data_collection.exclusion_config import load_exclusions, load_inclusions
from data_collection.helper import (
    DSSFolderTarget,
    OutputLayout,
    ensure_managed_folder,
    raw_to_dataframe,
    upload_json,
    upload_json_gzip,
    upload_parquet,
)



@dataclass(frozen=True)
class CollectInstanceResult:
    collected: List[str]
    errors: Dict[str, str]


class MyRunnable(Runnable):
    """Gather instance-level metadata from `client.list_*` methods."""

    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config or {}
        self.plugin_config = plugin_config or {}

        # DSS macros expose a single parameter set; we use `pulse_primary` as the
        # canonical container for all plugin settings.
        self.param_set = self.plugin_config.get("pulse_primary", {}) or {}

        # Remote output target (can be same DSS instance).
        self.output_project_key = self.param_set.get("pulse_project_key", "DATA_COLLECTION")
        self.output_folder_lookup = self.param_set.get("pulse_partitioned_data", "partitioned_data")
        self.output_connection_name = self.param_set.get("pulse_folder_connection")

    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        ctx: PulseMacroContext = build_context(plugin_config=self.plugin_config)
        self.param_set = ctx.param_set

        instance_name = get_instance_name(ctx.local_client)
        if not instance_name:
            raise ValueError("Could not determine instance_name (nodeId/installId)")

        run_dt = datetime.now(timezone.utc)
        run_ts = run_dt.isoformat()
        run_date = run_dt.date()

        layout = OutputLayout(base_dir=Path("partitioned_data"), module="instance_metadata")
        methods = get_noarg_list_methods(ctx.local_client)
        excluded = set(load_exclusions("instance_data").excluded_methods)

        # Project-level methods that are known to be project-invariant and only
        # need to be collected once.
        project_inclusions = load_inclusions("instance_project_inclusion.yaml")
        worker_project_key = self.param_set.get("pulse_worker_key")

        target = DSSFolderTarget(
            project_key=self.output_project_key,
            folder_lookup=self.output_folder_lookup,
            connection_name=self.output_connection_name,
            client=ctx.remote_client,
        )

        # Ensure output folder exists before writing.
        ensure_managed_folder(
            project_key=target.project_key,
            folder_lookup=target.folder_lookup,
            connection_name=target.connection_name,
            client=ctx.remote_client,
        )

        collected: List[str] = []
        errors: Dict[str, str] = {}

        if progress_callback is not None:
            progress_callback(0)

        # For client-level objects, there is no per-project key.
        file_key = "instance"

        for method_name, fn in sorted(methods.items()):
            if method_name in excluded:
                continue
            category = layout.category_name(method_name)
            prefix = f"{layout.prefix_base(method_name)}_"

            raw_path = layout.project_data_path(
                "raw",
                method_name,
                instance_name,
                run_date,
                file_key,
                "json.gz",
            )
            raw_error_path = layout.project_data_path(
                "raw_errors",
                method_name,
                instance_name,
                run_date,
                file_key,
                "json",
            )
            silver_path = layout.project_data_path(
                "silver",
                method_name,
                instance_name,
                run_date,
                file_key,
                "parquet",
            )
            silver_fail_path = layout.project_data_path(
                "silver_fail",
                method_name,
                instance_name,
                run_date,
                file_key,
                "parquet",
            )
            silver_fail_reason_path = layout.project_data_path(
                "silver_fail",
                method_name,
                instance_name,
                run_date,
                file_key,
                "dq.json",
            )

            try:
                payload = fn()

                if payload is None:
                    continue
                if isinstance(payload, (list, tuple, set)) and len(payload) == 0:
                    continue
                if isinstance(payload, dict) and len(payload) == 0:
                    continue

                raw_df = raw_to_dataframe(payload, prefix=prefix)
                if raw_df.shape[0] == 0:
                    continue

                upload_json_gzip(
                    target=target,
                    output_path=raw_path,
                    output_base_dir=layout.base_dir,
                    payload=payload,
                )

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
                        target=target,
                        output_path=silver_path,
                        output_base_dir=layout.base_dir,
                        df=silver_df,
                        compression="snappy",
                    )
                else:
                    upload_parquet(
                        target=target,
                        output_path=silver_fail_path,
                        output_base_dir=layout.base_dir,
                        df=silver_df,
                        compression="snappy",
                    )
                    upload_json(
                        target=target,
                        output_path=silver_fail_reason_path,
                        output_base_dir=layout.base_dir,
                        payload={
                            "instance_name": instance_name,
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
                upload_json(
                    target=target,
                    output_path=raw_error_path,
                    output_base_dir=layout.base_dir,
                    payload={
                        "instance_name": instance_name,
                        "run_ts": run_ts,
                        "method_name": method_name,
                        "error": repr(e),
                    },
                )

        # Run project-level inclusions once, against a single worker project.
        included_collected: List[str] = []
        included_errors: Dict[str, str] = {}
        if worker_project_key and project_inclusions:
            try:
                worker_project = ctx.local_client.get_project(worker_project_key)
                project_methods = get_noarg_list_methods(worker_project)

                for method_name in project_inclusions:
                    fn = project_methods.get(method_name)
                    if fn is None:
                        included_errors[method_name] = "method_not_found"
                        continue

                    category = layout.category_name(method_name)
                    prefix = f"{layout.prefix_base(method_name)}_"

                    raw_path = layout.project_data_path(
                        "raw",
                        method_name,
                        instance_name,
                        run_date,
                        worker_project_key,
                        "json.gz",
                    )
                    raw_error_path = layout.project_data_path(
                        "raw_errors",
                        method_name,
                        instance_name,
                        run_date,
                        worker_project_key,
                        "json",
                    )
                    silver_path = layout.project_data_path(
                        "silver",
                        method_name,
                        instance_name,
                        run_date,
                        worker_project_key,
                        "parquet",
                    )
                    silver_fail_path = layout.project_data_path(
                        "silver_fail",
                        method_name,
                        instance_name,
                        run_date,
                        worker_project_key,
                        "parquet",
                    )
                    silver_fail_reason_path = layout.project_data_path(
                        "silver_fail",
                        method_name,
                        instance_name,
                        run_date,
                        worker_project_key,
                        "dq.json",
                    )

                    try:
                        payload = fn()
                        if payload is None:
                            continue
                        if isinstance(payload, (list, tuple, set)) and len(payload) == 0:
                            continue
                        if isinstance(payload, dict) and len(payload) == 0:
                            continue

                        raw_df = raw_to_dataframe(payload, prefix=prefix)
                        if raw_df.shape[0] == 0:
                            continue

                        upload_json_gzip(
                            target=target,
                            output_path=raw_path,
                            output_base_dir=layout.base_dir,
                            payload=payload,
                        )

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
                                target=target,
                                output_path=silver_path,
                                output_base_dir=layout.base_dir,
                                df=silver_df,
                                compression="snappy",
                            )
                        else:
                            upload_parquet(
                                target=target,
                                output_path=silver_fail_path,
                                output_base_dir=layout.base_dir,
                                df=silver_df,
                                compression="snappy",
                            )
                            upload_json(
                                target=target,
                                output_path=silver_fail_reason_path,
                                output_base_dir=layout.base_dir,
                                payload={
                                    "instance_name": instance_name,
                                    "run_ts": run_ts,
                                    "method_name": method_name,
                                    "worker_project_key": worker_project_key,
                                    "rows": int(silver_df.shape[0]),
                                    "cols": int(silver_df.shape[1]),
                                    "dq_errors": dq.errors,
                                },
                            )

                        included_collected.append(method_name)
                    except Exception as e:
                        included_errors[method_name] = repr(e)
                        upload_json(
                            target=target,
                            output_path=raw_error_path,
                            output_base_dir=layout.base_dir,
                            payload={
                                "instance_name": instance_name,
                                "run_ts": run_ts,
                                "method_name": method_name,
                                "error": repr(e),
                                "worker_project_key": worker_project_key,
                            },
                        )
            except Exception as e:
                included_errors["__worker_project__"] = repr(e)

        if progress_callback is not None:
            progress_callback(len(collected) + len(included_collected))

        # Summary table
        summary_rows = [
            {"metric": "list_methods_total", "value": len(methods)},
            {"metric": "list_methods_collected", "value": len(collected)},
            {"metric": "list_methods_failed", "value": len(errors)},
            {"metric": "project_inclusions_total", "value": len(project_inclusions)},
            {"metric": "project_inclusions_collected", "value": len(included_collected)},
            {"metric": "project_inclusions_failed", "value": len(included_errors)},
        ]

        for method_name, err in sorted(included_errors.items()):
            summary_rows.append(
                {
                    "metric": "project_inclusion_error",
                    "value": f"{method_name}: {err}",
                }
            )

        summary = pd.DataFrame(summary_rows)

        rt = ResultTable()
        rt.add_column(1, "metric", "STRING")
        rt.add_column(2, "value", "STRING")
        for _, row in summary.astype(str).iterrows():
            rt.add_record([row["metric"], row["value"]])

        return rt
