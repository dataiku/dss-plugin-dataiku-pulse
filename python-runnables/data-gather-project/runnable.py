# This file is the actual code for the Python runnable data-gather-project
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

import dataiku
import pandas as pd
from dataiku.runnables import ResultTable, Runnable

from data_collection.data_collection.collect_all_projects import collect_all_projects
from data_collection.helper import DSSFolderTarget, ensure_managed_folder


class MyRunnable(Runnable):
    """Data Collection macro runnable."""

    def __init__(self, project_key, config, plugin_config):
        """Initialize runnable.

        :param project_key: the project in which the runnable executes
        :param config: the dict of the configuration of the object
        :param plugin_config: contains the plugin settings
        """

        self.project_key = project_key
        self.config = config or {}
        self.plugin_config = plugin_config or {}

        # Output target (managed folder) is controlled by plugin settings.
        self.output_project_key = self.plugin_config.get("pulse_project_key", "DATA_COLLECTION")
        self.output_folder_lookup = self.plugin_config.get(
            "pulse_partitioned_data",
            "partitioned_data",
        )
        self.output_connection_name = self.plugin_config.get("pulse_folder_connection")

        # Hub/spoke: optionally upload to a remote DSS instance.
        self.output_project_url = self.plugin_config.get("pulse_project_url")
        self.output_project_api = self.plugin_config.get("pulse_project_api")

        # Parallelism is controlled by plugin settings.
        # If `cores` is not set, default to local CPUs - 1.
        self.do_parallel = bool(self.plugin_config.get("do_parallel", True))

        default_cores = max((os.cpu_count() or 2) - 1, 1)
        self.n_jobs = int(self.plugin_config.get("cores", default_cores))
        self.batch_size = int(self.plugin_config.get("batch_size", 25))



    def get_progress_target(self):
        return None

    def _read_projects_delta(self, client: Any) -> pd.Timestamp:
        """Return the timestamp cursor used to filter projects.

        - Reads `projects_delta` from local project variables if present
        - Falls back to plugin_config `pulse_default_projects_delta`
        - If plugin_config `pulse_projects_delta_debug` is true, always use default
        """

        default_raw = self.plugin_config.get(
            "pulse_default_projects_delta",
            "2026-01-01 00:00:00.000000",
        )
        default_dt = pd.to_datetime(default_raw, utc=True, errors="coerce")
        if pd.isna(default_dt):
            default_dt = pd.Timestamp("2026-01-01", tz="UTC")

        if bool(self.plugin_config.get("pulse_projects_delta_debug", False)):
            return default_dt

        try:
            project = client.get_project(self.project_key)
            variables = project.get_variables()
            raw = variables.get("local", {}).get("projects_delta")
            if raw:
                dt = pd.to_datetime(raw, utc=True, errors="coerce")
                if not pd.isna(dt):
                    return dt
        except Exception:
            # Best-effort; fall back to default.
            pass

        return default_dt

    def _update_projects_delta(self, client: Any, value: str) -> None:
        """Best-effort update of local project variable `projects_delta`."""

        try:
            project = client.get_project(self.project_key)
            variables = project.get_variables()
            local = variables.get("local") or {}
            local["projects_delta"] = value
            variables["local"] = local
            project.set_variables(variables)
        except Exception:
            # Best-effort; do not fail the macro.
            pass

    @staticmethod
    def _extract_last_modified_on(project: Dict[str, Any]) -> Any:
        version_tag = project.get("versionTag") or {}
        creation_tag = project.get("creationTag") or {}
        return version_tag.get("lastModifiedOn") or creation_tag.get("lastModifiedOn")

    def _resolve_project_keys(self, client: Any, *, since: pd.Timestamp) -> List[str]:
        projects = client.list_projects()
        if not projects:
            return []

        df = pd.json_normalize(projects)

        # Normalize expected timestamp columns
        floor_dt = pd.Timestamp("2015-01-01", tz="UTC")

        from data_collection.data_normalizer.casting import _detect_epoch_unit

        for col in ["versionTag.lastModifiedOn", "creationTag.lastModifiedOn"]:
            if col not in df.columns:
                continue

            series = df[col]

            # If values are numeric epoch-like, detect the unit (s/ms/us/ns).
            if pd.api.types.is_numeric_dtype(series):
                unit = _detect_epoch_unit(series)
                dt = pd.to_datetime(series, unit=unit, utc=True, errors="coerce")
            else:
                # Try parse as string first.
                dt = pd.to_datetime(series, utc=True, errors="coerce")
                # If nothing parsed, retry as numeric epoch.
                if dt.notna().sum() == 0 and series.notna().sum() > 0:
                    unit = _detect_epoch_unit(series)
                    dt = pd.to_datetime(
                        pd.to_numeric(series, errors="coerce"),
                        unit=unit,
                        utc=True,
                        errors="coerce",
                    )

            dt = dt.dt.floor("s")

            # Some DSS payloads return null/epoch-like timestamps around 1970
            # (often for imported projects that were never modified). We treat
            # these as "old" projects and floor them to a reasonable baseline
            # so delta filtering remains deterministic.
            dt = dt.mask(dt < floor_dt, floor_dt)
            df[col] = dt

        if "versionTag.lastModifiedOn" in df.columns and "creationTag.lastModifiedOn" in df.columns:
            df["_lastModifiedOn"] = df["versionTag.lastModifiedOn"].fillna(df["creationTag.lastModifiedOn"])
        elif "versionTag.lastModifiedOn" in df.columns:
            df["_lastModifiedOn"] = df["versionTag.lastModifiedOn"]
        elif "creationTag.lastModifiedOn" in df.columns:
            df["_lastModifiedOn"] = df["creationTag.lastModifiedOn"]
        else:
            # No usable timestamps: collect all projects.
            df["_lastModifiedOn"] = pd.NaT

        if "projectKey" not in df.columns:
            return []

        # Filter by delta cursor.
        if df["_lastModifiedOn"].notna().any():
            df = df[df["_lastModifiedOn"] >= since]

        return df["projectKey"].dropna().astype(str).tolist()

    def run(self, progress_callback):
        """Execute the macro."""

        client = dataiku.api_client()

        run_start_ts = pd.Timestamp.now(tz="UTC").isoformat()

        since = self._read_projects_delta(client)
        keys = self._resolve_project_keys(client, since=since)

        # Used for path layout prefix only.
        output_base_dir = Path("partitioned_data")

        target = DSSFolderTarget(
            project_key=self.output_project_key,
            folder_lookup=self.output_folder_lookup,
            connection_name=self.output_connection_name,
            host=self.output_project_url,
            api_key=self.output_project_api,
        )

        # Ensure output folder exists before writing.
        ensure_managed_folder(
            project_key=target.project_key,
            folder_lookup=target.folder_lookup,
            connection_name=target.connection_name,
            host=target.host,
            api_key=target.api_key,
        )

        # progress_callback is per-runnable, not per-project, so we provide coarse reporting.
        if progress_callback is not None:
            progress_callback(0)

        n_jobs = self.n_jobs if self.do_parallel else 1

        result = collect_all_projects(
            client=client,
            output_base_dir=output_base_dir,
            project_keys=keys,
            n_jobs=n_jobs,
            batch_size=self.batch_size,
            output_folder_target=target,
        )

        if progress_callback is not None:
            progress_callback(len(result.collected_projects))

        # Best-effort: update the cursor for next run unless the macro imploded.
        # Use the run start timestamp to avoid skipping changes during the run.
        run_ts = run_start_ts
        self._update_projects_delta(client, run_ts)

        total_errors = sum(len(r.errors) for r in result.per_project.values())

        summary = pd.DataFrame(
            [
                {
                    "metric": "projects_selected",
                    "value": len(keys),
                },
                {
                    "metric": "projects_collected",
                    "value": len(result.collected_projects),
                },
                {
                    "metric": "total_list_errors",
                    "value": total_errors,
                },
                {
                    "metric": "projects_delta_since",
                    "value": since.isoformat(),
                },
                {
                    "metric": "projects_delta_updated_to",
                    "value": run_ts,
                },
            ]
        )

        rt = ResultTable()
        rt.add_column(1, "metric", "STRING")
        rt.add_column(2, "value", "STRING")

        for _, row in summary.astype(str).iterrows():
            rt.add_record([row["metric"], row["value"]])

        return rt
