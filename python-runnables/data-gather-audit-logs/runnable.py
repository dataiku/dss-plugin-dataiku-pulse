from __future__ import annotations

import importlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import dataiku
import pandas as pd
from dataiku.runnables import ResultTable, Runnable

from data_collection.audit_logs_modules.audit_paths import chdir_audit_logs
from data_collection.data_collection.instance import get_instance_name
from data_collection.data_normalizer import check_silver_dq, normalize_silver
from data_collection.helper import (
    DSSFolderTarget,
    OutputLayout,
    PulseMacroContext,
    build_context,
    ensure_managed_folder,
    upload_json_gzip,
    upload_parquet,
)


def _find_recent_files(file_list: Iterable[Path], *, hours: float) -> List[Path]:
    recent_files: List[Path] = []
    cutoff = time.time() - (hours * 3600)
    for p in file_list:
        try:
            if p.exists() and p.stat().st_mtime >= cutoff:
                recent_files.append(p)
        except Exception:
            continue
    return recent_files


def _load_yaml_list(path: Path) -> List[str]:
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    raise ValueError(f"Expected YAML list in {path}, got {type(raw)!r}")


def _load_processor_names() -> List[str]:
    """Load `modules.yaml` from the installed plugin resources.

    In DSS macro runs, runnables are executed from a temporary `dku_code.py` file,
    so relative filesystem paths like `Path(__file__).parents[...]` are not
    reliable. Reading the YAML from the `data_collection.audit_logs_modules`
    package ensures this works both locally and in DSS.
    """

    from importlib.resources import as_file, files

    modules_res = files("data_collection.audit_logs_modules").joinpath("modules.yaml")
    with as_file(modules_res) as p:
        return _load_yaml_list(Path(p))


@dataclass(frozen=True)
class ProcessorResult:
    name: str
    rows_in: int
    rows_out: int
    wrote_groups: int
    message: str | None = None


class MyRunnable(Runnable):
    """Gather audit logs and run audit processors."""

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

    def _is_local_debug(self) -> bool:
        return bool(self.param_set.get("pulse_audit_logs_debug", False))

    def _resolve_audit_start_ts(self) -> pd.Timestamp:
        """Resolve the configured starting timestamp for reading audit logs."""

        default_dt = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=1)

        raw = self.param_set.get("pulse_audit_logs_delta")
        if raw:
            dt = pd.to_datetime(raw, utc=True, errors="coerce")
            if not pd.isna(dt):
                return dt

        return default_dt

    def _read_audit_delta(self, client: Any) -> pd.Timestamp:
        """Return the delta cursor for incremental macro runs.

        Local/debug runs should not use the project variable cursor. They only use
        the configured starting timestamp (`pulse_audit_logs_delta`).
        """

        start_dt = self._resolve_audit_start_ts()

        # Debug behavior: ignore project variable and always use configured start.
        # When developing locally, we don't want to persist or advance cursors.
        if self._is_local_debug() or bool(self.param_set.get("pulse_audit_logs_delta_debug", False)):
            return start_dt

        try:
            project = client.get_project(self.project_key)
            variables = project.get_variables()
            raw = variables.get("local", {}).get("audit_log_delta")
            if raw:
                dt = pd.to_datetime(raw, utc=True, errors="coerce")
                if not pd.isna(dt):
                    return dt
        except Exception:
            pass

        return start_dt

    def _update_audit_delta(self, client: Any, value: str) -> None:
        try:
            project = client.get_project(self.project_key)
            variables = project.get_variables()
            local = variables.get("local") or {}
            local["audit_log_delta"] = value
            variables["local"] = local
            project.set_variables(variables)
        except Exception:
            pass

    def run(self, progress_callback):
        ctx: PulseMacroContext = build_context(plugin_config=self.plugin_config)
        self.param_set = ctx.param_set

        repo_root = Path(__file__).resolve().parents[2]
        chdir_audit_logs(client=ctx.local_client, plugin_config=self.param_set, repo_root=repo_root)

        instance_name = get_instance_name(ctx.local_client)
        if not instance_name:
            raise ValueError("Could not determine instance_name")

        run_dt = datetime.now(timezone.utc)
        run_ts = run_dt.isoformat()
        run_date = run_dt.date()
        run_epoch_ms = int(run_dt.timestamp() * 1000)

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

        layout = OutputLayout(base_dir=Path("partitioned_data"), module="audit_metadata")

        # Determine delta and which files to read
        last_update = self._read_audit_delta(ctx.local_client)
        time_diff = pd.Timestamp.now(tz="UTC") - last_update
        hours = float((time_diff.total_seconds() / 3600) + 1)

        files = [p for p in Path(".").iterdir() if p.is_file() and p.name.startswith("audit.log")]
        files = _find_recent_files(files, hours=hours)
        files = sorted(files, key=lambda p: p.name)

        # Load logs (delta by timestamp)
        dfs: List[pd.DataFrame] = []
        max_files = int(self.param_set.get("pulse_audit_logs_max_files", 5))
        for p in files[:max_files]:
            df = pd.read_json(p, lines=True)
            dfs.append(df)

        if not dfs:
            return "No audit log files to read"

        df_audit = pd.concat(dfs, ignore_index=True)

        # Ensure timestamp is UTC datetime then delta-filter
        if "timestamp" in df_audit.columns:
            # Audit logs may be epoch-like or ISO strings; detect if numeric.
            if pd.api.types.is_numeric_dtype(df_audit["timestamp"]):
                from data_collection.data_normalizer.casting import _detect_epoch_unit

                unit = _detect_epoch_unit(df_audit["timestamp"])
                df_audit["timestamp"] = pd.to_datetime(
                    df_audit["timestamp"],
                    unit=unit,
                    utc=True,
                    errors="coerce",
                ).dt.floor("s")
            else:
                df_audit["timestamp"] = pd.to_datetime(
                    df_audit["timestamp"],
                    utc=True,
                    errors="coerce",
                ).dt.floor("s")

            df_audit = df_audit[df_audit["timestamp"] >= last_update]

        if df_audit.shape[0] == 0:
            return "No new audit rows"

        # Optional RAW backup (before cleansing)
        if bool(self.param_set.get("pulse_backup_audit_logs", False)):
            raw_backup_path = (
                layout.base_dir
                / "raw"
                / "category=audit_logs"
                / "module=backup"
                / instance_name
                / f"{run_date.year:04d}"
                / f"{run_date.month:02d}"
                / f"{run_date.day:02d}"
                / f"audit_logs-{run_epoch_ms}.json.gz"
            )
            upload_json_gzip(
                target=target,
                output_path=raw_backup_path,
                output_base_dir=layout.base_dir,
                payload=df_audit.to_dict(orient="records"),
            )

        # Expand message
        if "message" in df_audit.columns:
            jdf = pd.json_normalize(df_audit["message"]).add_prefix("message_").reset_index(drop=True)
            drop_cols = [c for c in ["message", "mdc"] if c in df_audit.columns]
            df_audit = df_audit.drop(columns=drop_cols).reset_index(drop=True)
            df_audit = pd.concat([df_audit, jdf], axis=1)

        # Minimal smoothing
        df_audit["date"] = df_audit["timestamp"].dt.date
        df_audit["instance_name"] = instance_name

        # Load processors
        processor_names = _load_processor_names()

        processor_results: List[ProcessorResult] = []

        for proc_name in processor_names:
            try:
                mod = importlib.import_module(f"data_collection.audit_logs_modules.{proc_name}")
                out_df = mod.main(df_audit)
            except Exception as e:
                processor_results.append(ProcessorResult(proc_name, df_audit.shape[0], 0, 0, message=repr(e)))
                continue

            if out_df is None or not isinstance(out_df, pd.DataFrame) or out_df.shape[0] == 0:
                processor_results.append(
                    ProcessorResult(proc_name, df_audit.shape[0], 0, 0, message=f"module {proc_name} returned 0 results")
                )
                continue

            # Require dataiku_category for partitioning
            if "dataiku_category" not in out_df.columns:
                processor_results.append(ProcessorResult(proc_name, df_audit.shape[0], out_df.shape[0], 0, message="missing dataiku_category"))
                continue

            wrote_groups = 0
            for module_name, grp in out_df.groupby("dataiku_category"):
                # SILVER only
                silver_df = normalize_silver(
                    df=grp,
                    instance_name=instance_name,
                    run_ts=run_ts,
                    category=proc_name,
                    module=str(module_name),
                )

                # Write SILVER grouped by dataiku_category
                silver_path = (
                    layout.base_dir
                    / "silver"
                    / f"category={proc_name}"
                    / f"module={str(module_name)}"
                    / f"instance_name={instance_name}"
                    / f"year={run_date.year:04d}"
                    / f"month={run_date.month:02d}"
                    / f"day={run_date.day:02d}"
                    / f"audit_logs-{run_epoch_ms}.parquet"
                )
                silver_fail_reason_path = (
                    layout.base_dir
                    / "silver_fail"
                    / f"category={proc_name}"
                    / f"module={str(module_name)}"
                    / f"instance_name={instance_name}"
                    / f"year={run_date.year:04d}"
                    / f"month={run_date.month:02d}"
                    / f"day={run_date.day:02d}"
                    / f"audit_logs-{run_epoch_ms}.dq.json.gz"
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
                    wrote_groups += 1
                else:
                    fail_path = (
                        layout.base_dir
                        / "silver_fail"
                        / f"category={proc_name}"
                        / f"module={str(module_name)}"
                        / f"instance_name={instance_name}"
                        / f"year={run_date.year:04d}"
                        / f"month={run_date.month:02d}"
                        / f"day={run_date.day:02d}"
                        / f"audit_logs-{run_epoch_ms}.parquet"
                    )
                    upload_parquet(
                        target=target,
                        output_path=fail_path,
                        output_base_dir=layout.base_dir,
                        df=silver_df,
                        compression="snappy",
                    )
                    upload_json_gzip(
                        target=target,
                        output_path=silver_fail_reason_path,
                        output_base_dir=layout.base_dir,
                        payload={
                            "instance_name": instance_name,
                            "run_ts": run_ts,
                            "processor": proc_name,
                            "module": str(module_name),
                            "rows": int(silver_df.shape[0]),
                            "cols": int(silver_df.shape[1]),
                            "dq_errors": dq.errors,
                        },
                    )

            processor_results.append(ProcessorResult(proc_name, df_audit.shape[0], out_df.shape[0], wrote_groups))

        # Update cursor on success (best-effort).
        # Local/debug runs should not persist or advance cursors.
        if not self._is_local_debug():
            max_ts = df_audit["timestamp"].max()
            if pd.notna(max_ts):
                self._update_audit_delta(ctx.local_client, pd.Timestamp(max_ts).isoformat())

        # ResultTable
        rt = ResultTable()
        rt.add_column(1, "processor", "STRING")
        rt.add_column(2, "rows_in", "STRING")
        rt.add_column(3, "rows_out", "STRING")
        rt.add_column(4, "wrote_groups", "STRING")
        rt.add_column(5, "message", "STRING")

        for r in processor_results:
            rt.add_record([r.name, str(r.rows_in), str(r.rows_out), str(r.wrote_groups), str(r.message or "")])

        return rt
