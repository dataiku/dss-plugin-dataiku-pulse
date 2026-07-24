from __future__ import annotations

import datetime as dt
import importlib
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any

import dataiku
import pandas as pd
from dataiku.runnables import ResultTable, Runnable

from data_collection.data_normalizer import check_silver_dq, normalize_silver
from data_collection.helper import OutputLayout, build_context, ensure_output_folder, upload_json_gzip, upload_parquet

logger = logging.getLogger(__name__)

PARTITION_DATE_PATTERN = re.compile(r"^(\d{4})-(\d{2})$")


@dataclass(frozen=True)
class ProcessorResult:
    name: str
    rows_in: int
    rows_out: int
    wrote_groups: int
    message: str | None = None


@dataclass(frozen=True)
class HistoryProcessingStats:
    rows_read: int = 0
    rows_missing_timestamp: int = 0
    rows_invalid_timestamp: int = 0
    rows_out_of_range: int = 0
    files_scanned: int = 0
    files_failed: int = 0
    files_empty: int = 0
    chunks_processed: int = 0
    silver_fail_groups: int = 0
    processor_failures: int = 0


def _parse_timestamp(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        from data_collection.data_normalizer.casting import _detect_epoch_unit

        unit = _detect_epoch_unit(series)
        return pd.to_datetime(series, unit=unit, utc=True, errors="coerce").dt.floor("s")

    series_obj = series.astype("object")
    dt_series = pd.to_datetime(series_obj, utc=True, errors="coerce")

    if dt_series.notna().sum() == 0 and series_obj.notna().sum() > 0:
        from data_collection.data_normalizer.casting import _detect_epoch_unit

        unit = _detect_epoch_unit(series_obj)
        dt_series = pd.to_datetime(
            pd.to_numeric(series_obj, errors="coerce"),
            unit=unit,
            utc=True,
            errors="coerce",
        )

    return dt_series.dt.floor("s")


def _load_yaml_list(path: Path) -> list[str]:
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    raise ValueError(f"Expected YAML list in {path}, got {type(raw)!r}")


def _load_processor_names() -> list[str]:
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


def _first_non_empty_series(series_list: list[pd.Series], *, index: pd.Index) -> pd.Series:
    if not series_list:
        return pd.Series(pd.NA, index=index, dtype="object")

    out = pd.Series(pd.NA, index=index, dtype="object")
    for series in series_list:
        if isinstance(series, pd.DataFrame):
            series = _coerce_to_single_series(series, index=index)

        normalized = pd.Series(series, copy=False)
        if not normalized.index.equals(index):
            normalized = normalized.reindex(index)
        normalized = normalized.astype("object")
        mask = out.isna() & normalized.notna()
        if mask.any():
            out.loc[mask] = normalized.loc[mask]
    return out


def _coerce_to_single_series(value: pd.Series | pd.DataFrame, *, index: pd.Index) -> pd.Series:
    if isinstance(value, pd.Series):
        return value

    if value.shape[1] == 0:
        return pd.Series(pd.NA, index=index, dtype="object")

    logger.warning(
        "Collapsing duplicate-named columns into one alias source: columns=%s count=%s",
        [str(column) for column in value.columns],
        value.shape[1],
    )

    duplicated_sources = [value.iloc[:, column_idx] for column_idx in range(value.shape[1])]
    return _first_non_empty_series(duplicated_sources, index=index)


def _find_duplicate_column_names(df: pd.DataFrame) -> list[str]:
    duplicate_mask = df.columns.duplicated(keep=False)
    return [str(column) for column in df.columns[duplicate_mask].tolist()]


def _series_has_values(series: pd.Series) -> pd.Series:
    if series.dtype == "object":
        as_str = series.astype("string")
        return series.notna() & as_str.fillna("").str.strip().ne("")
    return series.notna()


def _collect_alias_sources(df: pd.DataFrame, candidate_names: list[str]) -> list[pd.Series]:
    sources: list[pd.Series] = []
    normalized_lookup = {str(column).replace(".", "").replace("_", "").lower(): column for column in df.columns}

    seen_columns: set[Any] = set()
    for candidate in candidate_names:
        if candidate in df.columns and candidate not in seen_columns:
            sources.append(_coerce_to_single_series(df[candidate], index=df.index))
            seen_columns.add(candidate)
            continue

        normalized_candidate = str(candidate).replace(".", "").replace("_", "").lower()
        matched_column = normalized_lookup.get(normalized_candidate)
        if matched_column is not None and matched_column not in seen_columns:
            sources.append(_coerce_to_single_series(df[matched_column], index=df.index))
            seen_columns.add(matched_column)

    return sources


def _normalize_history_chunk_shape(chunk: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    out = chunk.copy()
    stats = {
        "message_flattened_rows": 0,
        "client_event_flattened_rows": 0,
        "message_backfilled_from_client_event_rows": 0,
        "topic_backfilled_rows": 0,
        "timestamp_backfilled_from_server_timestamp_rows": 0,
        "message_msgtype_backfilled_rows": 0,
    }

    if "message" in out.columns:
        message_series = out["message"]
        message_mask = message_series.apply(lambda value: isinstance(value, dict))
        if bool(message_mask.any()):
            normalized_message = pd.json_normalize(message_series.where(message_mask, None)).add_prefix("message_")
            normalized_message.index = out.index
            for column in normalized_message.columns:
                if column not in out.columns:
                    out[column] = normalized_message[column]
                else:
                    existing = out[column]
                    fill_mask = ~_series_has_values(existing) & _series_has_values(normalized_message[column])
                    if bool(fill_mask.any()):
                        out.loc[fill_mask, column] = normalized_message.loc[fill_mask, column]
            stats["message_flattened_rows"] = int(message_mask.sum())

    if "clientEvent" in out.columns:
        client_event_series = out["clientEvent"]
        client_event_mask = client_event_series.apply(lambda value: isinstance(value, dict))
        if bool(client_event_mask.any()):
            normalized_client_event = pd.json_normalize(client_event_series.where(client_event_mask, None)).add_prefix(
                "clientEvent_"
            )
            normalized_client_event.index = out.index
            for column in normalized_client_event.columns:
                if column not in out.columns:
                    out[column] = normalized_client_event[column]
                else:
                    existing = out[column]
                    fill_mask = ~_series_has_values(existing) & _series_has_values(normalized_client_event[column])
                    if bool(fill_mask.any()):
                        out.loc[fill_mask, column] = normalized_client_event.loc[fill_mask, column]
            stats["client_event_flattened_rows"] = int(client_event_mask.sum())

    client_event_columns = [
        column
        for column in out.columns
        if str(column).startswith("clientEvent.") or str(column).startswith("clientEvent_")
    ]
    backfilled_message_rows = pd.Series(False, index=out.index)
    for column in client_event_columns:
        column_str = str(column)
        if column_str.startswith("clientEvent."):
            suffix = column_str.split(".", 1)[1].strip()
        else:
            suffix = column_str.split("_", 1)[1].strip()
        if not suffix:
            continue
        message_column = f"message_{suffix}"
        source = out[column]
        if message_column not in out.columns:
            out[message_column] = source
            backfilled_message_rows = backfilled_message_rows | _series_has_values(source)
            continue

        fill_mask = ~_series_has_values(out[message_column]) & _series_has_values(source)
        if bool(fill_mask.any()):
            out.loc[fill_mask, message_column] = source.loc[fill_mask]
            backfilled_message_rows = backfilled_message_rows | fill_mask

    stats["message_backfilled_from_client_event_rows"] = int(backfilled_message_rows.sum())

    topic_sources = _collect_alias_sources(
        out,
        [
            "topic",
            "clientEvent.auditTopic",
            "clientEvent_auditTopic",
            "clientevent.auditTopic",
            "clientevent_auditTopic",
            "message_auditTopic",
            "message.auditTopic",
            "auditTopic",
            "audittopic",
        ],
    )
    if "topic" not in out.columns:
        topic_series = _first_non_empty_series(topic_sources, index=out.index)
        out["topic"] = topic_series
        stats["topic_backfilled_rows"] = int(_series_has_values(topic_series).sum())
    else:
        existing_topic = out["topic"]
        topic_series = _first_non_empty_series(topic_sources, index=out.index)
        fill_mask = ~_series_has_values(existing_topic) & _series_has_values(topic_series)
        if bool(fill_mask.any()):
            out.loc[fill_mask, "topic"] = topic_series.loc[fill_mask]
            stats["topic_backfilled_rows"] = int(fill_mask.sum())

    if "timestamp" not in out.columns:
        timestamp_series = out["serverTimestamp"] if "serverTimestamp" in out.columns else pd.Series(pd.NA, index=out.index)
        out["timestamp"] = timestamp_series
        stats["timestamp_backfilled_from_server_timestamp_rows"] = int(_series_has_values(timestamp_series).sum())
    elif "serverTimestamp" in out.columns:
        fill_mask = ~_series_has_values(out["timestamp"]) & _series_has_values(out["serverTimestamp"])
        if bool(fill_mask.any()):
            out.loc[fill_mask, "timestamp"] = out.loc[fill_mask, "serverTimestamp"]
            stats["timestamp_backfilled_from_server_timestamp_rows"] = int(fill_mask.sum())

    msgtype_sources = _collect_alias_sources(
        out,
        [
            "message_msgType",
            "message.msgType",
            "clientEvent.msgType",
            "clientEvent_msgType",
            "clientevent.msgType",
            "clientevent_msgType",
            "msgType",
            "msgtype",
        ],
    )

    if "message_msgType" not in out.columns:
        msgtype_series = _first_non_empty_series(msgtype_sources, index=out.index)
        out["message_msgType"] = msgtype_series
        stats["message_msgtype_backfilled_rows"] = int(_series_has_values(msgtype_series).sum())
    else:
        msgtype_series = _first_non_empty_series(msgtype_sources, index=out.index)
        fill_mask = ~_series_has_values(out["message_msgType"]) & _series_has_values(msgtype_series)
        if bool(fill_mask.any()):
            out.loc[fill_mask, "message_msgType"] = msgtype_series.loc[fill_mask]
            stats["message_msgtype_backfilled_rows"] = int(fill_mask.sum())

    canonical_backfills = {
        "message_msgTypeBase": [
            "message_msgTypeBase",
            "message.msgTypeBase",
            "clientEvent.msgTypeBase",
            "clientEvent_msgTypeBase",
            "clientevent.msgTypeBase",
            "clientevent_msgTypeBase",
            "msgTypeBase",
            "msgtypebase",
        ],
        "message_authSource": [
            "message_authSource",
            "message.authSource",
            "clientEvent.authSource",
            "clientEvent_authSource",
            "clientevent.authSource",
            "clientevent_authSource",
            "authSource",
            "authsource",
        ],
        "message_authUser": [
            "message_authUser",
            "message.authUser",
            "clientEvent.authUser",
            "clientEvent_authUser",
            "clientevent.authUser",
            "clientevent_authUser",
            "authUser",
            "authuser",
        ],
        "message_projectKey": [
            "message_projectKey",
            "message.projectKey",
            "clientEvent.projectKey",
            "clientEvent_projectKey",
            "clientevent.projectKey",
            "clientevent_projectKey",
            "projectKey",
            "projectkey",
        ],
    }

    for canonical_name, aliases in canonical_backfills.items():
        canonical_sources = _collect_alias_sources(out, aliases)
        if canonical_name not in out.columns:
            out[canonical_name] = _first_non_empty_series(canonical_sources, index=out.index)
            continue

        canonical_series = _first_non_empty_series(canonical_sources, index=out.index)
        fill_mask = ~_series_has_values(out[canonical_name]) & _series_has_values(canonical_series)
        if bool(fill_mask.any()):
            out.loc[fill_mask, canonical_name] = canonical_series.loc[fill_mask]

    return out, stats


def _cleanup_history_processor_output(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    duplicate_columns = _find_duplicate_column_names(out)
    if duplicate_columns:
        logger.warning(
            "Processor output contains duplicate columns before cleanup: duplicates=%s row_count=%s total_columns=%s",
            duplicate_columns,
            out.shape[0],
            out.shape[1],
        )

    duplicate_pairs = {
        "msgtypebase": ["msgtypebase_x", "msgtypebase_y"],
    }

    for canonical_name, variants in duplicate_pairs.items():
        present = [column for column in variants if column in out.columns]
        if not present:
            continue
        merged = _first_non_empty_series([out[column] for column in present], index=out.index)
        out[canonical_name] = merged
        out = out.drop(columns=present, errors="ignore")

    raw_drop_columns = {
        "clientevent",
        "message",
        "mdc",
        "servertimestamp",
        "origaddress",
        "date",
    }
    out = out.drop(columns=[column for column in out.columns if str(column).lower() in raw_drop_columns], errors="ignore")

    source_prefixes = ("clientevent_",)
    keep_source_columns = {
        "clientevent_msgtype",
        "clientevent_audittopic",
        "clientevent_authsource",
        "clientevent_authuser",
        "clientevent_projectkey",
    }
    source_columns_to_drop = [
        column
        for column in out.columns
        if str(column).lower().startswith(source_prefixes) and str(column).lower() not in keep_source_columns
    ]
    out = out.drop(columns=source_columns_to_drop, errors="ignore")

    canonical_fill_pairs = {
        "msgtype": ["msgtype", "clientevent_msgtype"],
        "audittopic": ["audittopic", "clientevent_audittopic"],
        "authsource": ["authsource", "clientevent_authsource"],
        "authuser": ["authuser", "clientevent_authuser"],
        "project_key": ["project_key", "projectkey", "clientevent_projectkey"],
    }

    for canonical_name, variants in canonical_fill_pairs.items():
        sources = [out[column] for column in variants if column in out.columns]
        if not sources:
            continue
        merged = _first_non_empty_series(sources, index=out.index)
        out[canonical_name] = merged

    out = out.drop(
        columns=[column for column in keep_source_columns if column in out.columns],
        errors="ignore",
    )

    return out


class MyRunnable(Runnable):
    def __init__(self, project_key: str, config: dict[str, Any], plugin_config: dict[str, Any]):
        self.project_key = project_key
        self.config = config or {}
        self.plugin_config = plugin_config or {}
        self.param_set = self.plugin_config.get("pulse_primary", {}) or {}

    def get_progress_target(self):
        return None

    def _require_string(self, key: str, label: str) -> str:
        value = str(self.config.get(key) or "").strip()
        if not value:
            raise ValueError(f"Missing required parameter: {label}")
        return value

    def _open_managed_folder(self, folder_id: str) -> tuple[dataiku.Folder, str, list[str]]:
        try:
            folder = dataiku.Folder(
                lookup=folder_id,
                project_key=self.project_key,
                ignore_flow=True,
            )
            resolved_folder_id = folder.get_id()
        except Exception as exc:
            raise ValueError(
                f"Managed folder {folder_id!r} could not be resolved in project {self.project_key!r}. "
                "Check that the folder id is correct and that you have access to it."
            ) from exc

        try:
            partitions = folder.list_partitions() or []
        except Exception as exc:
            raise ValueError(
                f"Managed folder {resolved_folder_id!r} was resolved but could not list partitions. "
                "Check managed folder permissions and accessibility."
            ) from exc

        return folder, resolved_folder_id, partitions

    @staticmethod
    def _build_partition_frame(partitions: list[str]) -> pd.DataFrame:
        if not partitions:
            return pd.DataFrame(columns=["node_id", "partition"])

        partition_df = pd.DataFrame({"partition": partitions})
        split_columns = partition_df["partition"].str.split("|", expand=True)
        split_columns = split_columns.rename(columns={0: "node_id"})
        partition_df = pd.concat([partition_df, split_columns], axis=1)
        partition_df["node_id"] = partition_df["node_id"].fillna("").astype(str).str.strip()
        partition_df = partition_df[partition_df["node_id"] != ""].copy()
        return partition_df

    @staticmethod
    def _list_paths_for_partitions(folder: dataiku.Folder, partitions: list[str]) -> list[str]:
        paths: list[str] = []
        for partition in partitions:
            for path in folder.list_paths_in_partition(partition) or []:
                paths.append(str(path))
        return sorted(set(paths))

    @staticmethod
    def _parse_optional_date(value: Any, label: str) -> dt.date | None:
        if not value:
            return None

        raw_value = str(value).strip()
        if not raw_value:
            return None

        normalized = raw_value.replace("Z", "+00:00")
        try:
            return dt.datetime.fromisoformat(normalized).date()
        except ValueError as exc:
            raise ValueError(f"Invalid {label}: {raw_value!r}") from exc

    @staticmethod
    def _extract_partition_month(partition: str) -> dt.date | None:
        pieces = partition.split("|", 1)
        if len(pieces) < 2:
            return None

        match = PARTITION_DATE_PATTERN.match(pieces[1].strip())
        if not match:
            return None

        return dt.date(int(match.group(1)), int(match.group(2)), 1)

    def _discover_candidate_files(self, selected_partitions: list[str], matching_paths: list[str]) -> list[dict[str, Any]]:
        partition_by_path: dict[str, str] = {}
        for partition in selected_partitions:
            partition_prefix = f"/{partition}/"
            alt_prefix = f"{partition}/"
            for path in matching_paths:
                normalized_path = str(path)
                if partition_prefix in normalized_path or normalized_path.startswith(alt_prefix):
                    partition_by_path[normalized_path] = partition

        candidates: list[dict[str, Any]] = []
        for path in matching_paths:
            normalized_path = str(path)
            if not normalized_path.lower().endswith(".gz"):
                continue

            partition = partition_by_path.get(normalized_path, "")
            candidates.append(
                {
                    "path": normalized_path,
                    "partition": partition,
                    "partition_month": self._extract_partition_month(partition),
                }
            )

        return candidates

    @staticmethod
    def _filter_candidate_files(
        candidates: list[dict[str, Any]],
        *,
        start_date: dt.date | None,
        end_date: dt.date | None,
    ) -> list[dict[str, Any]]:
        if start_date and end_date and end_date < start_date:
            raise ValueError(
                f"Invalid date range: end_date {end_date.isoformat()} is before start_date {start_date.isoformat()}"
            )

        filtered: list[dict[str, Any]] = []
        for candidate in candidates:
            partition_month = candidate.get("partition_month")
            if partition_month is None:
                filtered.append(candidate)
                continue

            if start_date and partition_month < start_date.replace(day=1):
                continue
            if end_date and partition_month > end_date.replace(day=1):
                continue

            filtered.append(candidate)

        return filtered

    @staticmethod
    def _row_in_requested_range(
        timestamp_series: pd.Series,
        *,
        start_date: dt.date | None,
        end_date: dt.date | None,
    ) -> pd.Series:
        lower_bound = pd.Timestamp(start_date, tz="UTC") if start_date else None
        upper_bound = None
        if end_date:
            upper_bound = pd.Timestamp(end_date, tz="UTC") + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)

        mask = pd.Series(True, index=timestamp_series.index)
        if lower_bound is not None:
            mask = mask & (timestamp_series >= lower_bound)
        if upper_bound is not None:
            mask = mask & (timestamp_series <= upper_bound)
        return mask

    def _build_partition_dir(
        self,
        *,
        layout: OutputLayout,
        layer: str,
        proc_name: str,
        module_name: str,
        instance_name: str,
        event_date: dt.date,
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

    def _process_chunk(
        self,
        *,
        chunk: pd.DataFrame,
        chunk_idx: int,
        run_ts: str,
        run_epoch_ms: int,
        instance_name: str,
        target: Any,
        layout: OutputLayout,
        processor_names: list[str],
        processors: dict[str, Any],
        processor_messages: dict[str, str],
        processor_results_by_name: dict[str, dict[str, int]],
        stats: HistoryProcessingStats,
        start_date: dt.date | None,
        end_date: dt.date | None,
    ) -> tuple[HistoryProcessingStats, int]:
        rows_written = 0
        if chunk is None or chunk.shape[0] == 0:
            return stats, rows_written

        chunk, normalization_stats = _normalize_history_chunk_shape(chunk)
        stats = HistoryProcessingStats(
            rows_read=stats.rows_read + int(chunk.shape[0]),
            rows_missing_timestamp=stats.rows_missing_timestamp,
            rows_invalid_timestamp=stats.rows_invalid_timestamp,
            rows_out_of_range=stats.rows_out_of_range,
            files_scanned=stats.files_scanned,
            files_failed=stats.files_failed,
            files_empty=stats.files_empty,
            chunks_processed=stats.chunks_processed + 1,
            silver_fail_groups=stats.silver_fail_groups,
            processor_failures=stats.processor_failures,
        )

        if "timestamp" not in chunk.columns:
            return (
                HistoryProcessingStats(
                    rows_read=stats.rows_read,
                    rows_missing_timestamp=stats.rows_missing_timestamp + int(chunk.shape[0]),
                    rows_invalid_timestamp=stats.rows_invalid_timestamp,
                    rows_out_of_range=stats.rows_out_of_range,
                    files_scanned=stats.files_scanned,
                    files_failed=stats.files_failed,
                    files_empty=stats.files_empty,
                    chunks_processed=stats.chunks_processed,
                    silver_fail_groups=stats.silver_fail_groups,
                    processor_failures=stats.processor_failures,
                ),
                rows_written,
            )

        parsed_ts = _parse_timestamp(chunk["timestamp"])
        invalid_ts = int(parsed_ts.isna().sum())
        chunk = chunk.copy()
        chunk["timestamp"] = parsed_ts
        chunk = chunk[chunk["timestamp"].notna()].copy()
        stats = HistoryProcessingStats(
            rows_read=stats.rows_read,
            rows_missing_timestamp=stats.rows_missing_timestamp,
            rows_invalid_timestamp=stats.rows_invalid_timestamp + invalid_ts,
            rows_out_of_range=stats.rows_out_of_range,
            files_scanned=stats.files_scanned,
            files_failed=stats.files_failed,
            files_empty=stats.files_empty,
            chunks_processed=stats.chunks_processed,
            silver_fail_groups=stats.silver_fail_groups,
            processor_failures=stats.processor_failures,
        )
        if chunk.shape[0] == 0:
            return stats, rows_written

        in_range_mask = self._row_in_requested_range(chunk["timestamp"], start_date=start_date, end_date=end_date)
        rows_out_of_range = int((~in_range_mask).sum())
        chunk = chunk[in_range_mask].copy()
        stats = HistoryProcessingStats(
            rows_read=stats.rows_read,
            rows_missing_timestamp=stats.rows_missing_timestamp,
            rows_invalid_timestamp=stats.rows_invalid_timestamp,
            rows_out_of_range=stats.rows_out_of_range + rows_out_of_range,
            files_scanned=stats.files_scanned,
            files_failed=stats.files_failed,
            files_empty=stats.files_empty,
            chunks_processed=stats.chunks_processed,
            silver_fail_groups=stats.silver_fail_groups,
            processor_failures=stats.processor_failures,
        )
        if chunk.shape[0] == 0:
            return stats, rows_written

        chunk_max_ts = pd.Timestamp(chunk["timestamp"].max())

        if "message" in chunk.columns:
            jdf = pd.json_normalize(chunk["message"]).add_prefix("message_").reset_index(drop=True)
            drop_cols = [col for col in ["message", "mdc"] if col in chunk.columns]
            chunk = chunk.drop(columns=drop_cols).reset_index(drop=True)
            chunk = pd.concat([chunk, jdf], axis=1)

        chunk["date"] = chunk["timestamp"].dt.date
        chunk["instance_name"] = instance_name

        for proc_name in processor_names:
            mod = processors.get(proc_name)
            if mod is None:
                stats = HistoryProcessingStats(
                    rows_read=stats.rows_read,
                    rows_missing_timestamp=stats.rows_missing_timestamp,
                    rows_invalid_timestamp=stats.rows_invalid_timestamp,
                    rows_out_of_range=stats.rows_out_of_range,
                    files_scanned=stats.files_scanned,
                    files_failed=stats.files_failed,
                    files_empty=stats.files_empty,
                    chunks_processed=stats.chunks_processed,
                    silver_fail_groups=stats.silver_fail_groups,
                    processor_failures=stats.processor_failures + 1,
                )
                continue

            try:
                out_df = mod.main(chunk)
            except Exception as exc:
                logger.exception("Processor %s failed on history chunk %s", proc_name, chunk_idx)
                processor_messages.setdefault(proc_name, repr(exc))
                stats = HistoryProcessingStats(
                    rows_read=stats.rows_read,
                    rows_missing_timestamp=stats.rows_missing_timestamp,
                    rows_invalid_timestamp=stats.rows_invalid_timestamp,
                    rows_out_of_range=stats.rows_out_of_range,
                    files_scanned=stats.files_scanned,
                    files_failed=stats.files_failed,
                    files_empty=stats.files_empty,
                    chunks_processed=stats.chunks_processed,
                    silver_fail_groups=stats.silver_fail_groups,
                    processor_failures=stats.processor_failures + 1,
                )
                continue

            processor_results_by_name[proc_name]["rows_in"] += int(chunk.shape[0])

            if out_df is None or not isinstance(out_df, pd.DataFrame) or out_df.shape[0] == 0:
                continue

            processor_results_by_name[proc_name]["rows_out"] += int(out_df.shape[0])

            if "dataiku_category" not in out_df.columns:
                processor_messages.setdefault(proc_name, "missing dataiku_category")
                stats = HistoryProcessingStats(
                    rows_read=stats.rows_read,
                    rows_missing_timestamp=stats.rows_missing_timestamp,
                    rows_invalid_timestamp=stats.rows_invalid_timestamp,
                    rows_out_of_range=stats.rows_out_of_range,
                    files_scanned=stats.files_scanned,
                    files_failed=stats.files_failed,
                    files_empty=stats.files_empty,
                    chunks_processed=stats.chunks_processed,
                    silver_fail_groups=stats.silver_fail_groups,
                    processor_failures=stats.processor_failures + 1,
                )
                continue

            for module_name, grp in out_df.groupby("dataiku_category"):
                output_category = proc_name
                output_module = str(module_name)
                duplicate_columns = _find_duplicate_column_names(grp)
                if duplicate_columns:
                    logger.warning(
                        "Duplicate columns detected in history processor output: processor=%s category=%s duplicates=%s row_count=%s total_columns=%s",
                        proc_name,
                        output_module,
                        duplicate_columns,
                        grp.shape[0],
                        grp.shape[1],
                    )
                cleaned_grp = _cleanup_history_processor_output(grp)

                normalize_category = output_category
                normalize_module = output_module
                normalize_variant = None
                normalize_base = None

                silver_df = normalize_silver(
                    df=cleaned_grp,
                    instance_name=instance_name,
                    run_ts=run_ts,
                    category=normalize_category,
                    module=normalize_module,
                    todo_section="audit",
                    flatten_base=normalize_base,
                    flatten_variant=normalize_variant,
                )

                parquet_name = f"audit_logs-history-{run_epoch_ms}-{chunk_idx}.parquet"
                dq_name = f"audit_logs-history-{run_epoch_ms}-{chunk_idx}.dq.json.gz"
                if "timestamp" in silver_df.columns:
                    event_ts = pd.to_datetime(silver_df["timestamp"].max(), utc=True, errors="coerce")
                    event_date = chunk_max_ts.date() if pd.isna(event_ts) else event_ts.date()
                else:
                    event_date = chunk_max_ts.date()

                silver_path = self._build_partition_dir(
                    layout=layout,
                    layer="silver",
                    proc_name=output_category,
                    module_name=output_module,
                    instance_name=instance_name,
                    event_date=event_date,
                ) / parquet_name
                silver_fail_reason_path = self._build_partition_dir(
                    layout=layout,
                    layer="silver_fail",
                    proc_name=output_category,
                    module_name=output_module,
                    instance_name=instance_name,
                    event_date=event_date,
                ) / dq_name

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
                    rows_written += int(silver_df.shape[0])
                else:
                    fail_path = self._build_partition_dir(
                        layout=layout,
                        layer="silver_fail",
                        proc_name=output_category,
                        module_name=output_module,
                        instance_name=instance_name,
                        event_date=event_date,
                    ) / parquet_name
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
                            "history_import": True,
                        },
                    )
                    stats = HistoryProcessingStats(
                        rows_read=stats.rows_read,
                        rows_missing_timestamp=stats.rows_missing_timestamp,
                        rows_invalid_timestamp=stats.rows_invalid_timestamp,
                        rows_out_of_range=stats.rows_out_of_range,
                        files_scanned=stats.files_scanned,
                        files_failed=stats.files_failed,
                        files_empty=stats.files_empty,
                        chunks_processed=stats.chunks_processed,
                        silver_fail_groups=stats.silver_fail_groups + 1,
                        processor_failures=stats.processor_failures,
                    )

        return stats, rows_written

    def run(self, progress_callback):
        folder_id = self._require_string("folder_id", "Managed Folder ID")
        node_id = self._require_string("node_id", "Event Server Node ID")
        instance_name = self._require_string("instance_name", "Instance Name Mapping")
        start_date = self._parse_optional_date(self.config.get("start_date"), "start_date")
        end_date = self._parse_optional_date(self.config.get("end_date"), "end_date")

        logger.info(
            "Starting historical event-server import for folder_id=%s node_id=%s instance_name=%s",
            folder_id,
            node_id,
            instance_name,
        )

        folder, resolved_folder_id, partitions = self._open_managed_folder(folder_id)
        partition_df = self._build_partition_frame(partitions)
        if partition_df.empty:
            raise ValueError(
                f"Managed folder {resolved_folder_id!r} does not expose any discoverable event-server partitions."
            )

        available_nodes = sorted(partition_df["node_id"].drop_duplicates().tolist())
        matching_partition_df = partition_df[partition_df["node_id"] == node_id].copy()
        if matching_partition_df.empty:
            available_nodes_text = ", ".join(available_nodes) or "<none>"
            raise ValueError(
                f"Event-server node {node_id!r} was not found in managed folder {resolved_folder_id!r}. "
                f"Available nodes: {available_nodes_text}"
            )

        selected_partitions = matching_partition_df["partition"].drop_duplicates().tolist()
        try:
            matching_paths = self._list_paths_for_partitions(folder, selected_partitions)
        except Exception as exc:
            raise ValueError(
                f"Managed folder {resolved_folder_id!r} matched node {node_id!r} but could not list files for its partitions."
            ) from exc

        if not matching_paths:
            raise ValueError(
                f"Managed folder {resolved_folder_id!r} matched node {node_id!r} but no files were found in its partitions."
            )

        candidate_files = self._discover_candidate_files(selected_partitions, matching_paths)
        filtered_candidate_files = self._filter_candidate_files(
            candidate_files,
            start_date=start_date,
            end_date=end_date,
        )

        if not filtered_candidate_files:
            rt = ResultTable()
            rt.add_column(1, "processor", "STRING")
            rt.add_column(2, "rows_in", "STRING")
            rt.add_column(3, "rows_out", "STRING")
            rt.add_column(4, "wrote_groups", "STRING")
            rt.add_column(5, "message", "STRING")
            rt.add_record([
                "__summary__",
                "0",
                "0",
                "0",
                (
                    f"folder_id={folder_id}; resolved_folder_id={resolved_folder_id}; node_id={node_id}; "
                    f"instance_name={instance_name}; node_found=true; selected_node_count=1; matched_path_count={len(matching_paths)}; "
                    f"candidate_gz_file_count={len(candidate_files)}; filtered_gz_file_count=0; "
                    f"requested_start_date={(start_date.isoformat() if start_date else '')}; "
                    f"requested_end_date={(end_date.isoformat() if end_date else '')}; history_cursor_update=disabled"
                ),
            ])
            return rt

        ctx = build_context(plugin_config=self.plugin_config)
        run_dt = datetime.now(timezone.utc)
        run_ts = run_dt.isoformat()
        run_epoch_ms = int(run_dt.timestamp() * 1000)
        chunk_size = int(self.param_set.get("pulse_audit_logs_chunk_size", 50_000))

        target = ensure_output_folder(param_set=self.param_set, remote_client=ctx.remote_client)
        layout = OutputLayout(base_dir=Path("partitioned_data"), module="audit_metadata")

        processor_names = _load_processor_names()
        processors, processor_messages = _load_processors(processor_names)
        processor_results_by_name: dict[str, dict[str, int]] = defaultdict(
            lambda: {"rows_in": 0, "rows_out": 0, "wrote_groups": 0}
        )

        stats = HistoryProcessingStats()
        total_rows_written = 0
        chunk_idx = 0

        for candidate in filtered_candidate_files:
            file_path = str(candidate["path"])
            stats = HistoryProcessingStats(
                rows_read=stats.rows_read,
                rows_missing_timestamp=stats.rows_missing_timestamp,
                rows_invalid_timestamp=stats.rows_invalid_timestamp,
                rows_out_of_range=stats.rows_out_of_range,
                files_scanned=stats.files_scanned + 1,
                files_failed=stats.files_failed,
                files_empty=stats.files_empty,
                chunks_processed=stats.chunks_processed,
                silver_fail_groups=stats.silver_fail_groups,
                processor_failures=stats.processor_failures,
            )

            saw_rows = False
            try:
                with folder.get_download_stream(file_path) as stream:
                    for chunk in pd.read_json(stream, lines=True, compression="gzip", chunksize=chunk_size):
                        if chunk is not None and chunk.shape[0] > 0:
                            saw_rows = True
                        chunk_idx += 1
                        stats, rows_written = self._process_chunk(
                            chunk=chunk,
                            chunk_idx=chunk_idx,
                            run_ts=run_ts,
                            run_epoch_ms=run_epoch_ms,
                            instance_name=instance_name,
                            target=target,
                            layout=layout,
                            processor_names=processor_names,
                            processors=processors,
                            processor_messages=processor_messages,
                            processor_results_by_name=processor_results_by_name,
                            stats=stats,
                            start_date=start_date,
                            end_date=end_date,
                        )
                        total_rows_written += rows_written
            except ValueError:
                raise
            except Exception as exc:
                logger.exception("Failed processing historical audit file %s", file_path)
                processor_messages.setdefault("__file__", repr(exc))
                stats = HistoryProcessingStats(
                    rows_read=stats.rows_read,
                    rows_missing_timestamp=stats.rows_missing_timestamp,
                    rows_invalid_timestamp=stats.rows_invalid_timestamp,
                    rows_out_of_range=stats.rows_out_of_range,
                    files_scanned=stats.files_scanned,
                    files_failed=stats.files_failed + 1,
                    files_empty=stats.files_empty,
                    chunks_processed=stats.chunks_processed,
                    silver_fail_groups=stats.silver_fail_groups,
                    processor_failures=stats.processor_failures,
                )
                continue

            if not saw_rows:
                stats = HistoryProcessingStats(
                    rows_read=stats.rows_read,
                    rows_missing_timestamp=stats.rows_missing_timestamp,
                    rows_invalid_timestamp=stats.rows_invalid_timestamp,
                    rows_out_of_range=stats.rows_out_of_range,
                    files_scanned=stats.files_scanned,
                    files_failed=stats.files_failed,
                    files_empty=stats.files_empty + 1,
                    chunks_processed=stats.chunks_processed,
                    silver_fail_groups=stats.silver_fail_groups,
                    processor_failures=stats.processor_failures,
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
            str(total_rows_written),
            str(sum(item.wrote_groups for item in processor_results)),
            (
                f"folder_id={folder_id}; resolved_folder_id={resolved_folder_id}; node_id={node_id}; "
                f"instance_name={instance_name}; node_found=true; selected_node_count=1; matched_partition_count={len(selected_partitions)}; "
                f"matched_path_count={len(matching_paths)}; candidate_gz_file_count={len(candidate_files)}; "
                f"filtered_gz_file_count={len(filtered_candidate_files)}; files_scanned={stats.files_scanned}; "
                f"files_failed={stats.files_failed}; files_empty={stats.files_empty}; chunks_processed={stats.chunks_processed}; "
                f"rows_missing_timestamp={stats.rows_missing_timestamp}; rows_invalid_timestamp={stats.rows_invalid_timestamp}; "
                f"rows_out_of_range={stats.rows_out_of_range}; rows_written_to_silver={total_rows_written}; "
                f"silver_fail_groups={stats.silver_fail_groups}; processor_failures={stats.processor_failures}; "
                f"requested_start_date={(start_date.isoformat() if start_date else '')}; "
                f"requested_end_date={(end_date.isoformat() if end_date else '')}; history_cursor_update=disabled"
            ),
        ])

        return rt
