from __future__ import annotations

import importlib
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from dataiku.runnables import ResultTable, Runnable

from data_collection.audit_logs_modules.audit_paths import resolve_audit_logs_dir
from data_collection.data_collection.instance import get_instance_name
from data_collection.data_normalizer import check_silver_dq, normalize_silver
from data_collection.helper import (
    CursorSpec,
    OutputLayout,
    PulseMacroContext,
    build_context,
    ensure_output_folder,
    resolve_cursor_ts,
    resolve_worker_project_key,
    update_cursor_ts,
    upload_json_gzip,
    upload_parquet,
)

logger = logging.getLogger(__name__)


def _select_candidate_audit_files(
    file_list: list[Path],
    *,
    cursor_ts: pd.Timestamp,
    max_files: int,
) -> tuple[list[Path], int, pd.Timestamp | None, pd.Timestamp | None]:
    file_entries: list[tuple[Path, float]] = []
    for path in file_list:
        try:
            if path.exists():
                file_entries.append((path, path.stat().st_mtime))
        except Exception:
            continue

    if not file_entries:
        return [], 0, None, None

    cursor_epoch = cursor_ts.timestamp()
    sorted_entries = sorted(file_entries, key=lambda item: item[1], reverse=True)
    selected_entries: list[tuple[Path, float]] = []
    boundary_added = False

    for path, mtime_epoch in sorted_entries:
        if len(selected_entries) >= max_files:
            break

        if mtime_epoch >= cursor_epoch:
            selected_entries.append((path, mtime_epoch))
            continue

        if not selected_entries:
            selected_entries.append((path, mtime_epoch))
            boundary_added = True
            continue

        if not boundary_added:
            selected_entries.append((path, mtime_epoch))
            boundary_added = True
        break

    if not selected_entries:
        selected_entries.append(sorted_entries[0])

    selected_paths = [path for path, _ in selected_entries]
    selected_mtimes = [pd.Timestamp.fromtimestamp(mtime_epoch, tz="UTC") for _, mtime_epoch in selected_entries]
    newest_mtime = max(selected_mtimes) if selected_mtimes else None
    oldest_mtime = min(selected_mtimes) if selected_mtimes else None
    return selected_paths, len(sorted_entries), oldest_mtime, newest_mtime


def _parse_timestamp(series: pd.Series) -> pd.Series:
    """Parse audit log timestamps to UTC datetimes.

    Handles numeric epoch-like values and ISO-like strings.
    """

    if pd.api.types.is_numeric_dtype(series):
        from data_collection.data_normalizer.casting import _detect_epoch_unit

        unit = _detect_epoch_unit(series)
        return pd.to_datetime(series, unit=unit, utc=True, errors="coerce").dt.floor("s")

    series_obj = series.astype("object")
    dt = pd.to_datetime(series_obj, utc=True, errors="coerce")

    if dt.notna().sum() == 0 and series_obj.notna().sum() > 0:
        from data_collection.data_normalizer.casting import _detect_epoch_unit

        unit = _detect_epoch_unit(series_obj)
        dt = pd.to_datetime(
            pd.to_numeric(series_obj, errors="coerce"),
            unit=unit,
            utc=True,
            errors="coerce",
        )

    return dt.dt.floor("s")


def _load_yaml_list(path: Path) -> list[str]:
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    raise ValueError(f"Expected YAML list in {path}, got {type(raw)!r}")


def _load_processor_names() -> list[str]:
    from importlib.resources import as_file, files

    modules_res = files("data_collection.audit_logs_modules").joinpath("modules.yaml")
    with as_file(modules_res) as path:
        return _load_yaml_list(Path(path))


def _load_processors(names: list[str]) -> tuple[dict[str, Any], dict[str, str]]:
    processors: dict[str, Any] = {}
    messages: dict[str, str] = {}
    for name in names:
        try:
            processors[name] = importlib.import_module(f"data_collection.audit_logs_modules.{name}")
        except Exception as exc:
            logger.exception("Failed loading audit processor %s", name)
            messages[name] = repr(exc)
    return processors, messages


def _prefilter_processor_input(*, proc_name: str, chunk: pd.DataFrame) -> pd.DataFrame:
    if chunk is None or not isinstance(chunk, pd.DataFrame) or chunk.shape[0] == 0:
        return pd.DataFrame()

    if proc_name == "event_mapping":
        if "message_msgType" in chunk.columns:
            filtered = chunk[chunk["message_msgType"].notna()].copy()
            if filtered.shape[0] == 0:
                return filtered
            chunk = filtered
        return chunk

    if proc_name == "users":
        filtered = chunk
        if "message_authSource" in filtered.columns:
            filtered = filtered[filtered["message_authSource"] == "USER_FROM_UI"]
        if filtered.shape[0] == 0:
            return filtered
        if "message_scenarioId" in filtered.columns:
            filtered = filtered[filtered["message_scenarioId"].isna()]
        if filtered.shape[0] == 0:
            return filtered
        if "message_jobId" in filtered.columns:
            filtered = filtered[filtered["message_jobId"].isna()]
        if filtered.shape[0] == 0:
            return filtered

        login_candidates = [
            "message_login",
            "message_user",
            "message_authUser",
            "mdc_user",
            "login",
        ]
        present_login_cols = [col for col in login_candidates if col in filtered.columns]
        if not present_login_cols:
            return filtered.iloc[0:0]

        login_mask = None
        for col in present_login_cols:
            current_mask = filtered[col].astype("string").fillna("").str.len() > 0
            login_mask = current_mask if login_mask is None else (login_mask | current_mask)
        filtered = filtered[login_mask]
        if filtered.shape[0] == 0:
            return filtered

        msgtype_candidates = ["message_msgType", "message_msgtype", "msgType", "msgtype"]
        if not any(col in filtered.columns for col in msgtype_candidates):
            return filtered.iloc[0:0]
        return filtered.copy()

    return chunk


def _prepare_generic_audit_chunk(*, chunk: pd.DataFrame, instance_name: str) -> pd.DataFrame:
    prepared = chunk
    if "topic" in prepared.columns:
        prepared = prepared[prepared["topic"] == "generic"].copy()
    if prepared.shape[0] == 0:
        return prepared

    if "message" in prepared.columns:
        jdf = pd.json_normalize(prepared["message"]).add_prefix("message_").reset_index(drop=True)
        drop_cols = [col for col in ["message", "mdc"] if col in prepared.columns]
        prepared = prepared.drop(columns=drop_cols).reset_index(drop=True)
        prepared = pd.concat([prepared, jdf], axis=1)

    prepared["date"] = prepared["timestamp"].dt.date
    prepared["instance_name"] = instance_name
    return prepared


@dataclass(frozen=True)
class ProcessorResult:
    name: str
    rows_in: int
    rows_out: int
    wrote_groups: int
    message: str | None = None


@dataclass(frozen=True)
class ChunkProcessingStats:
    rows_read: int = 0
    rows_missing_timestamp: int = 0
    rows_invalid_timestamp: int = 0
    rows_before_cursor: int = 0
    raw_backups_written: int = 0
    silver_fail_groups: int = 0
    chunks_all_before_cursor: int = 0
    files_stopped_early: int = 0
    write_failures: int = 0
    raw_write_failures: int = 0
    silver_write_failures: int = 0


class MyRunnable(Runnable):
    """Gather audit logs and run audit processors."""

    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config or {}
        self.plugin_config = plugin_config or {}
        self.param_set = self.plugin_config.get("pulse_primary", {}) or {}
        self.output_project_key = self.param_set.get("pulse_project_key", "DATA_COLLECTION")
        self.output_folder_lookup = self.param_set.get("pulse_partitioned_data", "partitioned_data")
        self.output_connection_name = self.param_set.get("pulse_folder_connection")

    def get_progress_target(self):
        return None

    def _is_local_debug(self) -> bool:
        return bool(self.param_set.get("pulse_audit_logs_debug", False))

    def _resolve_audit_start_ts(self) -> pd.Timestamp:
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

    def _build_partition_dir(
        self,
        *,
        layout: OutputLayout,
        layer: str,
        proc_name: str,
        module_name: str,
        instance_name: str,
        event_date,
    ) -> Path:
        return (
            layout.base_dir
            / layer
            / f"category={proc_name}"
            / f"module={module_name}"
            / f"instance_name={instance_name}"
            / f"year={event_date.year:04d}"
            / f"month={event_date.month:02d}"
            / f"day={event_date.day:02d}"
        )

    def _build_raw_backup_path(
        self,
        *,
        layout: OutputLayout,
        instance_name: str,
        event_date,
        file_name: str,
    ) -> Path:
        return (
            layout.base_dir
            / "raw"
            / "category=audit_logs"
            / "module=backup"
            / f"instance_name={instance_name}"
            / f"year={event_date.year:04d}"
            / f"month={event_date.month:02d}"
            / f"day={event_date.day:02d}"
            / file_name
        )

    def run(self, progress_callback):
        ctx: PulseMacroContext = build_context(plugin_config=self.plugin_config)
        self.param_set = ctx.param_set

        repo_root = Path(__file__).resolve().parents[2]
        audit_dir = resolve_audit_logs_dir(client=ctx.local_client, repo_root=repo_root)
        logger.info("Resolved audit log directory: %s", audit_dir)

        instance_name = get_instance_name(ctx.local_client)
        if not instance_name:
            raise ValueError("Could not determine instance_name")

        run_dt = datetime.now(timezone.utc)
        run_ts = run_dt.isoformat()
        run_epoch_ms = int(run_dt.timestamp() * 1000)

        target = ensure_output_folder(param_set=self.param_set, remote_client=ctx.remote_client)
        layout = OutputLayout(base_dir=Path("partitioned_data"), module="audit_metadata")

        last_update = self._read_audit_delta(ctx.local_client)
        chunk_size = int(self.param_set.get("pulse_audit_logs_chunk_size", 50_000))
        max_files = int(self.param_set.get("pulse_audit_logs_max_files", 5))
        backup_raw = bool(self.param_set.get("pulse_backup_audit_logs", False))

        available_files = [path for path in audit_dir.iterdir() if path.is_file() and path.name.startswith("audit.log")]
        files, available_file_count, selected_oldest_mtime, selected_newest_mtime = _select_candidate_audit_files(
            available_files,
            cursor_ts=last_update,
            max_files=max_files,
        )

        processor_names = _load_processor_names()
        processors, processor_messages = _load_processors(processor_names)
        processor_results_by_name: dict[str, dict[str, int]] = defaultdict(
            lambda: {"rows_in": 0, "rows_out": 0, "wrote_groups": 0}
        )

        chunk_idx = 0
        wrote_any = False
        max_ts_seen: pd.Timestamp | None = None
        pending_cursor_ts: pd.Timestamp | None = None
        stats = ChunkProcessingStats()
        files_scanned = 0
        chunks_scanned = 0
        processor_failures = 0

        logger.info(
            "Starting audit gather for %s with last_update=%s, max_files=%s, chunk_size=%s, available_files=%s, selected_files=%s",
            instance_name,
            last_update,
            max_files,
            chunk_size,
            available_file_count,
            len(files),
        )
        logger.info("Audit raw backup enabled=%s", backup_raw)
        if files:
            logger.info(
                "Selected audit files oldest_mtime=%s newest_mtime=%s names=%s",
                selected_oldest_mtime.isoformat() if selected_oldest_mtime is not None else "",
                selected_newest_mtime.isoformat() if selected_newest_mtime is not None else "",
                ", ".join(path.name for path in files),
            )

        for file_idx, path in enumerate(files):
            files_scanned += 1
            stop_current_file_early = False
            allow_early_stop = file_idx > 0
            logger.info("Reading audit log file %s", path)
            for chunk in pd.read_json(path, lines=True, chunksize=chunk_size):
                chunks_scanned += 1
                chunk_idx += 1

                if chunk is None or chunk.shape[0] == 0:
                    continue

                stats = ChunkProcessingStats(
                    rows_read=stats.rows_read + int(chunk.shape[0]),
                    rows_missing_timestamp=stats.rows_missing_timestamp,
                    rows_invalid_timestamp=stats.rows_invalid_timestamp,
                    rows_before_cursor=stats.rows_before_cursor,
                    raw_backups_written=stats.raw_backups_written,
                    silver_fail_groups=stats.silver_fail_groups,
                    chunks_all_before_cursor=stats.chunks_all_before_cursor,
                    files_stopped_early=stats.files_stopped_early,
                )

                if "timestamp" not in chunk.columns:
                    logger.warning("Chunk %s missing timestamp column in %s", chunk_idx, path.name)
                    stats = ChunkProcessingStats(
                        rows_read=stats.rows_read,
                        rows_missing_timestamp=stats.rows_missing_timestamp + int(chunk.shape[0]),
                        rows_invalid_timestamp=stats.rows_invalid_timestamp,
                        rows_before_cursor=stats.rows_before_cursor,
                        raw_backups_written=stats.raw_backups_written,
                        silver_fail_groups=stats.silver_fail_groups,
                        chunks_all_before_cursor=stats.chunks_all_before_cursor,
                        files_stopped_early=stats.files_stopped_early,
                    )
                    continue

                parsed_ts = _parse_timestamp(chunk["timestamp"])
                invalid_ts = int(parsed_ts.isna().sum())
                if invalid_ts:
                    logger.info("Chunk %s dropped %s rows with invalid timestamps", chunk_idx, invalid_ts)
                chunk = chunk.copy()
                chunk["timestamp"] = parsed_ts
                chunk = chunk[chunk["timestamp"].notna()].copy()
                stats = ChunkProcessingStats(
                    rows_read=stats.rows_read,
                    rows_missing_timestamp=stats.rows_missing_timestamp,
                    rows_invalid_timestamp=stats.rows_invalid_timestamp + invalid_ts,
                    rows_before_cursor=stats.rows_before_cursor,
                    raw_backups_written=stats.raw_backups_written,
                    silver_fail_groups=stats.silver_fail_groups,
                    chunks_all_before_cursor=stats.chunks_all_before_cursor,
                    files_stopped_early=stats.files_stopped_early,
                )
                if chunk.shape[0] == 0:
                    continue

                chunk_min_ts = pd.Timestamp(chunk["timestamp"].min())
                chunk_max_ts = pd.Timestamp(chunk["timestamp"].max())

                if allow_early_stop and pd.notna(chunk_max_ts) and chunk_max_ts < last_update:
                    logger.info(
                        "Stopping file %s early at chunk %s because chunk_max_ts=%s is before cursor=%s",
                        path.name,
                        chunk_idx,
                        chunk_max_ts,
                        last_update,
                    )
                    stats = ChunkProcessingStats(
                        rows_read=stats.rows_read,
                        rows_missing_timestamp=stats.rows_missing_timestamp,
                        rows_invalid_timestamp=stats.rows_invalid_timestamp,
                        rows_before_cursor=stats.rows_before_cursor + int(chunk.shape[0]),
                        raw_backups_written=stats.raw_backups_written,
                        silver_fail_groups=stats.silver_fail_groups,
                        chunks_all_before_cursor=stats.chunks_all_before_cursor + 1,
                        files_stopped_early=stats.files_stopped_early + 1,
                    )
                    stop_current_file_early = True
                    break

                before_cursor = int((chunk["timestamp"] < last_update).sum())
                chunk = chunk[chunk["timestamp"] >= last_update].copy()
                stats = ChunkProcessingStats(
                    rows_read=stats.rows_read,
                    rows_missing_timestamp=stats.rows_missing_timestamp,
                    rows_invalid_timestamp=stats.rows_invalid_timestamp,
                    rows_before_cursor=stats.rows_before_cursor + before_cursor,
                    raw_backups_written=stats.raw_backups_written,
                    silver_fail_groups=stats.silver_fail_groups,
                    chunks_all_before_cursor=stats.chunks_all_before_cursor,
                    files_stopped_early=stats.files_stopped_early,
                )
                if chunk.shape[0] == 0:
                    if allow_early_stop and pd.notna(chunk_min_ts) and chunk_min_ts < last_update:
                        logger.info(
                            "Stopping file %s after empty post-cursor chunk %s; chunk_min_ts=%s cursor=%s",
                            path.name,
                            chunk_idx,
                            chunk_min_ts,
                            last_update,
                        )
                        stats = ChunkProcessingStats(
                            rows_read=stats.rows_read,
                            rows_missing_timestamp=stats.rows_missing_timestamp,
                            rows_invalid_timestamp=stats.rows_invalid_timestamp,
                            rows_before_cursor=stats.rows_before_cursor,
                            raw_backups_written=stats.raw_backups_written,
                            silver_fail_groups=stats.silver_fail_groups,
                            chunks_all_before_cursor=stats.chunks_all_before_cursor + 1,
                            files_stopped_early=stats.files_stopped_early + 1,
                        )
                        stop_current_file_early = True
                        break
                    continue

                wrote_any = True
                if pd.notna(chunk_max_ts) and (max_ts_seen is None or chunk_max_ts > max_ts_seen):
                    max_ts_seen = chunk_max_ts

                prepared_chunk = _prepare_generic_audit_chunk(chunk=chunk, instance_name=instance_name)
                if prepared_chunk.shape[0] == 0:
                    continue

                chunk_success = True

                if backup_raw:
                    event_date = chunk_max_ts.date()
                    raw_backup_path = self._build_raw_backup_path(
                        layout=layout,
                        instance_name=instance_name,
                        event_date=event_date,
                        file_name=f"audit_logs-{run_epoch_ms}-{chunk_idx}.json.gz",
                    )
                    logger.info("Writing audit raw backup to %s", raw_backup_path.as_posix())
                    try:
                        upload_json_gzip(
                            target=target,
                            output_path=raw_backup_path,
                            output_base_dir=layout.base_dir,
                            payload=chunk.to_dict(orient="records"),
                        )
                        stats = ChunkProcessingStats(
                            rows_read=stats.rows_read,
                            rows_missing_timestamp=stats.rows_missing_timestamp,
                            rows_invalid_timestamp=stats.rows_invalid_timestamp,
                            rows_before_cursor=stats.rows_before_cursor,
                            raw_backups_written=stats.raw_backups_written + 1,
                            silver_fail_groups=stats.silver_fail_groups,
                            chunks_all_before_cursor=stats.chunks_all_before_cursor,
                            files_stopped_early=stats.files_stopped_early,
                            write_failures=stats.write_failures,
                            raw_write_failures=stats.raw_write_failures,
                            silver_write_failures=stats.silver_write_failures,
                        )
                    except Exception as exc:
                        chunk_success = False
                        logger.exception("Raw audit backup write failed for chunk %s", chunk_idx)
                        processor_messages.setdefault("__raw_write__", repr(exc))
                        stats = ChunkProcessingStats(
                            rows_read=stats.rows_read,
                            rows_missing_timestamp=stats.rows_missing_timestamp,
                            rows_invalid_timestamp=stats.rows_invalid_timestamp,
                            rows_before_cursor=stats.rows_before_cursor,
                            raw_backups_written=stats.raw_backups_written,
                            silver_fail_groups=stats.silver_fail_groups,
                            chunks_all_before_cursor=stats.chunks_all_before_cursor,
                            files_stopped_early=stats.files_stopped_early,
                            write_failures=stats.write_failures + 1,
                            raw_write_failures=stats.raw_write_failures + 1,
                            silver_write_failures=stats.silver_write_failures,
                        )

                for proc_name in processor_names:
                    mod = processors.get(proc_name)
                    if mod is None:
                        processor_failures += 1
                        chunk_success = False
                        continue

                    proc_input = _prefilter_processor_input(proc_name=proc_name, chunk=prepared_chunk)
                    if proc_input is None or not isinstance(proc_input, pd.DataFrame) or proc_input.shape[0] == 0:
                        continue

                    try:
                        out_df = mod.main(proc_input)
                    except Exception as exc:
                        processor_failures += 1
                        chunk_success = False
                        logger.exception("Processor %s failed on chunk %s", proc_name, chunk_idx)
                        processor_messages.setdefault(proc_name, repr(exc))
                        continue

                    processor_results_by_name[proc_name]["rows_in"] += int(proc_input.shape[0])

                    if out_df is None or not isinstance(out_df, pd.DataFrame) or out_df.shape[0] == 0:
                        continue

                    processor_results_by_name[proc_name]["rows_out"] += int(out_df.shape[0])

                    if "dataiku_category" not in out_df.columns:
                        processor_failures += 1
                        chunk_success = False
                        message = "missing dataiku_category"
                        logger.warning("Processor %s output missing dataiku_category", proc_name)
                        processor_messages.setdefault(proc_name, message)
                        continue

                    for module_name, grp in out_df.groupby("dataiku_category"):
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
                        if "timestamp" in silver_df.columns:
                            event_ts = pd.to_datetime(silver_df["timestamp"].max(), utc=True, errors="coerce")
                            if pd.isna(event_ts):
                                event_date = chunk_max_ts.date()
                            else:
                                event_date = event_ts.date()
                        else:
                            event_date = chunk_max_ts.date()

                        silver_path = self._build_partition_dir(
                            layout=layout,
                            layer="silver",
                            proc_name=proc_name,
                            module_name=str(module_name),
                            instance_name=instance_name,
                            event_date=event_date,
                        ) / parquet_name
                        silver_fail_reason_path = self._build_partition_dir(
                            layout=layout,
                            layer="silver_fail",
                            proc_name=proc_name,
                            module_name=str(module_name),
                            instance_name=instance_name,
                            event_date=event_date,
                        ) / dq_name

                        dq = check_silver_dq(silver_df)
                        if dq.ok:
                            try:
                                upload_parquet(
                                    target=target,
                                    output_path=silver_path,
                                    output_base_dir=layout.base_dir,
                                    df=silver_df,
                                    compression="snappy",
                                )
                                processor_results_by_name[proc_name]["wrote_groups"] += 1
                            except Exception as exc:
                                chunk_success = False
                                logger.exception(
                                    "Silver write failed for processor=%s module=%s chunk=%s",
                                    proc_name,
                                    module_name,
                                    chunk_idx,
                                )
                                processor_messages.setdefault(f"{proc_name}::__write__", repr(exc))
                                stats = ChunkProcessingStats(
                                    rows_read=stats.rows_read,
                                    rows_missing_timestamp=stats.rows_missing_timestamp,
                                    rows_invalid_timestamp=stats.rows_invalid_timestamp,
                                    rows_before_cursor=stats.rows_before_cursor,
                                    raw_backups_written=stats.raw_backups_written,
                                    silver_fail_groups=stats.silver_fail_groups,
                                    chunks_all_before_cursor=stats.chunks_all_before_cursor,
                                    files_stopped_early=stats.files_stopped_early,
                                    write_failures=stats.write_failures + 1,
                                    raw_write_failures=stats.raw_write_failures,
                                    silver_write_failures=stats.silver_write_failures + 1,
                                )
                        else:
                            chunk_success = False
                            stats = ChunkProcessingStats(
                                rows_read=stats.rows_read,
                                rows_missing_timestamp=stats.rows_missing_timestamp,
                                rows_invalid_timestamp=stats.rows_invalid_timestamp,
                                rows_before_cursor=stats.rows_before_cursor,
                                raw_backups_written=stats.raw_backups_written,
                                silver_fail_groups=stats.silver_fail_groups + 1,
                                chunks_all_before_cursor=stats.chunks_all_before_cursor,
                                files_stopped_early=stats.files_stopped_early,
                                write_failures=stats.write_failures,
                                raw_write_failures=stats.raw_write_failures,
                                silver_write_failures=stats.silver_write_failures,
                            )
                            fail_path = self._build_partition_dir(
                                layout=layout,
                                layer="silver_fail",
                                proc_name=proc_name,
                                module_name=str(module_name),
                                instance_name=instance_name,
                                event_date=event_date,
                            ) / parquet_name
                            try:
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
                            except Exception as exc:
                                logger.exception(
                                    "Silver fail write failed for processor=%s module=%s chunk=%s",
                                    proc_name,
                                    module_name,
                                    chunk_idx,
                                )
                                processor_messages.setdefault(f"{proc_name}::__dq_write__", repr(exc))
                                stats = ChunkProcessingStats(
                                    rows_read=stats.rows_read,
                                    rows_missing_timestamp=stats.rows_missing_timestamp,
                                    rows_invalid_timestamp=stats.rows_invalid_timestamp,
                                    rows_before_cursor=stats.rows_before_cursor,
                                    raw_backups_written=stats.raw_backups_written,
                                    silver_fail_groups=stats.silver_fail_groups,
                                    chunks_all_before_cursor=stats.chunks_all_before_cursor,
                                    files_stopped_early=stats.files_stopped_early,
                                    write_failures=stats.write_failures + 1,
                                    raw_write_failures=stats.raw_write_failures,
                                    silver_write_failures=stats.silver_write_failures + 1,
                                )

                if chunk_success and pd.notna(chunk_max_ts):
                    if pending_cursor_ts is None or chunk_max_ts > pending_cursor_ts:
                        pending_cursor_ts = chunk_max_ts

            if stop_current_file_early:
                continue

        if not wrote_any:
            if backup_raw:
                logger.info(
                    "Audit raw backup was enabled, but no eligible rows remained after timestamp and delta filtering"
                )
            logger.info("No new audit rows found after %s", last_update)
            return "No new audit rows"

        if backup_raw and stats.raw_backups_written == 0:
            logger.info(
                "Audit raw backup was enabled, but no chunk reached the raw backup write step"
            )

        if pending_cursor_ts is not None and pd.notna(pending_cursor_ts):
            logger.info("Updating audit cursor to %s", pending_cursor_ts.isoformat())
            self._update_audit_delta(ctx.local_client, pending_cursor_ts.isoformat())
        else:
            logger.warning(
                "Skipping cursor update because no chunk completed successfully; max_ts_seen=%s",
                max_ts_seen,
            )

        processor_results: list[ProcessorResult] = []
        for name in processor_names:
            agg = processor_results_by_name.get(name, {})
            processor_results.append(
                ProcessorResult(
                    name,
                    int(agg.get("rows_in", 0)),
                    int(agg.get("rows_out", 0)),
                    int(agg.get("wrote_groups", 0)),
                    message=processor_messages.get(name),
                )
            )

        rt = ResultTable()
        rt.add_column(1, "processor", "STRING")
        rt.add_column(2, "rows_in", "STRING")
        rt.add_column(3, "rows_out", "STRING")
        rt.add_column(4, "wrote_groups", "STRING")
        rt.add_column(5, "message", "STRING")

        for result in processor_results:
            rt.add_record([
                result.name,
                str(result.rows_in),
                str(result.rows_out),
                str(result.wrote_groups),
                str(result.message or ""),
            ])

        rt.add_record([
            "__summary__",
            str(stats.rows_read),
            str(stats.rows_invalid_timestamp),
            str(sum(item.wrote_groups for item in processor_results)),
            (
                f"files_scanned={files_scanned}; chunks_scanned={chunks_scanned}; "
                f"available_file_count={available_file_count}; selected_file_count={len(files)}; "
                f"selected_oldest_mtime={(selected_oldest_mtime.isoformat() if selected_oldest_mtime is not None else '')}; "
                f"selected_newest_mtime={(selected_newest_mtime.isoformat() if selected_newest_mtime is not None else '')}; "
                f"missing_timestamp_rows={stats.rows_missing_timestamp}; "
                f"before_cursor_rows={stats.rows_before_cursor}; "
                f"chunks_all_before_cursor={stats.chunks_all_before_cursor}; "
                f"files_stopped_early={stats.files_stopped_early}; "
                f"raw_backups_written={stats.raw_backups_written}; "
                f"write_failures={stats.write_failures}; "
                f"raw_write_failures={stats.raw_write_failures}; "
                f"silver_write_failures={stats.silver_write_failures}; "
                f"silver_fail_groups={stats.silver_fail_groups}; "
                f"processor_failures={processor_failures}; "
                f"cursor_from={last_update.isoformat()}; "
                f"cursor_to={(pending_cursor_ts.isoformat() if pending_cursor_ts is not None else '')}"
            ),
        ])

        return rt
