from __future__ import annotations

import io
import json
import logging
import re
import tempfile
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from data_collection.audit_logs_modules.compact_silver_queue import _compact_queue_memory_limit_bytes
from data_collection.audit_logs_modules import event_mapping
from data_collection.data_normalizer import DQResult, check_silver_dq, normalize_silver
from data_collection.data_normalizer.extras import extras_to_json
from data_collection.data_normalizer.flatten_config import load_flatten_config
from data_collection.data_normalizer.schema_config import flatten_columns_search_dirs, normalize_column_names
from data_collection.helper import DSSFolderTarget, get_managed_folder_handle, upload_parquet
from shared_duckdb.create_conn import _duckdb_memory_limit_setting, _effective_memory_limit_bytes
from shared_storage_discovery import SelectedPartitionPaths, SelectedPathRecord
from shared_storage_parquet_s3 import COMPACT_SILVER_S3_READ_BATCH_SIZE, read_s3_parquet_file_batch

logger = logging.getLogger(__name__)

SILVER_EVENT_MAPPING_PREFIX = "/silver/category=event_mapping/"
SOURCE_FILENAME_PATTERN = re.compile(r"^(audit_logs-)(\d+)(-[^.]+\.parquet)$")
_LAST_REPLAY_SAVE_EPOCH_MS = 0
_REPLAY_SAVE_EPOCH_LOCK = threading.Lock()
_LAST_COMPACT_SAVE_EPOCH_MS = 0
_COMPACT_SAVE_EPOCH_LOCK = threading.Lock()
COMPACT_SILVER_OUTPUT_ROW_CHUNK_SIZE = 100_000
REPLACEMENT_EVIDENCE_SAMPLE_SIZE = 5
_STAGE_ORDER_COLUMN = "__compact_row_order"
COMPACT_PROCESSING_MODE_GENERIC = "generic_compaction"
COMPACT_PROCESSING_MODE_EVENT_MAPPING = "event_mapping_replay"
COMPACT_PROCESSING_MODE_MODULE_CONTRACT = "module_contract_normalization"


@dataclass(frozen=True)
class EventMappingSourceInfo:
    path: str
    module: str
    instance_name: str
    year: int
    month: int
    day: int
    filename: str

    @property
    def run_date(self) -> date:
        return date(self.year, self.month, self.day)


@dataclass(frozen=True)
class EventMappingReplayOutcome:
    status: str
    message: str
    source_path: str
    replacement_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReplayWritePlan:
    output_path: PurePosixPath
    silver_df: pd.DataFrame
    dq: DQResult
    module_name: str
    event_date: date
    expected_duckdb_types: dict[str, str] | None = None
    expected_arrow_types: dict[str, Any] | None = None


@dataclass(frozen=True)
class ReplacementUploadResult:
    status: str
    written_paths: tuple[str, ...] = ()
    cleanup_paths: tuple[str, ...] = ()
    dq_errors: tuple[str, ...] = ()
    message: str = ""


@dataclass(frozen=True)
class CompactPlanMetric:
    module_name: str
    rows: int
    columns: int
    dq_ok: bool
    dq_errors: tuple[str, ...]


@dataclass(frozen=True)
class CompactPlanSummary:
    mode: str
    run_epoch_ms: int
    input_rows: int
    input_columns: int
    rehydrated_rows: int | None = None
    rehydrated_columns: int | None = None
    mapper_rows: int | None = None
    mapper_columns: int | None = None
    mapper_groups: int = 0
    metrics: tuple[CompactPlanMetric, ...] = ()


@dataclass(frozen=True)
class CompactApplyResult:
    status: str
    written_paths: tuple[str, ...] = ()
    verified_paths: tuple[str, ...] = ()
    cleanup_paths: tuple[str, ...] = ()
    deleted_source_paths: tuple[str, ...] = ()
    retained_source_paths: tuple[str, ...] = ()
    dq_errors: tuple[str, ...] = ()
    message: str = ""


@dataclass(frozen=True)
class CompactStageResult:
    database_path: Path
    files_read: int
    raw_rows: int
    rows_after_drop_duplicates: int
    output_column_count: int


@dataclass(frozen=True)
class CompactProcessingResult:
    plan_summary: CompactPlanSummary
    apply_result: CompactApplyResult
    plan_count: int


@dataclass(frozen=True)
class CompactPartitionOutcome:
    year: str
    month: str
    day: str
    replay_mode: str
    status: str
    message: str
    run_epoch_ms: int
    files_read: int
    raw_rows: int
    rows_after_drop_duplicates: int
    output_column_count: int
    input_rows: int
    input_columns: int
    plan_count: int
    rehydrated_rows: int | None = None
    rehydrated_columns: int | None = None
    mapper_rows: int | None = None
    mapper_columns: int | None = None
    mapper_groups: int = 0
    metrics: tuple[CompactPlanMetric, ...] = ()
    written_count: int = 0
    verified_count: int = 0
    deleted_count: int = 0
    retained_count: int = 0

    @property
    def day_scope(self) -> str:
        return f"{self.year}/{self.month}/{self.day}"


class ReplaySkipError(ValueError):
    """Raised when a source file cannot be replayed safely."""


class ExtrasDecodeError(ReplaySkipError):
    """Raised when a SILVER extras payload is malformed or unsupported."""


def clean_managed_folder_path(path: str) -> str:
    normalized = str(path or "").strip()
    if not normalized:
        return ""
    return f"/{normalized.lstrip('/')}"


def list_managed_folder_paths(folder: Any) -> list[str]:
    if hasattr(folder, "list_paths_in_partition"):
        try:
            return [clean_managed_folder_path(path) for path in (folder.list_paths_in_partition() or [])]
        except TypeError:
            return [clean_managed_folder_path(path) for path in (folder.list_paths_in_partition("NP") or [])]

    if hasattr(folder, "list_contents"):
        payload = folder.list_contents() or {}
        items = payload.get("items") if isinstance(payload, dict) else []
        return [clean_managed_folder_path(item.get("path")) for item in items if isinstance(item, dict) and item.get("path")]

    raise TypeError(f"Unsupported folder handle type for listing: {type(folder)!r}")


def read_managed_folder_parquet(folder: Any, path: str) -> pd.DataFrame:
    cleaned = clean_managed_folder_path(path)
    if hasattr(folder, "get_download_stream"):
        with folder.get_download_stream(cleaned) as stream:
            return pd.read_parquet(io.BytesIO(stream.read()))

    raise TypeError(f"Unsupported folder handle type for parquet read: {type(folder)!r}")


def delete_managed_folder_file(folder: Any, path: str) -> None:
    cleaned = clean_managed_folder_path(path)
    if hasattr(folder, "delete_path"):
        folder.delete_path(cleaned)
        return
    if hasattr(folder, "delete_file"):
        folder.delete_file(cleaned)
        return
    raise TypeError(f"Unsupported folder handle type for delete: {type(folder)!r}")


def cleanup_written_replacements(*, folder: Any, paths: list[str]) -> tuple[tuple[str, ...], list[str]]:
    cleaned_paths: list[str] = []
    cleanup_errors: list[str] = []
    for replacement_index, path in enumerate(paths, start=1):
        try:
            delete_managed_folder_file(folder, path)
            cleaned_paths.append(path)
        except Exception as exc:
            logger.exception("Failed cleaning replay replacement %s", path)
            cleanup_errors.append(f"{path}: {exc!r}")
    return tuple(cleaned_paths), cleanup_errors


def format_partial_write_message(
    *,
    base_message: str,
    written_paths: tuple[str, ...],
    cleanup_paths: tuple[str, ...],
) -> str:
    parts = [base_message]
    if written_paths:
        parts.append(f"written_count={len(written_paths)}")
        parts.append(_format_replacement_path_sample(label="written_sample", paths=written_paths))
    if cleanup_paths:
        parts.append(f"cleanup_count={len(cleanup_paths)}")
        parts.append(_format_replacement_path_sample(label="cleanup_sample", paths=cleanup_paths))
    return "; ".join(parts)


def _format_replacement_path_sample(*, label: str, paths: tuple[str, ...]) -> str:
    sample = tuple(paths[:REPLACEMENT_EVIDENCE_SAMPLE_SIZE])
    omitted_count = max(0, len(paths) - len(sample))
    return f"{label}=[{', '.join(sample)}]; {label}_omitted_count={omitted_count}"


def discover_event_mapping_paths(folder: Any) -> list[str]:
    return sorted(
        path
        for path in list_managed_folder_paths(folder)
        if path.startswith(SILVER_EVENT_MAPPING_PREFIX) and path.endswith(".parquet")
    )


def parse_event_mapping_source_path(path: str) -> EventMappingSourceInfo:
    cleaned = clean_managed_folder_path(path)
    parts = PurePosixPath(cleaned).parts
    values: dict[str, str] = {}
    filename = None
    for part in parts[2:] if parts and parts[0] == "/" else parts[1:]:
        if "=" in part:
            key, value = part.split("=", 1)
            if key and value:
                values[key] = value
            continue
        filename = part

    required = ["module", "instance_name", "year", "month", "day"]
    missing = [key for key in required if not values.get(key)]
    if missing or not filename:
        raise ReplaySkipError(f"Unsupported event-mapping SILVER path layout: {cleaned}")

    return EventMappingSourceInfo(
        path=cleaned,
        module=values["module"],
        instance_name=values["instance_name"],
        year=int(values["year"]),
        month=int(values["month"]),
        day=int(values["day"]),
        filename=filename,
    )


def _decode_extras_value(value: Any) -> dict[str, Any]:
    if value is None or value is pd.NA:
        return {}
    if isinstance(value, float) and pd.isna(value):
        return {}
    if isinstance(value, str):
        if not value.strip():
            return {}
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ExtrasDecodeError(f"Invalid extras JSON: {exc.msg}") from exc
    elif isinstance(value, dict):
        payload = value
    else:
        raise ExtrasDecodeError(f"Unsupported extras type: {type(value).__name__}")

    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ExtrasDecodeError(f"Extras JSON must decode to an object, got {type(payload).__name__}")
    return payload


def rehydrate_event_mapping_source(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame):
        raise ReplaySkipError("Source parquet did not load as a dataframe")
    if df.shape[0] == 0:
        raise ReplaySkipError("Source parquet is empty")

    out = df.copy()
    extras_series = out["extras"] if "extras" in out.columns else pd.Series([None] * len(out), index=out.index)
    extras_rows = [_decode_extras_value(value) for value in extras_series.tolist()]

    base_rows: list[dict[str, Any]] = []
    for base_row, extras_row in zip(out.to_dict(orient="records"), extras_rows, strict=False):
        merged = dict(base_row)
        for key, value in extras_row.items():
            if key not in merged:
                merged[key] = value
        base_rows.append(merged)

    rehydrated = pd.DataFrame(base_rows)
    if "msgtype" in rehydrated.columns:
        rehydrated["message_msgType"] = rehydrated["msgtype"]
    stale_mapper_columns = [
        column_name
        for column_name in ["msgtype", "msgtypebase", "dataiku_category", "extras"]
        if column_name in rehydrated.columns
    ]
    if stale_mapper_columns:
        rehydrated = rehydrated.drop(columns=stale_mapper_columns)
    return rehydrated


def resolve_event_mapping_normalize_args(*, module_name: str) -> dict[str, Any]:
    return {
        "category": "audit_dataiku_usage",
        "module": "audit_metadata",
        "todo_section": "audit",
        "flatten_base": ("audit_dataiku_usage", "audit_metadata"),
        "flatten_variant": module_name,
    }


def _todo_section_for_compact_contract(*, category: str, module_name: str) -> str:
    if category == "users" and module_name == "instance_metadata":
        return "instance"
    if category == "users" and module_name == "user_activity":
        return "audit"
    if "instance" in module_name:
        return "instance"
    if "audit" in module_name or module_name.endswith("_activity"):
        return "audit"
    return "project"


def _required_columns_for_module_contract(*, category: str, module_name: str) -> list[str]:
    todo_section = _todo_section_for_compact_contract(category=category, module_name=module_name)
    contract_filename = f"{category}_{module_name}.yaml"
    contract_path = None
    for search_dir in flatten_columns_search_dirs():
        candidate = search_dir / contract_filename
        if candidate.exists():
            contract_path = candidate
            break
    if contract_path is None:
        raise ReplaySkipError(
            f"Unsupported compact SILVER normalization scope: missing canonical contract for category={category!r} module={module_name!r}"
        )
    cfg = load_flatten_config(
        category=category,
        module=module_name,
        flatten_columns_dir=contract_path.parent,
        todo_section=todo_section,
    )
    if cfg is None or not cfg.required_columns:
        raise ReplaySkipError(
            f"Unsupported compact SILVER normalization scope: missing canonical contract for category={category!r} module={module_name!r}"
        )
    required = [column for column in normalize_column_names(cfg.required_columns) if column not in {"instance_name", "run_ts"}]
    return ["instance_name", "run_ts", *required]


def _output_columns_for_module_contract(*, category: str, module_name: str) -> list[str]:
    required_columns = _required_columns_for_module_contract(
        category=category, module_name=module_name
    )
    required_without_globals = [
        column for column in required_columns if column not in {"instance_name", "run_ts"}
    ]
    return ["instance_name", *required_without_globals, "run_ts", "extras"]


def _canonical_arrow_types_for_module_contract(*, category: str, module_name: str) -> dict[str, Any]:
    empty_source = pd.DataFrame(columns=_output_columns_for_module_contract(category=category, module_name=module_name))
    normalized = normalize_module_contract_silver(
        df=empty_source,
        category=category,
        module_name=module_name,
        instance_name="__compact_schema_probe__",
    )
    schema = pa.Schema.from_pandas(normalized, preserve_index=False)
    return {field.name: field.type for field in schema}


def _expected_arrow_types_for_module_contract(*, category: str, module_name: str) -> dict[str, Any]:
    canonical_types = _canonical_arrow_types_for_module_contract(
        category=category, module_name=module_name
    )
    return {
        column_name: arrow_type
        for column_name, arrow_type in canonical_types.items()
        if pa.types.is_timestamp(arrow_type)
    }


def _is_nullish(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _values_are_equivalent(left: Any, right: Any) -> bool:
    if _is_nullish(left) and _is_nullish(right):
        return True
    if _is_nullish(left) or _is_nullish(right):
        return False
    return left == right


def _merge_existing_extras_into_source(df: pd.DataFrame, *, required_columns: list[str]) -> pd.DataFrame:
    if "extras" not in df.columns:
        return df
    if df.empty:
        return df.drop(columns=["extras"])

    source_rows = df.to_dict(orient="records")
    merged_rows: list[dict[str, Any]] = []
    for source_row in source_rows:
        merged = dict(source_row)
        extras_payload = _decode_extras_value(merged.get("extras"))
        for key, value in extras_payload.items():
            if key == "extras":
                raise ReplaySkipError(
                    "Cannot safely merge nested extras payload key 'extras' during Compact SILVER normalization"
                )
            existing_value = merged.get(key)
            existing_is_null = key not in merged or _is_nullish(existing_value)
            extra_is_null = _is_nullish(value)
            if existing_is_null and not extra_is_null:
                merged[key] = value
            elif not existing_is_null and not extra_is_null and not _values_are_equivalent(existing_value, value):
                column_kind = "contract" if key in required_columns else "noncontract"
                raise ReplaySkipError(
                    f"Conflicting non-null values for {column_kind} column {key!r} between top-level field and extras"
                )
        merged.pop("extras", None)
        merged_rows.append(merged)
    return pd.DataFrame(merged_rows)


def normalize_module_contract_silver(
    *, df: pd.DataFrame, category: str, module_name: str, instance_name: str
) -> pd.DataFrame:
    required_columns = _required_columns_for_module_contract(
        category=category, module_name=module_name
    )
    source_df = _merge_existing_extras_into_source(df.copy(), required_columns=required_columns)
    if "run_ts" not in source_df.columns:
        raise ReplaySkipError(
            f"Unsupported compact SILVER normalization scope: source category={category!r} module={module_name!r} is missing run_ts"
        )
    silver_df = normalize_silver(
        df=source_df,
        instance_name=instance_name,
        run_ts=None,
        category=category,
        module=module_name,
        todo_section=_todo_section_for_compact_contract(category=category, module_name=module_name),
    )
    if category == "users" and module_name == "user_activity" and "dataiku_category" in source_df.columns:
        silver_df = silver_df.copy()
        silver_df["dataiku_category"] = source_df["dataiku_category"].reset_index(drop=True)
    if "extras" in silver_df.columns:
        silver_df = silver_df.copy()
        silver_df["extras"] = silver_df["extras"].map(extras_to_json)
    return silver_df


def resolve_replay_event_date(*, silver_df: pd.DataFrame, fallback_date: date) -> date:
    if "timestamp" in silver_df.columns:
        event_ts = pd.to_datetime(silver_df["timestamp"].max(), utc=True, errors="coerce")
        if not pd.isna(event_ts):
            return event_ts.date()
    return fallback_date


def next_replay_save_epoch_ms() -> int:
    global _LAST_REPLAY_SAVE_EPOCH_MS

    with _REPLAY_SAVE_EPOCH_LOCK:
        current_epoch_ms = time.time_ns() // 1_000_000
        next_epoch_ms = (
            current_epoch_ms if current_epoch_ms > _LAST_REPLAY_SAVE_EPOCH_MS else _LAST_REPLAY_SAVE_EPOCH_MS + 1
        )
        _LAST_REPLAY_SAVE_EPOCH_MS = next_epoch_ms
        return next_epoch_ms


def next_compact_save_epoch_ms() -> int:
    global _LAST_COMPACT_SAVE_EPOCH_MS

    with _COMPACT_SAVE_EPOCH_LOCK:
        current_epoch_ms = time.time_ns() // 1_000_000
        next_epoch_ms = (
            current_epoch_ms if current_epoch_ms > _LAST_COMPACT_SAVE_EPOCH_MS else _LAST_COMPACT_SAVE_EPOCH_MS + 1
        )
        _LAST_COMPACT_SAVE_EPOCH_MS = next_epoch_ms
        return next_epoch_ms


def build_compact_replacement_filename(*, run_epoch_ms: int, sequence_number: int) -> str:
    if sequence_number <= 0:
        raise ValueError(f"sequence_number must be positive, got {sequence_number}")
    return f"compact_silver-{run_epoch_ms}-{sequence_number:04d}.parquet"


def build_compact_output_path(
    *,
    category: str,
    module_name: str,
    instance_name: str,
    event_date: date,
    run_epoch_ms: int,
    sequence_number: int,
) -> PurePosixPath:
    return (
        PurePosixPath("/")
        / "silver"
        / f"category={category}"
        / f"module={module_name}"
        / f"instance_name={instance_name}"
        / f"year={event_date.year:04d}"
        / f"month={event_date.month:02d}"
        / f"day={event_date.day:02d}"
        / build_compact_replacement_filename(run_epoch_ms=run_epoch_ms, sequence_number=sequence_number)
    )


def build_replacement_filename(*, source_filename: str, module_name: str, save_epoch_ms: int) -> str:
    match = SOURCE_FILENAME_PATTERN.match(source_filename)
    if match is None:
        raise ReplaySkipError(f"Unsupported event-mapping source filename: {source_filename}")
    return f"{match.group(1)}{save_epoch_ms}-{module_name}.parquet"


def build_event_mapping_output_path(*, source: EventMappingSourceInfo, module_name: str, event_date: date) -> PurePosixPath:
    replacement_filename = build_replacement_filename(
        source_filename=source.filename,
        module_name=module_name,
        save_epoch_ms=next_replay_save_epoch_ms(),
    )
    return (
        PurePosixPath("/")
        / "silver"
        / "category=event_mapping"
        / f"module={module_name}"
        / f"instance_name={source.instance_name}"
        / f"year={event_date.year:04d}"
        / f"month={event_date.month:02d}"
        / f"day={event_date.day:02d}"
        / replacement_filename
    )


def _resolve_replay_run_ts(source_df: pd.DataFrame, fallback_date: date, explicit_run_ts: str | None) -> str:
    if explicit_run_ts:
        return explicit_run_ts
    if "run_ts" in source_df.columns:
        non_null = source_df["run_ts"].dropna()
        if non_null.shape[0] > 0:
            return str(non_null.astype(str).iloc[0])
    return f"{fallback_date.isoformat()}T00:00:00Z"


def _selected_event_date(selected_records: list[SelectedPathRecord]) -> date:
    if not selected_records:
        raise ReplaySkipError("No selected source records were provided")
    record = selected_records[0]
    return date(int(record.year), int(record.month), int(record.day))


def _selected_partition_identity(selected_records: list[SelectedPathRecord]) -> tuple[str, str, str, str, str, str]:
    if not selected_records:
        raise ReplaySkipError("No selected source records were provided")
    record = selected_records[0]
    expected_identity = (
        record.category,
        record.module,
        record.instance_name,
        record.year,
        record.month,
        record.day,
    )
    for candidate in selected_records[1:]:
        candidate_identity = (
            candidate.category,
            candidate.module,
            candidate.instance_name,
            candidate.year,
            candidate.month,
            candidate.day,
        )
        if candidate_identity != expected_identity:
            raise ReplaySkipError(
                "Selected source records span multiple logical compact partitions; refusing mixed-instance/day compaction"
            )
    return expected_identity


def _validate_output_paths_do_not_overlap_sources(*, plans: list[ReplayWritePlan], selected_records: list[SelectedPathRecord]) -> None:
    source_paths = {record.relative_path for record in selected_records}
    for plan in plans:
        if str(plan.output_path) in source_paths:
            raise ReplaySkipError(f"Replacement output path overlaps selected source path: {plan.output_path}")


def _quote_duckdb_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _duckdb_columns(con: duckdb.DuckDBPyConnection, table_name: str) -> list[str]:
    quoted_table = _quote_duckdb_identifier(table_name)
    return [str(row[1]) for row in con.execute(f"PRAGMA table_info({quoted_table})").fetchall()]


def _compact_stage_duckdb_config(*, spill_dir: Path) -> dict[str, str]:
    spill_dir.mkdir(parents=True, exist_ok=True)
    effective_memory_bytes, memory_source = _effective_memory_limit_bytes()
    memory_limit_bytes = _compact_queue_memory_limit_bytes(effective_memory_bytes)
    memory_limit_setting = _duckdb_memory_limit_setting(memory_limit_bytes)
    config = {
        "temp_directory": str(spill_dir),
        "preserve_insertion_order": "false",
    }
    if memory_limit_setting:
        config["memory_limit"] = memory_limit_setting
    else:
        logger.warning(
            "Compact SILVER stage DuckDB effective memory unavailable; using DuckDB default memory limit memory_source=%s spill_directory=%s",
            memory_source,
            spill_dir,
        )
    return config


def _process_rss_mb() -> int | None:
    try:
        with open("/proc/self/status", encoding="utf-8") as status_file:
            for line in status_file:
                if not line.startswith("VmRSS:"):
                    continue
                parts = line.split()
                if len(parts) < 2:
                    return None
                rss_kb = int(parts[1])
                if rss_kb < 0:
                    return None
                return rss_kb // 1024
    except (OSError, ValueError):
        return None
    return None


def _stage_compact_s3_partition(
    *,
    storage_ctx: Any,
    storage_ctx_factory: Any | None,
    selected_partition: SelectedPartitionPaths,
    database_path: Path,
    s3_read_batch_size: int,
) -> CompactStageResult:
    total_files = len(selected_partition.selected_records)
    started = time.monotonic()
    spill_dir = database_path.parent / "duckdb-spill"
    duckdb_config = _compact_stage_duckdb_config(spill_dir=spill_dir)
    logger.info(
        "Compact SILVER partition staging started partition=%s source_file_count=%s s3_read_batch_size=%s stage_memory_limit=%s spill_directory=%s elapsed_seconds=%.3f rss_mb=%s",
        selected_partition.partition_scope,
        total_files,
        s3_read_batch_size,
        duckdb_config.get("memory_limit"),
        duckdb_config["temp_directory"],
        0.0,
        _process_rss_mb(),
    )

    con = duckdb.connect(str(database_path), config=duckdb_config)
    raw_created = False
    raw_rows = 0
    files_read = 0
    row_order_start = 0
    try:
        total_batches = (total_files + s3_read_batch_size - 1) // s3_read_batch_size
        for batch_number, record_batch in enumerate(
            _iter_record_batches(selected_partition.selected_records, batch_size=s3_read_batch_size),
            start=1,
        ):
            batch_started = time.monotonic()
            batch_storage_ctx = storage_ctx_factory() if storage_ctx_factory is not None else storage_ctx
            batch_full_paths = [record.full_path for record in record_batch]
            batch_df = read_s3_parquet_file_batch(batch_storage_ctx, full_paths=batch_full_paths)
            batch_df = batch_df.copy()
            batch_df[_STAGE_ORDER_COLUMN] = range(row_order_start, row_order_start + len(batch_df))
            row_order_start += len(batch_df)
            _append_dataframe_to_table(con, table_name="raw_stage", df=batch_df)
            raw_created = True

            batch_file_count = len(record_batch)
            raw_rows += int(len(batch_df))
            files_read += batch_file_count
            logger.info(
                "Compact SILVER S3 read batch completed partition=%s batch_number=%s total_batches=%s batch_file_count=%s cumulative_files=%s cumulative_rows=%s elapsed_seconds=%.3f rss_mb=%s",
                selected_partition.partition_scope,
                batch_number,
                total_batches,
                batch_file_count,
                files_read,
                raw_rows,
                time.monotonic() - batch_started,
                _process_rss_mb(),
            )
            del batch_df

        if not raw_created:
            raise ReplaySkipError("Selected parquet payload is empty")

        raw_columns = [column for column in _duckdb_columns(con, "raw_stage") if column != _STAGE_ORDER_COLUMN]
        if not raw_columns:
            raise ReplaySkipError("Selected parquet payload has no columns")
        source_column_sql = ", ".join(_quote_duckdb_identifier(column) for column in raw_columns)
        con.execute(
            f"""
            CREATE TABLE dedup_stage AS
            SELECT MIN({_quote_duckdb_identifier(_STAGE_ORDER_COLUMN)}) AS {_quote_duckdb_identifier(_STAGE_ORDER_COLUMN)}, {source_column_sql}
            FROM raw_stage
            GROUP BY {source_column_sql}
            ORDER BY {_quote_duckdb_identifier(_STAGE_ORDER_COLUMN)}
            """.strip()
        )
        rows_after_drop_duplicates = int(con.execute("SELECT COUNT(*) FROM dedup_stage").fetchone()[0])
        logger.info(
            "Compact SILVER partition staging completed partition=%s source_file_count=%s raw_rows=%s rows_after_drop_duplicates=%s elapsed_seconds=%.3f rss_mb=%s",
            selected_partition.partition_scope,
            total_files,
            raw_rows,
            rows_after_drop_duplicates,
            time.monotonic() - started,
            _process_rss_mb(),
        )
        return CompactStageResult(
            database_path=database_path,
            files_read=files_read,
            raw_rows=raw_rows,
            rows_after_drop_duplicates=rows_after_drop_duplicates,
            output_column_count=len(raw_columns),
        )
    finally:
        con.close()


def _iter_table_chunks(
    con: duckdb.DuckDBPyConnection,
    *,
    table_name: str,
    chunk_size: int,
    exclude_columns: set[str] | None = None,
) -> Iterator[pd.DataFrame]:
    if chunk_size <= 0:
        raise ValueError(f"Compact SILVER output chunk size must be positive, got {chunk_size}")
    exclude_columns = exclude_columns or set()
    output_columns = [column for column in _duckdb_columns(con, table_name) if column not in exclude_columns]
    if not output_columns:
        return
    output_sql = ", ".join(_quote_duckdb_identifier(column) for column in output_columns)
    quoted_table = _quote_duckdb_identifier(table_name)
    offset = 0
    while True:
        chunk_df = con.execute(
            f"""
            SELECT {output_sql}
            FROM {quoted_table}
            ORDER BY {_quote_duckdb_identifier(_STAGE_ORDER_COLUMN)}
            LIMIT ? OFFSET ?
            """.strip(),
            [chunk_size, offset],
        ).fetchdf()
        if chunk_df.empty:
            break
        yield chunk_df
        offset += len(chunk_df)


def _duckdb_column_type_map(
    con: duckdb.DuckDBPyConnection, table_name: str, *, exclude_columns: set[str] | None = None
) -> dict[str, str]:
    exclude_columns = exclude_columns or set()
    return {
        str(column_name): str(column_type).upper()
        for column_name, column_type, *_ in con.execute(
            f"DESCRIBE {_quote_duckdb_identifier(table_name)}"
        ).fetchall()
        if str(column_name) not in exclude_columns
    }


def _deduplicate_staged_table_exact(
    con: duckdb.DuckDBPyConnection, table_name: str, *, exclude_columns: set[str] | None = None
) -> int:
    exclude_columns = exclude_columns or set()
    data_columns = [column for column in _duckdb_columns(con, table_name) if column not in exclude_columns]
    if not data_columns:
        return int(con.execute(f"SELECT COUNT(*) FROM {_quote_duckdb_identifier(table_name)}").fetchone()[0])

    quoted_table = _quote_duckdb_identifier(table_name)
    partition_sql = ", ".join(_quote_duckdb_identifier(column) for column in data_columns)
    order_sql = _quote_duckdb_identifier(_STAGE_ORDER_COLUMN)
    con.execute(
        f"""
        CREATE OR REPLACE TABLE {quoted_table} AS
        SELECT *
        FROM (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY {partition_sql} ORDER BY {order_sql}) AS _compact_exact_rank
            FROM {quoted_table}
        )
        WHERE _compact_exact_rank = 1
        ORDER BY {order_sql}
        """.strip()
    )
    con.execute(f"ALTER TABLE {quoted_table} DROP COLUMN _compact_exact_rank")
    return int(con.execute(f"SELECT COUNT(*) FROM {quoted_table}").fetchone()[0])


def _deduplicate_dataframe_exact(df: pd.DataFrame) -> pd.DataFrame:
    return df.drop_duplicates(ignore_index=True)


def _restore_dataframe_types_from_duckdb_schema(
    df: pd.DataFrame, expected_duckdb_types: dict[str, str] | None
) -> pd.DataFrame:
    if not expected_duckdb_types:
        return df
    out = df.copy()
    for column_name, duckdb_type in expected_duckdb_types.items():
        if column_name not in out.columns:
            continue
        normalized_type = duckdb_type.upper()
        if normalized_type in {"VARCHAR", "TEXT", "STRING"}:
            out[column_name] = out[column_name].astype("string")
        elif normalized_type.startswith("TIMESTAMP"):
            out[column_name] = pd.to_datetime(out[column_name], utc=True, errors="coerce")
        elif normalized_type == "BOOLEAN":
            out[column_name] = out[column_name].astype("boolean")
        elif normalized_type in {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT"}:
            out[column_name] = pd.to_numeric(out[column_name], errors="coerce").astype("Int64")
        elif normalized_type in {"FLOAT", "DOUBLE", "REAL"}:
            out[column_name] = pd.to_numeric(out[column_name], errors="coerce").astype("Float64")
    return out


def _restore_dataframe_types_from_arrow_schema(
    df: pd.DataFrame, expected_arrow_types: dict[str, Any] | None
) -> pd.DataFrame:
    if not expected_arrow_types:
        return df
    out = df.copy()
    for column_name, arrow_type in expected_arrow_types.items():
        if column_name not in out.columns:
            continue
        if pa.types.is_timestamp(arrow_type):
            timezone_name = arrow_type.tz
            if timezone_name:
                dtype = pd.DatetimeTZDtype(unit=arrow_type.unit, tz=timezone_name)
                out[column_name] = pd.to_datetime(out[column_name], utc=True, errors="coerce").astype(dtype)
            else:
                out[column_name] = pd.to_datetime(out[column_name], errors="coerce").astype(f"datetime64[{arrow_type.unit}]")
    return out


def _parquet_physical_type_for_duckdb_type(duckdb_type: str) -> str | None:
    normalized_type = duckdb_type.upper()
    if normalized_type in {"VARCHAR", "TEXT", "STRING"}:
        return "string"
    if normalized_type.startswith("TIMESTAMP"):
        return "timestamp"
    if normalized_type == "BOOLEAN":
        return "bool"
    if normalized_type in {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT"}:
        return "integer"
    if normalized_type in {"FLOAT", "DOUBLE", "REAL"}:
        return "floating"
    return None


def _verify_parquet_schema_matches_expected(
    *,
    schema: Any,
    expected_duckdb_types: dict[str, str] | None,
    path: str,
    expected_arrow_types: dict[str, Any] | None = None,
) -> None:
    if not expected_duckdb_types:
        if not expected_arrow_types:
            return
        schema_names = set(schema.names)
        for column_name, expected_arrow_type in expected_arrow_types.items():
            if column_name not in schema_names:
                raise RuntimeError(f"Replacement parquet {path} is missing column {column_name!r}")
            actual_arrow_type = schema.field(column_name).type
            if actual_arrow_type != expected_arrow_type:
                raise RuntimeError(
                    f"Replacement parquet {path} column {column_name!r} has type {actual_arrow_type!r}, expected {expected_arrow_type!r}"
                )
        return
    schema_names = set(schema.names)
    for column_name, duckdb_type in expected_duckdb_types.items():
        if column_name not in schema_names:
            raise RuntimeError(f"Replacement parquet {path} is missing column {column_name!r}")
        expected_type = _parquet_physical_type_for_duckdb_type(duckdb_type)
        if expected_type is None:
            continue
        actual_type = schema.field(column_name).type
        actual_type_name = str(actual_type)
        if expected_type == "string" and actual_type_name != "string":
            raise RuntimeError(
                f"Replacement parquet {path} column {column_name!r} has type {actual_type_name!r}, expected 'string'"
            )
        if expected_type == "timestamp" and not actual_type_name.startswith("timestamp"):
            raise RuntimeError(
                f"Replacement parquet {path} column {column_name!r} has type {actual_type_name!r}, expected timestamp"
            )
        if expected_type == "bool" and actual_type_name != "bool":
            raise RuntimeError(
                f"Replacement parquet {path} column {column_name!r} has type {actual_type_name!r}, expected 'bool'"
            )
        if expected_type == "integer" and not actual_type_name.startswith("int"):
            raise RuntimeError(
                f"Replacement parquet {path} column {column_name!r} has type {actual_type_name!r}, expected integer"
            )
        if expected_type == "floating" and actual_type_name not in {"float", "double"}:
            raise RuntimeError(
                f"Replacement parquet {path} column {column_name!r} has type {actual_type_name!r}, expected floating point"
            )
    if not expected_arrow_types:
        return
    for column_name, expected_arrow_type in expected_arrow_types.items():
        if column_name not in schema_names:
            raise RuntimeError(f"Replacement parquet {path} is missing column {column_name!r}")
        actual_arrow_type = schema.field(column_name).type
        if actual_arrow_type != expected_arrow_type:
            raise RuntimeError(
                f"Replacement parquet {path} column {column_name!r} has type {actual_arrow_type!r}, expected {expected_arrow_type!r}"
            )


def _iter_record_batches(records: list[SelectedPathRecord], *, batch_size: int) -> Iterator[list[SelectedPathRecord]]:
    if batch_size <= 0:
        raise ValueError(f"S3 parquet read batch size must be positive, got {batch_size}")
    for start in range(0, len(records), batch_size):
        yield records[start : start + batch_size]


def _compact_replay_run_ts(con: duckdb.DuckDBPyConnection, *, fallback_date: date) -> str:
    if "run_ts" not in _duckdb_columns(con, "dedup_stage"):
        return f"{fallback_date.isoformat()}T00:00:00Z"
    row = con.execute(
        f"""
        SELECT run_ts
        FROM dedup_stage
        WHERE run_ts IS NOT NULL
        ORDER BY {_quote_duckdb_identifier(_STAGE_ORDER_COLUMN)}
        LIMIT 1
        """.strip()
    ).fetchone()
    if row is None:
        return f"{fallback_date.isoformat()}T00:00:00Z"
    return str(row[0])


def _append_dataframe_to_table(
    con: duckdb.DuckDBPyConnection,
    *,
    table_name: str,
    df: pd.DataFrame,
) -> None:
    con.register("compact_stage_batch", df)
    try:
        quoted_table = _quote_duckdb_identifier(table_name)
        if table_name in {row[0] for row in con.execute("SHOW TABLES").fetchall()}:
            existing_columns = set(_duckdb_columns(con, table_name))
            for column in df.columns:
                if column not in existing_columns:
                    column_type = con.execute(
                        "SELECT typeof(?)",
                        [df[column].dropna().iloc[0] if df[column].dropna().shape[0] else None],
                    ).fetchone()[0]
                    if column_type == "NULL":
                        column_type = "VARCHAR"
                    con.execute(f"ALTER TABLE {quoted_table} ADD COLUMN {_quote_duckdb_identifier(column)} {column_type}")
            con.execute(f"INSERT INTO {quoted_table} BY NAME SELECT * FROM compact_stage_batch")
        else:
            con.execute(f"CREATE TABLE {quoted_table} AS SELECT * FROM compact_stage_batch")
    finally:
        con.unregister("compact_stage_batch")


def _stage_normalized_event_mapping_chunks(
    con: duckdb.DuckDBPyConnection,
    *,
    selected_records: list[SelectedPathRecord],
    selected_event_date: date,
    instance_name: str,
    run_epoch_ms: int,
    output_chunk_size: int,
) -> tuple[dict[str, str], CompactPlanSummary]:
    replay_run_ts = _compact_replay_run_ts(con, fallback_date=selected_event_date)
    module_tables: dict[str, str] = {}
    module_rows: dict[str, int] = {}
    module_columns: dict[str, int] = {}
    rehydrated_rows = 0
    rehydrated_columns: int | None = None
    mapper_rows = 0
    mapper_columns: int | None = None
    silver_row_order = 0

    for chunk_df in _iter_table_chunks(
        con,
        table_name="dedup_stage",
        chunk_size=output_chunk_size,
        exclude_columns={_STAGE_ORDER_COLUMN},
    ):
        rehydrated = rehydrate_event_mapping_source(chunk_df)
        rehydrated_rows += int(rehydrated.shape[0])
        rehydrated_columns = int(rehydrated.shape[1]) if rehydrated_columns is None else rehydrated_columns
        mapped_df = event_mapping.main(rehydrated)
        if mapped_df is None or not isinstance(mapped_df, pd.DataFrame):
            raise ReplaySkipError("event_mapping.main() did not return a dataframe")
        mapper_columns = int(mapped_df.shape[1]) if mapper_columns is None else mapper_columns
        if mapped_df.shape[0] == 0:
            continue
        if "dataiku_category" not in mapped_df.columns:
            raise ReplaySkipError("Mapped dataframe is missing dataiku_category")
        mapper_rows += int(mapped_df.shape[0])
        for mapped_module_name in sorted(str(value) for value in mapped_df["dataiku_category"].dropna().unique().tolist()):
            group_df = mapped_df.loc[mapped_df["dataiku_category"] == mapped_module_name].copy()
            silver_df = normalize_silver(
                df=group_df,
                instance_name=instance_name,
                run_ts=replay_run_ts,
                **resolve_event_mapping_normalize_args(module_name=mapped_module_name),
            )
            if silver_df.empty:
                continue
            silver_df = silver_df.copy()
            silver_df[_STAGE_ORDER_COLUMN] = range(silver_row_order, silver_row_order + len(silver_df))
            silver_row_order += len(silver_df)
            table_name = module_tables.setdefault(mapped_module_name, f"silver_stage_{len(module_tables) + 1}")
            _append_dataframe_to_table(con, table_name=table_name, df=silver_df)
            module_rows[mapped_module_name] = module_rows.get(mapped_module_name, 0) + int(silver_df.shape[0])
            module_columns[mapped_module_name] = max(module_columns.get(mapped_module_name, 0), int(silver_df.shape[1] - 1))
            del silver_df
        del chunk_df

    if not module_tables:
        return {}, CompactPlanSummary(
            mode="event_mapping_replay",
            run_epoch_ms=run_epoch_ms,
            input_rows=int(con.execute("SELECT COUNT(*) FROM dedup_stage").fetchone()[0]),
            input_columns=len([column for column in _duckdb_columns(con, "dedup_stage") if column != _STAGE_ORDER_COLUMN]),
            rehydrated_rows=rehydrated_rows,
            rehydrated_columns=rehydrated_columns or 0,
            mapper_rows=mapper_rows,
            mapper_columns=mapper_columns or 0,
            mapper_groups=0,
            metrics=(),
        )

    metrics = tuple(
        CompactPlanMetric(
            module_name=module_name,
            rows=module_rows[module_name],
            columns=module_columns[module_name],
            dq_ok=True,
            dq_errors=(),
        )
        for module_name in sorted(module_tables)
    )
    return module_tables, CompactPlanSummary(
        mode="event_mapping_replay",
        run_epoch_ms=run_epoch_ms,
        input_rows=int(con.execute("SELECT COUNT(*) FROM dedup_stage").fetchone()[0]),
        input_columns=len([column for column in _duckdb_columns(con, "dedup_stage") if column != _STAGE_ORDER_COLUMN]),
        rehydrated_rows=rehydrated_rows,
        rehydrated_columns=rehydrated_columns or 0,
        mapper_rows=mapper_rows,
        mapper_columns=mapper_columns or 0,
        mapper_groups=len(module_tables),
        metrics=metrics,
    )


def _stage_normalized_module_contract_chunks(
    con: duckdb.DuckDBPyConnection,
    *,
    category: str,
    module_name: str,
    selected_event_date: date,
    instance_name: str,
    run_epoch_ms: int,
    output_chunk_size: int,
) -> tuple[str, CompactPlanSummary]:
    _required_columns_for_module_contract(category=category, module_name=module_name)
    table_name = "silver_stage_1"
    total_rows = int(con.execute("SELECT COUNT(*) FROM dedup_stage").fetchone()[0])
    input_columns = len([column for column in _duckdb_columns(con, "dedup_stage") if column != _STAGE_ORDER_COLUMN])
    silver_row_order = 0
    output_rows = 0
    output_columns = 0

    for chunk_df in _iter_table_chunks(
        con,
        table_name="dedup_stage",
        chunk_size=output_chunk_size,
        exclude_columns={_STAGE_ORDER_COLUMN},
    ):
        silver_df = normalize_module_contract_silver(
            df=chunk_df,
            category=category,
            module_name=module_name,
            instance_name=instance_name,
        )
        if silver_df.empty:
            continue
        silver_df = silver_df.copy()
        silver_df[_STAGE_ORDER_COLUMN] = range(silver_row_order, silver_row_order + len(silver_df))
        silver_row_order += len(silver_df)
        output_rows += int(silver_df.shape[0])
        output_columns = max(output_columns, int(silver_df.shape[1] - 1))
        _append_dataframe_to_table(con, table_name=table_name, df=silver_df)
        del chunk_df
        del silver_df

    if output_rows <= 0:
        raise ReplaySkipError(
            f"Module contract normalization produced no rows for category={category!r} module={module_name!r}"
        )

    normalized_rows_before_exact_dedup = output_rows
    output_rows = _deduplicate_staged_table_exact(
        con, table_name, exclude_columns={_STAGE_ORDER_COLUMN}
    )
    logger.info(
        "Compact SILVER module-contract normalization staged category=%s module=%s input_rows=%s normalized_rows=%s final_unique_rows=%s",
        category,
        module_name,
        total_rows,
        normalized_rows_before_exact_dedup,
        output_rows,
    )

    expected_duckdb_types = _duckdb_column_type_map(
        con, table_name, exclude_columns={_STAGE_ORDER_COLUMN}
    )
    actual_columns = [column for column in _duckdb_columns(con, table_name) if column != _STAGE_ORDER_COLUMN]
    expected_order = _output_columns_for_module_contract(
        category=category, module_name=module_name
    )
    if actual_columns != expected_order:
        raise ReplaySkipError(
            f"Module contract normalization produced unexpected schema for category={category!r} module={module_name!r}: {actual_columns!r}"
        )

    return table_name, CompactPlanSummary(
        mode=COMPACT_PROCESSING_MODE_MODULE_CONTRACT,
        run_epoch_ms=run_epoch_ms,
        input_rows=total_rows,
        input_columns=input_columns,
        metrics=(
            CompactPlanMetric(
                module_name=module_name,
                rows=output_rows,
                columns=output_columns,
                dq_ok=True,
                dq_errors=(),
            ),
        ),
    )


def _table_event_date(con: duckdb.DuckDBPyConnection, *, table_name: str, fallback_date: date) -> date:
    if "timestamp" not in _duckdb_columns(con, table_name):
        return fallback_date
    quoted_table = _quote_duckdb_identifier(table_name)
    value = con.execute(f"SELECT MAX(timestamp) FROM {quoted_table}").fetchone()[0]
    event_ts = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(event_ts):
        return fallback_date
    return event_ts.date()


def _chunked_plan_iterator(
    *,
    con: duckdb.DuckDBPyConnection,
    selected_records: list[SelectedPathRecord],
    normalize_silver_mode: bool,
    processing_mode: str,
    run_epoch_ms: int,
    output_chunk_size: int,
) -> tuple[Iterator[ReplayWritePlan], CompactPlanSummary]:
    selected_event_date = _selected_event_date(selected_records)
    category, module_name, instance_name, event_year, event_month, event_day = _selected_partition_identity(selected_records)
    if (f"{selected_event_date.year:04d}", f"{selected_event_date.month:02d}", f"{selected_event_date.day:02d}") != (event_year, event_month, event_day):
        raise ReplaySkipError("Selected source records contain inconsistent logical partition dates")

    effective_processing_mode = processing_mode or (
        COMPACT_PROCESSING_MODE_EVENT_MAPPING if normalize_silver_mode else COMPACT_PROCESSING_MODE_GENERIC
    )

    if effective_processing_mode == COMPACT_PROCESSING_MODE_GENERIC:
        total_rows = int(con.execute("SELECT COUNT(*) FROM dedup_stage").fetchone()[0])
        input_columns = len([column for column in _duckdb_columns(con, "dedup_stage") if column != _STAGE_ORDER_COLUMN])
        chunk_count = int((total_rows + output_chunk_size - 1) // output_chunk_size)

        def iter_generic_plans() -> Iterator[ReplayWritePlan]:
            for sequence_offset, silver_df in enumerate(
                _iter_table_chunks(
                    con,
                    table_name="dedup_stage",
                    chunk_size=output_chunk_size,
                    exclude_columns={_STAGE_ORDER_COLUMN},
                ),
                start=1,
            ):
                yield ReplayWritePlan(
                    output_path=build_compact_output_path(
                        category=category,
                        module_name=module_name,
                        instance_name=instance_name,
                        event_date=selected_event_date,
                        run_epoch_ms=run_epoch_ms,
                        sequence_number=sequence_offset,
                    ),
                    silver_df=silver_df,
                    dq=check_silver_dq(silver_df),
                    module_name=module_name,
                    event_date=selected_event_date,
                )

        return iter_generic_plans(), CompactPlanSummary(
            mode=COMPACT_PROCESSING_MODE_GENERIC,
            run_epoch_ms=run_epoch_ms,
            input_rows=total_rows,
            input_columns=input_columns,
            metrics=(
                CompactPlanMetric(
                    module_name=module_name,
                    rows=total_rows,
                    columns=input_columns,
                    dq_ok=True,
                    dq_errors=(),
                ),
            ) if chunk_count else (),
        )

    if effective_processing_mode == COMPACT_PROCESSING_MODE_MODULE_CONTRACT:
        table_name, plan_summary = _stage_normalized_module_contract_chunks(
            con,
            category=category,
            module_name=module_name,
            selected_event_date=selected_event_date,
            instance_name=instance_name,
            run_epoch_ms=run_epoch_ms,
            output_chunk_size=output_chunk_size,
        )
        expected_duckdb_types = _duckdb_column_type_map(
            con, table_name, exclude_columns={_STAGE_ORDER_COLUMN}
        )
        expected_arrow_types = _expected_arrow_types_for_module_contract(
            category=category, module_name=module_name
        )

        def iter_module_contract_plans() -> Iterator[ReplayWritePlan]:
            for sequence_number, silver_df in enumerate(
                _iter_table_chunks(
                    con,
                    table_name=table_name,
                    chunk_size=output_chunk_size,
                    exclude_columns={_STAGE_ORDER_COLUMN},
                ),
                start=1,
            ):
                silver_df = _restore_dataframe_types_from_duckdb_schema(
                    silver_df, expected_duckdb_types
                )
                yield ReplayWritePlan(
                    output_path=build_compact_output_path(
                        category=category,
                        module_name=module_name,
                        instance_name=instance_name,
                        event_date=selected_event_date,
                        run_epoch_ms=run_epoch_ms,
                        sequence_number=sequence_number,
                    ),
                    silver_df=silver_df,
                    dq=check_silver_dq(silver_df),
                    module_name=module_name,
                    event_date=selected_event_date,
                    expected_duckdb_types=expected_duckdb_types,
                    expected_arrow_types=expected_arrow_types,
                )

        return iter_module_contract_plans(), plan_summary

    if effective_processing_mode != COMPACT_PROCESSING_MODE_EVENT_MAPPING:
        raise ReplaySkipError(f"Unsupported compact SILVER processing mode: {effective_processing_mode!r}")

    if category != "event_mapping":
        raise ReplaySkipError(
            f"Event Mapping replay cannot process category={category!r} module={module_name!r}"
        )

    module_tables, plan_summary = _stage_normalized_event_mapping_chunks(
        con,
        selected_records=selected_records,
        selected_event_date=selected_event_date,
        instance_name=instance_name,
        run_epoch_ms=run_epoch_ms,
        output_chunk_size=output_chunk_size,
    )

    def iter_replay_plans() -> Iterator[ReplayWritePlan]:
        sequence_number = 1
        for mapped_module_name in sorted(module_tables):
            table_name = module_tables[mapped_module_name]
            event_date = _table_event_date(con, table_name=table_name, fallback_date=selected_event_date)
            expected_duckdb_types = _duckdb_column_type_map(
                con, table_name, exclude_columns={_STAGE_ORDER_COLUMN}
            )
            for silver_df in _iter_table_chunks(
                con,
                table_name=table_name,
                chunk_size=output_chunk_size,
                exclude_columns={_STAGE_ORDER_COLUMN},
            ):
                silver_df = _restore_dataframe_types_from_duckdb_schema(
                    silver_df, expected_duckdb_types
                )
                yield ReplayWritePlan(
                    output_path=build_compact_output_path(
                        category="event_mapping",
                        module_name=mapped_module_name,
                        instance_name=instance_name,
                        event_date=event_date,
                        run_epoch_ms=run_epoch_ms,
                        sequence_number=sequence_number,
                    ),
                    silver_df=silver_df,
                    dq=check_silver_dq(silver_df),
                    module_name=mapped_module_name,
                    event_date=event_date,
                    expected_duckdb_types=expected_duckdb_types,
                )
                sequence_number += 1

    return iter_replay_plans(), plan_summary


def apply_compact_replacement_plan_iterator(
    *,
    target: DSSFolderTarget,
    folder: Any,
    source_relative_paths: list[str],
    plans: Iterator[ReplayWritePlan],
) -> tuple[CompactApplyResult, int, tuple[CompactPlanMetric, ...]]:
    written_paths: list[str] = []
    written_expected_types: list[dict[str, str] | None] = []
    written_expected_arrow_types: list[dict[str, Any] | None] = []
    plan_count = 0
    dq_errors: list[str] = []
    metric_rows: dict[str, int] = {}
    metric_columns: dict[str, int] = {}
    metric_dq_ok: dict[str, bool] = {}
    metric_dq_errors: dict[str, list[str]] = {}
    try:
        for plan in plans:
            plan_count += 1
            metric_rows[plan.module_name] = metric_rows.get(plan.module_name, 0) + int(plan.silver_df.shape[0])
            metric_columns[plan.module_name] = max(metric_columns.get(plan.module_name, 0), int(plan.silver_df.shape[1]))
            metric_dq_ok[plan.module_name] = metric_dq_ok.get(plan.module_name, True) and bool(plan.dq.ok)
            if not plan.dq.ok:
                plan_errors = [str(error) for error in plan.dq.errors]
                dq_errors.extend(plan_errors)
                metric_dq_errors.setdefault(plan.module_name, []).extend(plan_errors)
                continue
            upload_parquet(
                target=target,
                output_path=Path(str(plan.output_path)),
                output_base_dir=Path("/"),
                df=_restore_dataframe_types_from_arrow_schema(
                    _restore_dataframe_types_from_duckdb_schema(
                        plan.silver_df, plan.expected_duckdb_types
                    ),
                    plan.expected_arrow_types,
                ),
                compression="snappy",
            )
            written_paths.append(str(plan.output_path))
            written_expected_types.append(plan.expected_duckdb_types)
            written_expected_arrow_types.append(plan.expected_arrow_types)
            logger.info(
                "Compact SILVER replacement uploaded output_sequence=%s written_count=%s rows=%s elapsed_seconds=%.3f rss_mb=%s",
                plan_count,
                len(written_paths),
                int(plan.silver_df.shape[0]),
                0.0,
                _process_rss_mb(),
            )
    except Exception as exc:
        cleaned_paths, cleanup_errors = cleanup_written_replacements(folder=folder, paths=written_paths)
        if cleanup_errors:
            return CompactApplyResult(
                status="upload_failed_cleanup_failed",
                written_paths=tuple(written_paths),
                cleanup_paths=cleaned_paths,
                retained_source_paths=tuple(source_relative_paths),
                message=format_partial_write_message(
                    base_message=f"Upload failed: {exc!r}; cleanup failed for {len(cleanup_errors)} replacement(s)",
                    written_paths=tuple(written_paths),
                    cleanup_paths=cleaned_paths,
                ),
            ), plan_count, _compact_plan_metrics(metric_rows, metric_columns, metric_dq_ok, metric_dq_errors)
        return CompactApplyResult(
            status="upload_failed_cleaned",
            written_paths=tuple(written_paths),
            cleanup_paths=cleaned_paths,
            retained_source_paths=tuple(source_relative_paths),
            message=format_partial_write_message(
                base_message=f"Upload failed: {exc!r}; cleaned {len(cleaned_paths)} replacement(s)",
                written_paths=tuple(written_paths),
                cleanup_paths=cleaned_paths,
            ),
        ), plan_count, _compact_plan_metrics(metric_rows, metric_columns, metric_dq_ok, metric_dq_errors)

    if dq_errors:
        cleaned_paths, cleanup_errors = cleanup_written_replacements(folder=folder, paths=written_paths)
        status = "dq_failed_cleanup_failed" if cleanup_errors else "dq_failed_cleaned"
        return CompactApplyResult(
            status=status,
            written_paths=tuple(written_paths),
            cleanup_paths=cleaned_paths,
            retained_source_paths=tuple(source_relative_paths),
            dq_errors=tuple(dq_errors),
            message=", ".join(dq_errors) if not cleanup_errors else f"{', '.join(dq_errors)}; cleanup failed for {len(cleanup_errors)} replacement(s)",
        ), plan_count, _compact_plan_metrics(metric_rows, metric_columns, metric_dq_ok, metric_dq_errors)

    if not written_paths:
        return CompactApplyResult(
            status="no_mapped_output_retained",
            retained_source_paths=tuple(source_relative_paths),
            message=(
                "Compact SILVER processing produced no replacement output; "
                f"written=0, verified=0, deleted=0, retained={len(source_relative_paths)}"
            ),
        ), plan_count, _compact_plan_metrics(metric_rows, metric_columns, metric_dq_ok, metric_dq_errors)

    verified_paths: list[str] = []
    try:
        for written_path, expected_duckdb_types, expected_arrow_types in zip(
            written_paths,
            written_expected_types,
            written_expected_arrow_types,
            strict=True,
        ):
            if expected_duckdb_types or expected_arrow_types:
                verify_managed_folder_file(
                    folder,
                    written_path,
                    expected_duckdb_types=expected_duckdb_types,
                    expected_arrow_types=expected_arrow_types,
                )
            else:
                verify_managed_folder_file(folder, written_path)
            verified_paths.append(written_path)
    except Exception as exc:
        cleaned_paths, cleanup_errors = cleanup_written_replacements(folder=folder, paths=written_paths)
        if cleanup_errors:
            return CompactApplyResult(
                status="verification_failed_cleanup_failed",
                written_paths=tuple(written_paths),
                verified_paths=tuple(verified_paths),
                cleanup_paths=cleaned_paths,
                retained_source_paths=tuple(source_relative_paths),
                message=f"Verification failed: {exc!r}; cleanup failed for {len(cleanup_errors)} replacement(s)",
            ), plan_count, _compact_plan_metrics(metric_rows, metric_columns, metric_dq_ok, metric_dq_errors)
        return CompactApplyResult(
            status="verification_failed_cleaned",
            written_paths=tuple(written_paths),
            verified_paths=tuple(verified_paths),
            cleanup_paths=cleaned_paths,
            retained_source_paths=tuple(source_relative_paths),
            message=f"Verification failed: {exc!r}; cleaned {len(cleaned_paths)} replacement(s)",
        ), plan_count, _compact_plan_metrics(metric_rows, metric_columns, metric_dq_ok, metric_dq_errors)

    deleted_paths: list[str] = []
    for index, source_path in enumerate(source_relative_paths):
        try:
            delete_managed_folder_file(folder, source_path)
            deleted_paths.append(source_path)
        except Exception as exc:
            return CompactApplyResult(
                status="delete_failed",
                written_paths=tuple(written_paths),
                verified_paths=tuple(verified_paths),
                deleted_source_paths=tuple(deleted_paths),
                retained_source_paths=tuple(source_relative_paths[index:]),
                message=f"Delete failed after verified writes: {exc!r}",
            ), plan_count, _compact_plan_metrics(metric_rows, metric_columns, metric_dq_ok, metric_dq_errors)

    return CompactApplyResult(
        status="succeeded",
        written_paths=tuple(written_paths),
        verified_paths=tuple(verified_paths),
        deleted_source_paths=tuple(deleted_paths),
        retained_source_paths=(),
        message=f"Verified {len(verified_paths)} replacement file(s) and deleted {len(deleted_paths)} source file(s)",
    ), plan_count, _compact_plan_metrics(metric_rows, metric_columns, metric_dq_ok, metric_dq_errors)


def _compact_plan_metrics(
    metric_rows: dict[str, int],
    metric_columns: dict[str, int],
    metric_dq_ok: dict[str, bool],
    metric_dq_errors: dict[str, list[str]],
) -> tuple[CompactPlanMetric, ...]:
    return tuple(
        CompactPlanMetric(
            module_name=module_name,
            rows=metric_rows[module_name],
            columns=metric_columns[module_name],
            dq_ok=metric_dq_ok.get(module_name, True),
            dq_errors=tuple(metric_dq_errors.get(module_name, [])),
        )
        for module_name in sorted(metric_rows)
    )


def process_staged_compact_partition(
    *,
    target: DSSFolderTarget,
    selected_records: list[SelectedPathRecord],
    source_relative_paths: list[str],
    database_path: Path,
    normalize_silver_mode: bool,
    processing_mode: str | None = None,
    run_epoch_ms: int,
    output_chunk_size: int = COMPACT_SILVER_OUTPUT_ROW_CHUNK_SIZE,
) -> CompactProcessingResult:
    con = duckdb.connect(str(database_path))
    try:
        plans, plan_summary = _chunked_plan_iterator(
            con=con,
            selected_records=selected_records,
            normalize_silver_mode=normalize_silver_mode,
            processing_mode=processing_mode or (
                COMPACT_PROCESSING_MODE_EVENT_MAPPING
                if normalize_silver_mode
                else COMPACT_PROCESSING_MODE_GENERIC
            ),
            run_epoch_ms=run_epoch_ms,
            output_chunk_size=output_chunk_size,
        )
        folder = get_managed_folder_handle(target=target)
        apply_result, plan_count, metrics = apply_compact_replacement_plan_iterator(
            target=target,
            folder=folder,
            source_relative_paths=source_relative_paths,
            plans=plans,
        )
        return CompactProcessingResult(
            plan_summary=replace(plan_summary, metrics=metrics),
            apply_result=apply_result,
            plan_count=plan_count,
        )
    finally:
        con.close()


def plan_compact_selected_day(
    *,
    selected_records: list[SelectedPathRecord],
    selected_df: pd.DataFrame,
    normalize_silver_mode: bool,
    run_epoch_ms: int,
    processing_mode: str | None = None,
) -> tuple[list[ReplayWritePlan], CompactPlanSummary]:
    if not isinstance(selected_df, pd.DataFrame):
        raise ReplaySkipError("Selected parquet payload did not load as a dataframe")
    if selected_df.shape[0] == 0:
        raise ReplaySkipError("Selected parquet payload is empty")
    if not selected_records:
        raise ReplaySkipError("No selected source records were provided")

    selected_event_date = _selected_event_date(selected_records)
    category, module_name, instance_name, event_year, event_month, event_day = _selected_partition_identity(selected_records)
    if (f"{selected_event_date.year:04d}", f"{selected_event_date.month:02d}", f"{selected_event_date.day:02d}") != (event_year, event_month, event_day):
        raise ReplaySkipError("Selected source records contain inconsistent logical partition dates")

    effective_processing_mode = processing_mode or (
        COMPACT_PROCESSING_MODE_EVENT_MAPPING if normalize_silver_mode else COMPACT_PROCESSING_MODE_GENERIC
    )

    if effective_processing_mode == COMPACT_PROCESSING_MODE_GENERIC:
        silver_df = _deduplicate_dataframe_exact(selected_df)
        output_path = build_compact_output_path(
            category=category,
            module_name=module_name,
            instance_name=instance_name,
            event_date=selected_event_date,
            run_epoch_ms=run_epoch_ms,
            sequence_number=1,
        )
        plan = ReplayWritePlan(
            output_path=output_path,
            silver_df=silver_df,
            dq=check_silver_dq(silver_df),
            module_name=module_name,
            event_date=selected_event_date,
        )
        plans = [plan]
        _validate_output_paths_do_not_overlap_sources(plans=plans, selected_records=selected_records)
        return plans, CompactPlanSummary(
            mode=COMPACT_PROCESSING_MODE_GENERIC,
            run_epoch_ms=run_epoch_ms,
            input_rows=int(selected_df.shape[0]),
            input_columns=int(selected_df.shape[1]),
            metrics=(
                CompactPlanMetric(
                    module_name=module_name,
                    rows=int(plan.silver_df.shape[0]),
                    columns=int(plan.silver_df.shape[1]),
                    dq_ok=bool(plan.dq.ok),
                    dq_errors=tuple(str(error) for error in plan.dq.errors),
                ),
            ),
        )

    if effective_processing_mode == COMPACT_PROCESSING_MODE_MODULE_CONTRACT:
        silver_df = normalize_module_contract_silver(
            df=selected_df,
            category=category,
            module_name=module_name,
            instance_name=instance_name,
        )
        silver_df = _deduplicate_dataframe_exact(silver_df)
        dq = check_silver_dq(silver_df)
        output_path = build_compact_output_path(
            category=category,
            module_name=module_name,
            instance_name=instance_name,
            event_date=selected_event_date,
            run_epoch_ms=run_epoch_ms,
            sequence_number=1,
        )
        plans = [
            ReplayWritePlan(
                output_path=output_path,
                silver_df=silver_df,
                dq=dq,
                module_name=module_name,
                event_date=selected_event_date,
                expected_arrow_types=_expected_arrow_types_for_module_contract(
                    category=category, module_name=module_name
                ),
            )
        ]
        _validate_output_paths_do_not_overlap_sources(plans=plans, selected_records=selected_records)
        return plans, CompactPlanSummary(
            mode=COMPACT_PROCESSING_MODE_MODULE_CONTRACT,
            run_epoch_ms=run_epoch_ms,
            input_rows=int(selected_df.shape[0]),
            input_columns=int(selected_df.shape[1]),
            metrics=(
                CompactPlanMetric(
                    module_name=module_name,
                    rows=int(silver_df.shape[0]),
                    columns=int(silver_df.shape[1]),
                    dq_ok=bool(dq.ok),
                    dq_errors=tuple(str(error) for error in dq.errors),
                ),
            ),
        )

    if effective_processing_mode != COMPACT_PROCESSING_MODE_EVENT_MAPPING:
        raise ReplaySkipError(f"Unsupported compact SILVER processing mode: {effective_processing_mode!r}")
    if category != "event_mapping":
        raise ReplaySkipError(
            f"Event Mapping replay cannot process category={category!r} module={module_name!r}"
        )

    rehydrated = rehydrate_event_mapping_source(selected_df)
    mapped_df = event_mapping.main(rehydrated)
    if mapped_df is None or not isinstance(mapped_df, pd.DataFrame):
        raise ReplaySkipError("event_mapping.main() did not return a dataframe")
    if mapped_df.shape[0] == 0:
        return [], CompactPlanSummary(
            mode="event_mapping_replay",
            run_epoch_ms=run_epoch_ms,
            input_rows=int(selected_df.shape[0]),
            input_columns=int(selected_df.shape[1]),
            rehydrated_rows=int(rehydrated.shape[0]),
            rehydrated_columns=int(rehydrated.shape[1]),
            mapper_rows=0,
            mapper_columns=int(mapped_df.shape[1]),
            mapper_groups=0,
            metrics=(),
        )
    if "dataiku_category" not in mapped_df.columns:
        raise ReplaySkipError("Mapped dataframe is missing dataiku_category")

    replay_run_ts = _resolve_replay_run_ts(selected_df, selected_event_date, None)
    grouped_modules = sorted(str(module_value) for module_value in mapped_df["dataiku_category"].dropna().unique().tolist())

    plans: list[ReplayWritePlan] = []
    metrics: list[CompactPlanMetric] = []
    for sequence_number, mapped_module_name in enumerate(grouped_modules, start=1):
        group_df = mapped_df.loc[mapped_df["dataiku_category"] == mapped_module_name].copy()
        silver_df = normalize_silver(
            df=group_df,
            instance_name=instance_name,
            run_ts=replay_run_ts,
            **resolve_event_mapping_normalize_args(module_name=mapped_module_name),
        )
        event_date = resolve_replay_event_date(silver_df=silver_df, fallback_date=selected_event_date)
        dq = check_silver_dq(silver_df)
        plans.append(
            ReplayWritePlan(
                output_path=build_compact_output_path(
                    category="event_mapping",
                    module_name=mapped_module_name,
                    instance_name=instance_name,
                    event_date=event_date,
                    run_epoch_ms=run_epoch_ms,
                    sequence_number=sequence_number,
                ),
                silver_df=silver_df,
                dq=dq,
                module_name=mapped_module_name,
                event_date=event_date,
            )
        )
        metrics.append(
            CompactPlanMetric(
                module_name=mapped_module_name,
                rows=int(silver_df.shape[0]),
                columns=int(silver_df.shape[1]),
                dq_ok=bool(dq.ok),
                dq_errors=tuple(str(error) for error in dq.errors),
            )
        )

    _validate_output_paths_do_not_overlap_sources(plans=plans, selected_records=selected_records)
    return plans, CompactPlanSummary(
        mode="event_mapping_replay",
        run_epoch_ms=run_epoch_ms,
        input_rows=int(selected_df.shape[0]),
        input_columns=int(selected_df.shape[1]),
        rehydrated_rows=int(rehydrated.shape[0]),
        rehydrated_columns=int(rehydrated.shape[1]),
        mapper_rows=int(mapped_df.shape[0]),
        mapper_columns=int(mapped_df.shape[1]),
        mapper_groups=len(grouped_modules),
        metrics=tuple(metrics),
    )


def verify_managed_folder_file(
    folder: Any,
    path: str,
    *,
    expected_duckdb_types: dict[str, str] | None = None,
    expected_arrow_types: dict[str, Any] | None = None,
) -> None:
    cleaned = clean_managed_folder_path(path)
    read_full_payload = bool(expected_duckdb_types or expected_arrow_types)
    if hasattr(folder, "get_download_stream"):
        with folder.get_download_stream(cleaned) as stream:
            data = stream.read() if read_full_payload else stream.read(1)
        if read_full_payload:
            _verify_parquet_schema_matches_expected(
                schema=pq.read_schema(io.BytesIO(data)),
                expected_duckdb_types=expected_duckdb_types,
                expected_arrow_types=expected_arrow_types,
                path=path,
            )
        return
    if hasattr(folder, "get_file"):
        payload = folder.get_file(cleaned)
        if hasattr(payload, "read"):
            data = payload.read() if read_full_payload else payload.read(1)
            if read_full_payload:
                _verify_parquet_schema_matches_expected(
                    schema=pq.read_schema(io.BytesIO(data)),
                    expected_duckdb_types=expected_duckdb_types,
                    expected_arrow_types=expected_arrow_types,
                    path=path,
                )
        return
    raise TypeError(f"Unsupported folder handle type for verification: {type(folder)!r}")


def apply_compact_replacement_plans(
    *,
    target: DSSFolderTarget,
    folder: Any,
    source_relative_paths: list[str],
    plans: list[ReplayWritePlan],
) -> CompactApplyResult:
    if not plans:
        return CompactApplyResult(
            status="no_mapped_output_retained",
            retained_source_paths=tuple(source_relative_paths),
            message=(
                "Current event-mapping replay produced no replacement output; "
                f"written=0, verified=0, deleted=0, retained={len(source_relative_paths)}"
            ),
        )

    upload_result = upload_event_mapping_replacements(target=target, folder=folder, plans=plans)
    if upload_result.status != "uploaded":
        return CompactApplyResult(
            status=upload_result.status,
            written_paths=upload_result.written_paths,
            cleanup_paths=upload_result.cleanup_paths,
            retained_source_paths=tuple(source_relative_paths),
            dq_errors=upload_result.dq_errors,
            message=upload_result.message,
        )

    verified_paths: list[str] = []
    try:
        for written_path in upload_result.written_paths:
            verify_managed_folder_file(folder, written_path)
            verified_paths.append(written_path)
    except Exception as exc:
        cleaned_paths, cleanup_errors = cleanup_written_replacements(folder=folder, paths=list(upload_result.written_paths))
        if cleanup_errors:
            return CompactApplyResult(
                status="verification_failed_cleanup_failed",
                written_paths=upload_result.written_paths,
                verified_paths=tuple(verified_paths),
                cleanup_paths=cleaned_paths,
                retained_source_paths=tuple(source_relative_paths),
                message=format_partial_write_message(
                    base_message=f"Verification failed: {exc!r}; cleanup failed for {len(cleanup_errors)} replacement(s)",
                    written_paths=upload_result.written_paths,
                    cleanup_paths=cleaned_paths,
                ),
            )
        return CompactApplyResult(
            status="verification_failed_cleaned",
            written_paths=upload_result.written_paths,
            verified_paths=tuple(verified_paths),
            cleanup_paths=cleaned_paths,
            retained_source_paths=tuple(source_relative_paths),
            message=f"Verification failed: {exc!r}; cleaned {len(cleaned_paths)} replacement(s)",
        )

    deleted_paths: list[str] = []
    for index, source_path in enumerate(source_relative_paths):
        try:
            delete_managed_folder_file(folder, source_path)
            deleted_paths.append(source_path)
        except Exception as exc:
            return CompactApplyResult(
                status="delete_failed",
                written_paths=upload_result.written_paths,
                verified_paths=tuple(verified_paths),
                deleted_source_paths=tuple(deleted_paths),
                retained_source_paths=tuple(source_relative_paths[index:]),
                message=f"Delete failed after verified writes: {exc!r}",
            )

    return CompactApplyResult(
        status="succeeded",
        written_paths=upload_result.written_paths,
        verified_paths=tuple(verified_paths),
        deleted_source_paths=tuple(deleted_paths),
        retained_source_paths=(),
        message=f"Verified {len(verified_paths)} replacement file(s) and deleted {len(deleted_paths)} source file(s)",
    )


def process_compact_selected_partition(
    *,
    storage_ctx: Any,
    storage_ctx_factory: Any | None = None,
    target: DSSFolderTarget,
    selected_partition: SelectedPartitionPaths,
    normalize_silver_mode: bool,
    processing_mode: str | None = None,
    s3_read_batch_size: int = COMPACT_SILVER_S3_READ_BATCH_SIZE,
    output_chunk_size: int = COMPACT_SILVER_OUTPUT_ROW_CHUNK_SIZE,
) -> CompactPartitionOutcome:
    replay_mode = processing_mode or (
        COMPACT_PROCESSING_MODE_EVENT_MAPPING
        if normalize_silver_mode
        else COMPACT_PROCESSING_MODE_GENERIC
    )
    run_epoch_ms = next_compact_save_epoch_ms()
    with tempfile.TemporaryDirectory(prefix="compact-silver-") as stage_dir:
        stage_result = _stage_compact_s3_partition(
            storage_ctx=storage_ctx,
            storage_ctx_factory=storage_ctx_factory,
            selected_partition=selected_partition,
            database_path=Path(stage_dir) / "partition.duckdb",
            s3_read_batch_size=s3_read_batch_size,
        )
        processing_result = process_staged_compact_partition(
            target=target,
            selected_records=selected_partition.selected_records,
            source_relative_paths=selected_partition.relative_paths,
            database_path=stage_result.database_path,
            normalize_silver_mode=normalize_silver_mode,
            processing_mode=replay_mode,
            run_epoch_ms=run_epoch_ms,
            output_chunk_size=output_chunk_size,
        )
    return CompactPartitionOutcome(
        year=selected_partition.year,
        month=selected_partition.month,
        day=selected_partition.day,
        replay_mode=replay_mode,
        status=processing_result.apply_result.status,
        message=processing_result.apply_result.message,
        run_epoch_ms=run_epoch_ms,
        files_read=stage_result.files_read,
        raw_rows=stage_result.raw_rows,
        rows_after_drop_duplicates=stage_result.rows_after_drop_duplicates,
        output_column_count=stage_result.output_column_count,
        input_rows=processing_result.plan_summary.input_rows,
        input_columns=processing_result.plan_summary.input_columns,
        plan_count=processing_result.plan_count,
        rehydrated_rows=processing_result.plan_summary.rehydrated_rows,
        rehydrated_columns=processing_result.plan_summary.rehydrated_columns,
        mapper_rows=processing_result.plan_summary.mapper_rows,
        mapper_columns=processing_result.plan_summary.mapper_columns,
        mapper_groups=processing_result.plan_summary.mapper_groups,
        metrics=processing_result.plan_summary.metrics,
        written_count=len(processing_result.apply_result.written_paths),
        verified_count=len(processing_result.apply_result.verified_paths),
        deleted_count=len(processing_result.apply_result.deleted_source_paths),
        retained_count=len(processing_result.apply_result.retained_source_paths),
    )


def plan_event_mapping_replay(*, source: EventMappingSourceInfo, source_df: pd.DataFrame, run_ts: str | None = None) -> list[ReplayWritePlan]:
    rehydrated = rehydrate_event_mapping_source(source_df)
    mapped_df = event_mapping.main(rehydrated)
    if mapped_df is None or not isinstance(mapped_df, pd.DataFrame):
        raise ReplaySkipError("event_mapping.main() did not return a dataframe")
    if mapped_df.shape[0] == 0:
        return []
    if "dataiku_category" not in mapped_df.columns:
        raise ReplaySkipError("Mapped dataframe is missing dataiku_category")

    replay_run_ts = _resolve_replay_run_ts(source_df, source.run_date, run_ts)

    plans: list[ReplayWritePlan] = []
    for module_name, group_df in mapped_df.groupby("dataiku_category"):
        module_name_str = str(module_name)
        silver_df = normalize_silver(
            df=group_df,
            instance_name=source.instance_name,
            run_ts=replay_run_ts,
            **resolve_event_mapping_normalize_args(module_name=module_name_str),
        )
        event_date = resolve_replay_event_date(silver_df=silver_df, fallback_date=source.run_date)
        plans.append(
            ReplayWritePlan(
                output_path=build_event_mapping_output_path(source=source, module_name=module_name_str, event_date=event_date),
                silver_df=silver_df,
                dq=check_silver_dq(silver_df),
                module_name=module_name_str,
                event_date=event_date,
            )
        )
    return plans


def upload_event_mapping_replacements(*, target: DSSFolderTarget, folder: Any, plans: list[ReplayWritePlan]) -> ReplacementUploadResult:
    dq_errors = [error for plan in plans if not plan.dq.ok for error in plan.dq.errors]
    if dq_errors:
        return ReplacementUploadResult(
            status="dq_failed",
            dq_errors=tuple(dq_errors),
            message=", ".join(dq_errors),
        )

    written_paths: list[str] = []
    try:
        for plan in plans:
            upload_parquet(
                target=target,
                output_path=Path(str(plan.output_path)),
                output_base_dir=Path("/"),
                df=plan.silver_df,
                compression="snappy",
            )
            written_paths.append(str(plan.output_path))
    except Exception as exc:
        cleaned_paths, cleanup_errors = cleanup_written_replacements(folder=folder, paths=written_paths)
        if cleanup_errors:
            return ReplacementUploadResult(
                status="upload_failed_cleanup_failed",
                written_paths=tuple(written_paths),
                cleanup_paths=cleaned_paths,
                message=format_partial_write_message(
                    base_message=f"Upload failed: {exc!r}; cleanup failed for {len(cleanup_errors)} replacement(s)",
                    written_paths=tuple(written_paths),
                    cleanup_paths=cleaned_paths,
                ),
            )
        return ReplacementUploadResult(
            status="upload_failed_cleaned",
            written_paths=tuple(written_paths),
            cleanup_paths=cleaned_paths,
            message=format_partial_write_message(
                base_message=f"Upload failed: {exc!r}; cleaned {len(cleaned_paths)} replacement(s)",
                written_paths=tuple(written_paths),
                cleanup_paths=cleaned_paths,
            ),
        )

    return ReplacementUploadResult(
        status="uploaded",
        written_paths=tuple(written_paths),
        message=f"Wrote {len(written_paths)} replacement file(s)",
    )
