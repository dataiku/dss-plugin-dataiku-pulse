from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterator, Protocol

import pandas as pd


class StorageContextLike(Protocol):
    connection_type: str
    folder_root: str
    bucket_or_container: str | None
    project_handle: Any
    connection_handle: Any
    connection_name: str
    cached_connection_info: dict[str, Any]


PATH_INDEX_COLUMNS = [
    "full_path",
    "header_path",
    "layer",
    "category",
    "module",
    "instance_name",
    "year",
    "month",
    "day",
    "base_name",
]

COMPACT_OUTPUT_PREFIX = "compact_silver-"


@dataclass(frozen=True)
class SelectedPathRecord:
    relative_path: str
    full_path: str
    base_name: str
    layer: str
    category: str
    module: str
    instance_name: str
    year: str
    month: str
    day: str


@dataclass(frozen=True)
class SelectedDayPaths:
    total_matched_paths: int
    filtered_matching_paths: int
    skipped_compact_outputs: int
    excluded_recent_paths: int
    eligible_paths: int
    cutoff_date: date
    minimum_age_days: int
    year: str
    month: str
    day: str
    selected_records: list[SelectedPathRecord]

    @property
    def full_paths(self) -> list[str]:
        return [record.full_path for record in self.selected_records]

    @property
    def relative_paths(self) -> list[str]:
        return [record.relative_path for record in self.selected_records]


@dataclass(frozen=True)
class SelectedPartitionPaths:
    year: str
    month: str
    day: str
    selected_records: list[SelectedPathRecord]

    @property
    def full_paths(self) -> list[str]:
        return [record.full_path for record in self.selected_records]

    @property
    def relative_paths(self) -> list[str]:
        return [record.relative_path for record in self.selected_records]


@dataclass(frozen=True)
class SelectedPartitionBatch:
    total_matched_paths: int
    filtered_matching_paths: int
    skipped_compact_outputs: int
    excluded_recent_paths: int
    eligible_paths: int
    cutoff_date: date
    minimum_age_days: int
    selected_partitions: list[SelectedPartitionPaths]


def _normalize_relative_prefix(relative_prefix: str) -> str:
    prefix = str(relative_prefix or "").strip().strip("/")
    if not prefix:
        return ""
    return f"{prefix}/"


def _normalize_folder_root(folder_root: str) -> str:
    root = str(folder_root or "").strip().strip("/")
    if not root:
        return ""
    return f"{root}/"


def _physical_prefix(*, folder_root: str, relative_prefix: str) -> str:
    return f"{_normalize_folder_root(folder_root)}{_normalize_relative_prefix(relative_prefix)}"


def _relative_from_physical_key(*, folder_root: str, object_key: str) -> str | None:
    root = _normalize_folder_root(folder_root)
    key = str(object_key or "")
    if root and not key.startswith(root):
        return None
    rel = key[len(root):] if root else key
    rel = rel.lstrip("/")
    if not rel:
        return None
    return f"/{rel}"


def _matches_suffix(path: str, suffix: str | None) -> bool:
    if not suffix:
        return True
    return path.endswith(suffix)


def _header_path(storage_ctx: StorageContextLike) -> str:
    bucket_or_container = str(storage_ctx.bucket_or_container or "").strip().strip("/")
    if not bucket_or_container:
        raise ValueError("Managed-folder discovery context is missing bucket/container for native path indexing")
    root = str(storage_ctx.folder_root or "").strip().strip("/")
    if not root:
        return bucket_or_container
    return f"{bucket_or_container}/{root}"


def _parse_partition_segment(segment: str, *, expected_key: str, source_path: str) -> str:
    key, separator, value = str(segment).partition("=")
    if separator != "=" or key != expected_key or not value:
        raise ValueError(f"Unexpected managed-folder path format for {source_path!r}: expected {expected_key}=...")
    return value


def _path_index_row(storage_ctx: StorageContextLike, relative_path: str) -> dict[str, str]:
    path = str(relative_path or "").strip()
    parts = path.lstrip("/").split("/")
    if len(parts) != 8:
        raise ValueError(f"Unexpected managed-folder path format for {path!r}: expected 8 path segments")

    layer = parts[0]
    if layer != "silver":
        raise ValueError(f"Unexpected managed-folder path format for {path!r}: expected silver layer")

    category = _parse_partition_segment(parts[1], expected_key="category", source_path=path)
    module = _parse_partition_segment(parts[2], expected_key="module", source_path=path)
    instance_name = _parse_partition_segment(parts[3], expected_key="instance_name", source_path=path)
    year = _parse_partition_segment(parts[4], expected_key="year", source_path=path)
    month = _parse_partition_segment(parts[5], expected_key="month", source_path=path)
    day = _parse_partition_segment(parts[6], expected_key="day", source_path=path)
    base_name = parts[7]
    if not base_name:
        raise ValueError(f"Unexpected managed-folder path format for {path!r}: missing base file name")

    header_path = _header_path(storage_ctx)
    full_path = f"{header_path}/{path.lstrip('/')}"
    return {
        "full_path": full_path,
        "header_path": header_path,
        "layer": layer,
        "category": category,
        "module": module,
        "instance_name": instance_name,
        "year": year,
        "month": month,
        "day": day,
        "base_name": base_name,
    }


def _selected_path_record(storage_ctx: StorageContextLike, relative_path: str) -> SelectedPathRecord:
    row = _path_index_row(storage_ctx, relative_path)
    return SelectedPathRecord(
        relative_path=str(relative_path),
        full_path=row["full_path"],
        base_name=row["base_name"],
        layer=row["layer"],
        category=row["category"],
        module=row["module"],
        instance_name=row["instance_name"],
        year=row["year"],
        month=row["month"],
        day=row["day"],
    )


def _is_compact_output(base_name: str) -> bool:
    return str(base_name).startswith(COMPACT_OUTPUT_PREFIX) and str(base_name).endswith(".parquet")


def _day_key(*, year: str, month: str, day: str) -> tuple[int, int, int]:
    try:
        return int(year), int(month), int(day)
    except ValueError as exc:
        raise ValueError(
            f"Unexpected managed-folder path format: non-numeric day partition year={year!r} month={month!r} day={day!r}"
        ) from exc


def _partition_date(*, year: str, month: str, day: str) -> date:
    try:
        return date(int(year), int(month), int(day))
    except ValueError as exc:
        raise ValueError(
            f"Unexpected managed-folder path format: invalid UTC calendar date year={year!r} month={month!r} day={day!r}"
        ) from exc


def _iter_provider_object_keys(storage_ctx: StorageContextLike, *, physical_prefix: str) -> Iterator[str]:
    connection_type = storage_ctx.connection_type
    if connection_type == "EC2":
        from shared_storage_discovery_s3 import iter_s3_object_keys

        yield from iter_s3_object_keys(storage_ctx, physical_prefix=physical_prefix)
        return
    if connection_type == "Azure":
        from shared_storage_discovery_azure import iter_azure_blob_names

        yield from iter_azure_blob_names(storage_ctx, physical_prefix=physical_prefix)
        return
    if connection_type == "GCS":
        from shared_storage_discovery_gcs import iter_gcs_object_names

        yield from iter_gcs_object_names(storage_ctx, physical_prefix=physical_prefix)
        return
    raise RuntimeError(f"Unsupported managed-folder provider for native discovery: {connection_type}")


def iter_managed_folder_paths(
    storage_ctx: StorageContextLike,
    *,
    relative_prefix: str,
    suffix: str | None = None,
) -> Iterator[str]:
    if storage_ctx.connection_type not in {"EC2", "Azure", "GCS"}:
        raise RuntimeError(f"Unsupported managed-folder provider for native discovery: {storage_ctx.connection_type}")
    physical_prefix = _physical_prefix(folder_root=storage_ctx.folder_root, relative_prefix=relative_prefix)
    for object_key in _iter_provider_object_keys(storage_ctx, physical_prefix=physical_prefix):
        rel = _relative_from_physical_key(folder_root=storage_ctx.folder_root, object_key=object_key)
        if rel is None or rel.endswith("/") or not _matches_suffix(rel, suffix):
            continue
        yield rel


def count_managed_folder_paths(
    storage_ctx: StorageContextLike,
    *,
    relative_prefix: str,
    suffix: str | None = None,
) -> int:
    return sum(1 for _ in iter_managed_folder_paths(storage_ctx, relative_prefix=relative_prefix, suffix=suffix))


def collect_managed_folder_snapshot(
    storage_ctx: StorageContextLike,
    *,
    relative_prefix: str,
    suffix: str | None = None,
    progress_interval: int | None = None,
    progress_callback: Callable[[int], None] | None = None,
) -> list[str]:
    source_paths: list[str] = []
    for path in iter_managed_folder_paths(storage_ctx, relative_prefix=relative_prefix, suffix=suffix):
        source_paths.append(path)
        if (
            progress_callback is not None
            and progress_interval is not None
            and progress_interval > 0
            and len(source_paths) % progress_interval == 0
        ):
            progress_callback(len(source_paths))
    source_paths.sort()
    return source_paths


def build_managed_folder_path_index(
    storage_ctx: StorageContextLike,
    *,
    relative_prefix: str,
    suffix: str | None = None,
) -> pd.DataFrame:
    rows = [
        _path_index_row(storage_ctx, path)
        for path in iter_managed_folder_paths(storage_ctx, relative_prefix=relative_prefix, suffix=suffix)
    ]
    return pd.DataFrame(rows, columns=PATH_INDEX_COLUMNS)


def _selected_partition_batch_to_day_paths(batch: SelectedPartitionBatch) -> SelectedDayPaths:
    if not batch.selected_partitions:
        raise ValueError("No managed-folder paths matched the requested exact partition filters")
    selected = batch.selected_partitions[0]
    return SelectedDayPaths(
        total_matched_paths=batch.total_matched_paths,
        filtered_matching_paths=batch.filtered_matching_paths,
        skipped_compact_outputs=batch.skipped_compact_outputs,
        excluded_recent_paths=batch.excluded_recent_paths,
        eligible_paths=batch.eligible_paths,
        cutoff_date=batch.cutoff_date,
        minimum_age_days=batch.minimum_age_days,
        year=selected.year,
        month=selected.month,
        day=selected.day,
        selected_records=selected.selected_records,
    )


def select_latest_partition_paths_batch(
    storage_ctx: StorageContextLike,
    *,
    relative_prefix: str,
    suffix: str | None = None,
    partition_filters: dict[str, str],
    partition_count: int,
    minimum_age_days: int = 0,
    utc_today: date | None = None,
) -> SelectedPartitionBatch:
    if partition_count <= 0:
        raise ValueError(f"partition_count must be positive, got {partition_count}")
    if minimum_age_days < 0:
        raise ValueError(f"minimum_age_days must be non-negative, got {minimum_age_days}")

    today_utc = utc_today or datetime.now(timezone.utc).date()
    cutoff_date = today_utc - timedelta(days=minimum_age_days)
    retained_records: dict[tuple[int, int, int], list[SelectedPathRecord]] = {}
    retained_parts: dict[tuple[int, int, int], tuple[str, str, str]] = {}
    total_matched_paths = 0
    filtered_matching_paths = 0
    skipped_compact_outputs = 0
    excluded_recent_paths = 0
    eligible_paths = 0

    for relative_path in iter_managed_folder_paths(storage_ctx, relative_prefix=relative_prefix, suffix=suffix):
        total_matched_paths += 1
        record = _selected_path_record(storage_ctx, relative_path)
        if any(getattr(record, column_name) != expected_value for column_name, expected_value in partition_filters.items()):
            continue

        if _is_compact_output(record.base_name):
            skipped_compact_outputs += 1
            continue

        filtered_matching_paths += 1
        partition_day = _partition_date(year=record.year, month=record.month, day=record.day)
        if partition_day >= cutoff_date:
            excluded_recent_paths += 1
            continue

        eligible_paths += 1
        candidate_day = _day_key(year=record.year, month=record.month, day=record.day)
        candidate_parts = (record.year, record.month, record.day)

        if candidate_day in retained_records:
            retained_records[candidate_day].append(record)
            continue

        if len(retained_records) < partition_count:
            retained_records[candidate_day] = [record]
            retained_parts[candidate_day] = candidate_parts
            continue

        oldest_retained_day = min(retained_records)
        if candidate_day > oldest_retained_day:
            del retained_records[oldest_retained_day]
            del retained_parts[oldest_retained_day]
            retained_records[candidate_day] = [record]
            retained_parts[candidate_day] = candidate_parts

    if filtered_matching_paths > 0 and eligible_paths <= 0:
        raise ValueError(
            f"All exact-filter matches are excluded by minimum_age_days={minimum_age_days}; cutoff_date={cutoff_date.isoformat()}"
        )

    retained_days = sorted(retained_records, reverse=True)
    if not retained_days:
        raise ValueError("No managed-folder paths matched the requested exact partition filters")
    if len(retained_days) < partition_count:
        raise ValueError(
            f"Only {len(retained_days)} eligible managed-folder day partition(s) matched the requested exact filters; required {partition_count}"
        )

    selected_partitions = [
        SelectedPartitionPaths(
            year=retained_parts[retained_day][0],
            month=retained_parts[retained_day][1],
            day=retained_parts[retained_day][2],
            selected_records=sorted(retained_records[retained_day], key=lambda item: item.relative_path),
        )
        for retained_day in retained_days
    ]
    return SelectedPartitionBatch(
        total_matched_paths=total_matched_paths,
        filtered_matching_paths=filtered_matching_paths,
        skipped_compact_outputs=skipped_compact_outputs,
        excluded_recent_paths=excluded_recent_paths,
        eligible_paths=eligible_paths,
        cutoff_date=cutoff_date,
        minimum_age_days=minimum_age_days,
        selected_partitions=selected_partitions,
    )


def select_latest_partition_paths(
    storage_ctx: StorageContextLike,
    *,
    relative_prefix: str,
    suffix: str | None = None,
    partition_filters: dict[str, str],
    minimum_age_days: int = 0,
    utc_today: date | None = None,
) -> SelectedDayPaths:
    batch = select_latest_partition_paths_batch(
        storage_ctx,
        relative_prefix=relative_prefix,
        suffix=suffix,
        partition_filters=partition_filters,
        partition_count=1,
        minimum_age_days=minimum_age_days,
        utc_today=utc_today,
    )
    return _selected_partition_batch_to_day_paths(batch)


def filter_path_index(df: pd.DataFrame, **filters: str) -> pd.DataFrame:
    filtered = df.copy()
    for column_name, expected_value in filters.items():
        if column_name not in filtered.columns:
            raise ValueError(f"Unknown path-index filter column: {column_name}")
        filtered = filtered.loc[filtered[column_name] == expected_value]
    return filtered.reset_index(drop=True)


def select_latest_partition_day(df: pd.DataFrame) -> tuple[str, str, str]:
    required_columns = {"year", "month", "day"}
    missing_columns = required_columns.difference(df.columns)
    if missing_columns:
        raise ValueError(f"Path-index DataFrame is missing required day columns: {sorted(missing_columns)}")
    if df.empty:
        raise ValueError("Path-index DataFrame is empty; cannot select a day")

    unique_days = df.loc[:, ["year", "month", "day"]].drop_duplicates().copy()
    for column_name in ["year", "month", "day"]:
        numeric_values = pd.to_numeric(unique_days[column_name], errors="coerce")
        if numeric_values.isna().any():
            raise ValueError(f"Path-index DataFrame contains non-numeric {column_name} values; cannot select a day")
        unique_days[f"_{column_name}"] = numeric_values.astype(int)

    unique_days = unique_days.sort_values(by=["_year", "_month", "_day"], ascending=False, kind="stable")
    selected = unique_days.iloc[0]
    return str(selected["year"]), str(selected["month"]), str(selected["day"])


__all__ = [
    "PATH_INDEX_COLUMNS",
    "SelectedPathRecord",
    "SelectedDayPaths",
    "SelectedPartitionPaths",
    "SelectedPartitionBatch",
    "StorageContextLike",
    "build_managed_folder_path_index",
    "collect_managed_folder_snapshot",
    "count_managed_folder_paths",
    "filter_path_index",
    "iter_managed_folder_paths",
    "select_latest_partition_paths",
    "select_latest_partition_paths_batch",
    "select_latest_partition_day",
]
