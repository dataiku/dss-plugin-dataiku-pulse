from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

import pandas as pd

from data_collection.audit_logs_modules import event_mapping
from data_collection.data_normalizer import DQResult, check_silver_dq, normalize_silver
from data_collection.helper import DSSFolderTarget, upload_parquet

logger = logging.getLogger(__name__)

SILVER_EVENT_MAPPING_PREFIX = "/silver/category=event_mapping/"


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
            return pd.read_parquet(stream)

    if hasattr(folder, "get_file"):
        with folder.get_file(cleaned) as response:
            return pd.read_parquet(response.raw)

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
    if "msgtype" in rehydrated.columns and "message_msgType" not in rehydrated.columns:
        rehydrated["message_msgType"] = rehydrated["msgtype"]
    if "dataiku_category" in rehydrated.columns:
        rehydrated = rehydrated.drop(columns=["dataiku_category"])
    if "extras" in rehydrated.columns:
        rehydrated = rehydrated.drop(columns=["extras"])
    return rehydrated


def resolve_event_mapping_normalize_args(*, module_name: str) -> dict[str, Any]:
    return {
        "category": "audit_dataiku_usage",
        "module": "audit_metadata",
        "todo_section": "audit",
        "flatten_base": ("audit_dataiku_usage", "audit_metadata"),
        "flatten_variant": module_name,
    }


def resolve_replay_event_date(*, silver_df: pd.DataFrame, fallback_date: date) -> date:
    if "timestamp" in silver_df.columns:
        event_ts = pd.to_datetime(silver_df["timestamp"].max(), utc=True, errors="coerce")
        if not pd.isna(event_ts):
            return event_ts.date()
    return fallback_date


def build_event_mapping_output_path(*, source: EventMappingSourceInfo, module_name: str, event_date: date) -> PurePosixPath:
    return (
        PurePosixPath("/")
        / "silver"
        / "category=event_mapping"
        / f"module={module_name}"
        / f"instance_name={source.instance_name}"
        / f"year={event_date.year:04d}"
        / f"month={event_date.month:02d}"
        / f"day={event_date.day:02d}"
        / source.filename
    )


def _resolve_replay_run_ts(source_df: pd.DataFrame, fallback_date: date, explicit_run_ts: str | None) -> str:
    if explicit_run_ts:
        return explicit_run_ts
    if "run_ts" in source_df.columns:
        non_null = source_df["run_ts"].dropna()
        if non_null.shape[0] > 0:
            return str(non_null.astype(str).iloc[0])
    return f"{fallback_date.isoformat()}T00:00:00Z"


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


def upload_event_mapping_replacements(*, target: DSSFolderTarget, plans: list[ReplayWritePlan]) -> tuple[tuple[str, ...], list[str]]:
    written_paths: list[str] = []
    dq_errors: list[str] = []
    for plan in plans:
        if not plan.dq.ok:
            dq_errors.extend(plan.dq.errors)
            continue
        upload_parquet(
            target=target,
            output_path=Path(str(plan.output_path)),
            output_base_dir=Path("/"),
            df=plan.silver_df,
            compression="snappy",
        )
        written_paths.append(str(plan.output_path))
    return tuple(written_paths), dq_errors
