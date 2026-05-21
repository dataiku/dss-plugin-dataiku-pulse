from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from dataiku.runnables import ResultTable, Runnable

from data_collection.data_collection.instance import get_instance_name
from data_collection.data_collection.introspection import get_noarg_list_methods
from data_collection.exclusion_config import load_exclusions, load_inclusions
from data_collection.helper import (
    OutputLayout,
    PulseMacroContext,
    build_context,
    ensure_output_folder,
    resolve_worker_project_key,
)
from data_collection.method_rules import MethodCallContext, MethodCollectResult, collect_method_output

logger = logging.getLogger(__name__)


class MyRunnable(Runnable):
    """Gather instance-level metadata from DSS methods with centralized rules."""

    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config or {}
        self.plugin_config = plugin_config or {}
        self.param_set = self.plugin_config.get("pulse_primary", {}) or {}

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
        project_inclusions = load_inclusions("instance_project_inclusion.yaml")
        worker_project_key = str(
            self.param_set.get("pulse_worker_key")
            or resolve_worker_project_key(ctx.local_client, fallback_project_key=self.project_key)
        )
        target = ensure_output_folder(param_set=self.param_set, remote_client=ctx.remote_client)

        if progress_callback is not None:
            progress_callback(0)

        method_results: list[MethodCollectResult] = []
        total_work = len(methods) + len(project_inclusions)
        completed = 0

        for method_name, fn in sorted(methods.items()):
            if method_name in excluded:
                method_results.append(MethodCollectResult(method_name, "instance", "excluded", 0, 0, 0))
            else:
                method_results.append(
                    collect_method_output(
                        fn=fn,
                        method_name=method_name,
                        file_key="instance",
                        layout=layout,
                        target=target,
                        context=MethodCallContext(
                            scope="instance",
                            instance_name=instance_name,
                            run_ts=run_ts,
                            param_set=self.param_set,
                            worker_project_key=worker_project_key,
                        ),
                        run_date=run_date,
                        todo_section="instance",
                    )
                )
            completed += 1
            if progress_callback is not None and total_work > 0:
                progress_callback(completed / total_work)

        project_methods = {}
        try:
            worker_project = ctx.local_client.get_project(worker_project_key)
            project_methods = get_noarg_list_methods(worker_project)
        except Exception as exc:
            logger.exception("Failed to resolve worker project %s", worker_project_key)
            method_results.append(MethodCollectResult("__worker_project__", "project_inclusion", "call_failed", 0, 0, 0, message=repr(exc)))

        for method_name in project_inclusions:
            fn = project_methods.get(method_name)
            if fn is None:
                method_results.append(MethodCollectResult(method_name, "project_inclusion", "method_not_found", 0, 0, 0))
            else:
                method_results.append(
                    collect_method_output(
                        fn=fn,
                        method_name=method_name,
                        file_key=worker_project_key,
                        layout=layout,
                        target=target,
                        context=MethodCallContext(
                            scope="instance",
                            instance_name=instance_name,
                            run_ts=run_ts,
                            param_set=self.param_set,
                            project_key=worker_project_key,
                            worker_project_key=worker_project_key,
                        ),
                        run_date=run_date,
                        todo_section="instance",
                    )
                )
            completed += 1
            if progress_callback is not None and total_work > 0:
                progress_callback(completed / total_work)

        status_counts: dict[str, int] = {}
        for item in method_results:
            status_counts[item.status] = status_counts.get(item.status, 0) + 1

        rt = ResultTable()
        rt.add_column(1, "metric", "STRING")
        rt.add_column(2, "value", "STRING")
        rt.add_column(3, "scope", "STRING")
        rt.add_column(4, "status", "STRING")
        rt.add_column(5, "details", "STRING")

        for row in [
            ("list_methods_total", str(len(methods)), "summary", "info", ""),
            ("project_inclusions_total", str(len(project_inclusions)), "summary", "info", ""),
            ("excluded_total", str(status_counts.get("excluded", 0)), "summary", "info", ""),
            ("excluded_by_rule_total", str(status_counts.get("excluded_by_rule", 0)), "summary", "info", ""),
            ("silver_written_total", str(status_counts.get("silver_written", 0)), "summary", "info", ""),
            ("silver_failed_dq_total", str(status_counts.get("silver_failed_dq", 0)), "summary", "info", ""),
            ("empty_payload_total", str(status_counts.get("empty_payload", 0)), "summary", "info", ""),
            ("empty_dataframe_total", str(status_counts.get("empty_dataframe", 0)), "summary", "info", ""),
            ("call_failed_total", str(status_counts.get("call_failed", 0)), "summary", "info", ""),
            ("method_not_found_total", str(status_counts.get("method_not_found", 0)), "summary", "info", ""),
            ("needs_rule_total", str(status_counts.get("needs_rule", 0)), "summary", "info", ""),
        ]:
            rt.add_record(list(row))

        for item in method_results:
            rt.add_record([
                item.method_name,
                str(item.rows_silver if item.rows_silver else item.rows_raw),
                item.scope,
                item.status,
                f"rule={item.rule_mode}; duration_ms={item.duration_ms}; rows_raw={item.rows_raw}; rows_silver={item.rows_silver}; message={item.message or ''}",
            ])

        return rt
