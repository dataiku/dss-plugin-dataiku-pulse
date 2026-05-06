from __future__ import annotations

import importlib
import json
import os
import time
from collections import defaultdict
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
    CursorSpec,
    resolve_worker_project_key,
    OutputLayout,
    PulseMacroContext,
    build_context,
    ensure_output_folder,
    resolve_cursor_ts,
    update_cursor_ts,
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


def _parse_timestamp(series: pd.Series) -> pd.Series:
    """Parse audit log timestamps to UTC datetimes.

    Handles numeric epoch-like values and ISO-like strings.
    """

    if pd.api.types.is_numeric_dtype(series):
        from data_collection.data_normalizer.casting import _detect_epoch_unit

        unit = _detect_epoch_unit(series)
        return pd.to_datetime(series, unit=unit, utc=True, errors="coerce").dt.floor("s")

    # Use object dtype so pandas doesn't treat mixed values oddly.
    series_obj = series.astype("object")
    dt = pd.to_datetime(series_obj, utc=True, errors="coerce")

    # If parsing failed for all rows but values are numeric-like strings,
    # retry as epoch.
    if dt.notna().sum() == 0 and series_obj.notna().sum() > 0:
        from data_collection.data_normalizer.casting import _detect_epoch_unit

        unit = _detect_epoch_unit(series_obj)
        dt = pd.to_datetime(pd.to_numeric(series_obj, errors="coerce"), unit=unit, utc=True, errors="coerce")

    return dt.dt.floor("s")


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
        start_dt = self._resolve_audit_start_ts()
        worker_project_key = resolve_worker_project_key(client, fallback_project_key=self.project_key)

        return resolve_cursor_ts(
            client=client,
            project_key=worker_project_key,
            param_set=self.param_set,
            spec=CursorSpec(variable_name="audit_log_delta", debug_key="pulse_audit_logs_delta_debug"),
            default_ts=start_dt,
            local_mode=self._is_local_debug(),
        )

    def _update_audit_delta(self, client: Any, value: str) -> None:
        worker_project_key = resolve_worker_project_key(client, fallback_project_key=self.project_key)

        update_cursor_ts(
            client=client,
            project_key=worker_project_key,
            spec=CursorSpec(variable_name="audit_log_delta"),
            value=value,
            enabled=not self._is_local_debug(),
        )

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

        target = ensure_output_folder(param_set=self.param_set, remote_client=ctx.remote_client)

        layout = OutputLayout(base_dir=Path("partitioned_data"), module="audit_metadata")

        # Determine delta and which files to read
        last_update = self._read_audit_delta(ctx.local_client)
        time_diff = pd.Timestamp.now(tz="UTC") - last_update
        hours = float((time_diff.total_seconds() / 3600) + 1)

        files = [p for p in Path(".").iterdir() if p.is_file() and p.name.startswith("audit.log")]
        files = _find_recent_files(files, hours=hours)
        files = sorted(files, key=lambda p: p.name)

        chunk_size = int(self.param_set.get("pulse_audit_logs_chunk_size", 50_000))
        max_files = int(self.param_set.get("pulse_audit_logs_max_files", 5))
        backup_raw = bool(self.param_set.get("pulse_backup_audit_logs", False))

        processor_names = _load_processor_names()
        processor_results_by_name: dict[str, dict[str, int]] = defaultdict(
            lambda: {"rows_in": 0, "rows_out": 0, "wrote_groups": 0}
        )
        processor_messages: dict[str, str] = {}

        chunk_idx = 0
        wrote_any = False
        max_ts_seen: pd.Timestamp | None = None

        for p in files[:max_files]:
            for chunk in pd.read_json(p, lines=True, chunksize=chunk_size):
                chunk_idx += 1

                if chunk is None or chunk.shape[0] == 0:
                    continue

                if "timestamp" not in chunk.columns:
                    continue

                chunk["timestamp"] = _parse_timestamp(chunk["timestamp"])
                chunk = chunk[chunk["timestamp"].notna()].copy()
                if chunk.shape[0] == 0:
                    continue

                chunk = chunk[chunk["timestamp"] >= last_update]
                if chunk.shape[0] == 0:
                    continue

                wrote_any = True

                chunk_max_ts = chunk["timestamp"].max()
                if pd.notna(chunk_max_ts):
                    if max_ts_seen is None or chunk_max_ts > max_ts_seen:
                        max_ts_seen = pd.Timestamp(chunk_max_ts)

                # Optional RAW backup (before cleansing)
                if backup_raw:
                    raw_backup_path = (
                        layout.base_dir
                        / "raw"
                        / "category=audit_logs"
                        / "module=backup"
                        / instance_name
                        / f"{run_date.year:04d}"
                        / f"{run_date.month:02d}"
                        / f"{run_date.day:02d}"
                        / f"audit_logs-{run_epoch_ms}-{chunk_idx}.json.gz"
                    )
                    upload_json_gzip(
                        target=target,
                        output_path=raw_backup_path,
                        output_base_dir=layout.base_dir,
                        payload=chunk.to_dict(orient="records"),
                    )

                # Expand message
                if "message" in chunk.columns:
                    jdf = pd.json_normalize(chunk["message"]).add_prefix("message_").reset_index(drop=True)
                    drop_cols = [c for c in ["message", "mdc"] if c in chunk.columns]
                    chunk = chunk.drop(columns=drop_cols).reset_index(drop=True)
                    chunk = pd.concat([chunk, jdf], axis=1)

                # Minimal smoothing
                chunk["date"] = chunk["timestamp"].dt.date
                chunk["instance_name"] = instance_name

                for proc_name in processor_names:
                    try:
                        mod = importlib.import_module(f"data_collection.audit_logs_modules.{proc_name}")
                        out_df = mod.main(chunk)
                    except Exception as e:
                        processor_messages.setdefault(proc_name, repr(e))
                        continue

                    processor_results_by_name[proc_name]["rows_in"] += int(chunk.shape[0])

                    if out_df is None or not isinstance(out_df, pd.DataFrame) or out_df.shape[0] == 0:
                        continue

                    processor_results_by_name[proc_name]["rows_out"] += int(out_df.shape[0])

                    # Require dataiku_category for partitioning
                    if "dataiku_category" not in out_df.columns:
                        processor_messages.setdefault(proc_name, "missing dataiku_category")
                        continue

                    for module_name, grp in out_df.groupby("dataiku_category"):
                        # SILVER only
                        # Flatten config lookup key can differ from storage partitions.
                        # For event_mapping, all categories share a common base schema.
                        flatten_category = proc_name
                        flatten_module = str(module_name)
                        flatten_variant = None
                        flatten_base = None
                        if proc_name == "event_mapping":
                            flatten_category = "audit_dataiku_usage"
                            flatten_module = "audit_metadata"
                            flatten_variant = str(module_name)
                            flatten_base = ("audit_dataiku_usage", "audit_metadata")

                        silver_df = normalize_silver(
                            df=grp,
                            instance_name=instance_name,
                            run_ts=run_ts,
                            category=flatten_category,
                            module=flatten_module,
                            todo_section="audit",
                            flatten_base=flatten_base,
                            flatten_variant=flatten_variant,
                        )

                        parquet_name = f"audit_logs-{run_epoch_ms}-{chunk_idx}.parquet"
                        dq_name = f"audit_logs-{run_epoch_ms}-{chunk_idx}.dq.json.gz"

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
                            / parquet_name
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
                            / dq_name
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
                            processor_results_by_name[proc_name]["wrote_groups"] += 1
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
                                / parquet_name
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

        if not wrote_any:
            return "No new audit rows"

        # Update cursor on success (best-effort).
        if max_ts_seen is not None and pd.notna(max_ts_seen):
            self._update_audit_delta(ctx.local_client, pd.Timestamp(max_ts_seen).isoformat())

        processor_results: List[ProcessorResult] = []
        for name, agg in processor_results_by_name.items():
            processor_results.append(
                ProcessorResult(
                    name,
                    int(agg.get("rows_in", 0)),
                    int(agg.get("rows_out", 0)),
                    int(agg.get("wrote_groups", 0)),
                    message=processor_messages.get(name),
                )
            )

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
