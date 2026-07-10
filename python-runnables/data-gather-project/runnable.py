from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd
from dateutil.relativedelta import relativedelta
from dataiku.runnables import ResultTable, Runnable

from data_collection.data_collection.collect_all_projects import collect_all_projects
from data_collection.helper import (
    CursorSpec,
    PulseMacroContext,
    build_context,
    ensure_output_folder,
    resolve_cursor_ts,
    resolve_worker_project_key,
    update_cursor_ts,
)


logger = logging.getLogger(__name__)


class MyRunnable(Runnable):
    """Project metadata collection runnable."""

    def __init__(self, project_key, config, plugin_config):
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

    def _resolve_projects_default_ts(self) -> pd.Timestamp:
        fallback_dt = pd.Timestamp.now(tz="UTC") - relativedelta(months=3)
        default_raw = self.param_set.get("pulse_default_projects_delta")
        if not default_raw:
            return fallback_dt
        default_dt = pd.to_datetime(default_raw, utc=True, errors="coerce")
        if pd.isna(default_dt):
            return fallback_dt
        return default_dt

    def _read_projects_delta(self, client: Any) -> pd.Timestamp:
        worker_project_key = resolve_worker_project_key(client, fallback_project_key=self.project_key)
        return resolve_cursor_ts(
            client=client,
            project_key=worker_project_key,
            param_set=self.param_set,
            spec=CursorSpec(variable_name="projects_delta", debug_key="pulse_projects_delta_debug"),
            default_ts=self._resolve_projects_default_ts(),
            local_mode=False,
        )

    def _update_projects_delta(self, client: Any, value: str) -> None:
        worker_project_key = resolve_worker_project_key(client, fallback_project_key=self.project_key)
        update_cursor_ts(
            client=client,
            project_key=worker_project_key,
            spec=CursorSpec(variable_name="projects_delta"),
            value=value,
            enabled=True,
        )

    def _resolve_project_keys(self, client: Any, *, since: pd.Timestamp) -> list[str]:
        projects = client.list_projects()
        if not projects:
            return []

        df = pd.json_normalize(projects)
        floor_dt = pd.Timestamp("2015-01-01", tz="UTC")

        from data_collection.data_normalizer.casting import _detect_epoch_unit

        for col in ["versionTag.lastModifiedOn", "creationTag.lastModifiedOn"]:
            if col not in df.columns:
                continue
            series = df[col]
            if pd.api.types.is_numeric_dtype(series):
                unit = _detect_epoch_unit(series)
                dt = pd.to_datetime(series, unit=unit, utc=True, errors="coerce")
            else:
                dt = pd.to_datetime(series, utc=True, errors="coerce")
                if dt.notna().sum() == 0 and series.notna().sum() > 0:
                    unit = _detect_epoch_unit(series)
                    dt = pd.to_datetime(pd.to_numeric(series, errors="coerce"), unit=unit, utc=True, errors="coerce")
            dt = dt.dt.floor("s")
            dt = dt.mask(dt < floor_dt, floor_dt)
            df[col] = dt

        if "versionTag.lastModifiedOn" in df.columns and "creationTag.lastModifiedOn" in df.columns:
            df["_lastModifiedOn"] = df["versionTag.lastModifiedOn"].fillna(df["creationTag.lastModifiedOn"])
        elif "versionTag.lastModifiedOn" in df.columns:
            df["_lastModifiedOn"] = df["versionTag.lastModifiedOn"]
        elif "creationTag.lastModifiedOn" in df.columns:
            df["_lastModifiedOn"] = df["creationTag.lastModifiedOn"]
        else:
            df["_lastModifiedOn"] = pd.NaT

        keys: list[str] = []
        for _, row in df.iterrows():
            last_modified = row.get("_lastModifiedOn")
            project_key = row.get("projectKey")
            if not project_key:
                continue
            if pd.isna(last_modified) or pd.Timestamp(last_modified) >= since:
                keys.append(str(project_key))
        return keys

    def run(self, progress_callback):
        run_started_at = pd.Timestamp.now(tz="UTC").floor("s")
        ctx: PulseMacroContext = build_context(plugin_config=self.plugin_config)
        self.param_set = ctx.param_set

        target = ensure_output_folder(param_set=self.param_set, remote_client=ctx.remote_client)
        since = self._read_projects_delta(ctx.local_client)
        project_keys = self._resolve_project_keys(ctx.local_client, since=since)

        debug_dir = None
        if bool(self.param_set.get("pulse_projects_debug_missing_timestamps", False)):
            debug_dir = Path(tempfile.gettempdir()) / "pulse-project-debug"

        result = collect_all_projects(
            client=ctx.local_client,
            output_base_dir=Path("partitioned_data"),
            project_keys=project_keys,
            since=since.to_pydatetime(),
            debug_dir=debug_dir,
            n_jobs=(self.n_jobs if self.do_parallel else 1),
            batch_size=self.batch_size,
            output_folder_target=target,
        )

        has_project_errors = any(project_result.errors for project_result in result.per_project.values())
        if not has_project_errors:
            self._update_projects_delta(ctx.local_client, run_started_at.isoformat())
        else:
            logger.warning("Skipping projects cursor update because project collection reported errors")

        status_counts: dict[str, int] = {}
        total_methods = 0
        total_errors = 0
        for project_result in result.per_project.values():
            total_errors += len(project_result.errors)
            for item in project_result.method_results:
                total_methods += 1
                status_counts[item.status] = status_counts.get(item.status, 0) + 1

        rt = ResultTable()
        rt.add_column(1, "metric", "STRING")
        rt.add_column(2, "value", "STRING")
        rt.add_column(3, "scope", "STRING")
        rt.add_column(4, "status", "STRING")
        rt.add_column(5, "details", "STRING")

        for row in [
            ("projects_selected", str(len(project_keys)), "summary", "info", ""),
            ("projects_collected", str(len(result.collected_projects)), "summary", "info", ""),
            ("project_method_results", str(total_methods), "summary", "info", ""),
            ("project_errors_total", str(total_errors), "summary", "info", ""),
            ("silver_written_total", str(status_counts.get("silver_written", 0)), "summary", "info", ""),
            ("silver_failed_dq_total", str(status_counts.get("silver_failed_dq", 0)), "summary", "info", ""),
            ("filtered_by_delta_total", str(status_counts.get("filtered_by_delta", 0)), "summary", "info", ""),
            ("empty_payload_total", str(status_counts.get("empty_payload", 0)), "summary", "info", ""),
            ("empty_dataframe_total", str(status_counts.get("empty_dataframe", 0)), "summary", "info", ""),
            ("excluded_total", str(status_counts.get("excluded", 0)), "summary", "info", ""),
            ("excluded_by_rule_total", str(status_counts.get("excluded_by_rule", 0)), "summary", "info", ""),
            ("call_failed_total", str(status_counts.get("call_failed", 0)), "summary", "info", ""),
            ("needs_rule_total", str(status_counts.get("needs_rule", 0)), "summary", "info", ""),
        ]:
            rt.add_record(list(row))

        for project_key, project_result in sorted(result.per_project.items()):
            rt.add_record([
                project_key,
                str(len(project_result.collected)),
                "project",
                "project_summary",
                f"errors={len(project_result.errors)}",
            ])

        return rt
