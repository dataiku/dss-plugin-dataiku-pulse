from __future__ import annotations

import math
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import pandas as pd

from shared_storage_credentials import resolve_aws_access


COMPACT_SILVER_S3_READ_BATCH_SIZE = 5_000


class StorageContextLike(Protocol):
    connection_type: str
    connection_name: str
    connection_handle: Any
    cached_connection_info: dict[str, Any]
    project_handle: Any


@dataclass(frozen=True)
class S3ParquetReadBatch:
    dataframe: pd.DataFrame
    batch_number: int
    total_batches: int
    files_read: int
    raw_rows: int


def _validate_s3_read_request(storage_ctx: StorageContextLike, full_paths: Sequence[str]) -> None:
    if storage_ctx.connection_type != "EC2":
        raise RuntimeError(
            f"S3 parquet reader only supports EC2 managed-folder connections in Phase 3: {storage_ctx.connection_type}"
        )
    if not full_paths:
        raise ValueError("S3 parquet reader requires at least one selected source path")


def _iter_path_batches(full_paths: Sequence[str], *, batch_size: int) -> Iterator[Sequence[str]]:
    if batch_size <= 0:
        raise ValueError(f"S3 parquet read batch size must be positive, got {batch_size}")
    for start in range(0, len(full_paths), batch_size):
        yield full_paths[start : start + batch_size]


def read_s3_parquet_file_batch(storage_ctx: StorageContextLike, *, full_paths: Sequence[str]) -> pd.DataFrame:
    _validate_s3_read_request(storage_ctx, full_paths)

    from pyarrow import dataset as pa_dataset
    from pyarrow import fs as pa_fs

    aws_access = resolve_aws_access(storage_ctx)
    filesystem = pa_fs.S3FileSystem(
        access_key=aws_access["access_key"],
        secret_key=aws_access["secret_key"],
        session_token=aws_access["session_token"],
        region=aws_access["region_name"],
    )
    dataset = pa_dataset.dataset(full_paths, filesystem=filesystem, format="parquet")
    return dataset.to_table().to_pandas()


def iter_s3_parquet_file_batches(
    storage_ctx: StorageContextLike,
    *,
    full_paths: Sequence[str],
    batch_size: int = COMPACT_SILVER_S3_READ_BATCH_SIZE,
    storage_ctx_factory: Callable[[], StorageContextLike] | None = None,
) -> Iterator[S3ParquetReadBatch]:
    _validate_s3_read_request(storage_ctx, full_paths)
    if batch_size <= 0:
        raise ValueError(f"S3 parquet read batch size must be positive, got {batch_size}")
    total_batches = int(math.ceil(len(full_paths) / batch_size))
    for batch_number, batch_paths in enumerate(_iter_path_batches(full_paths, batch_size=batch_size), start=1):
        batch_storage_ctx = storage_ctx_factory() if storage_ctx_factory is not None else storage_ctx
        raw_df = read_s3_parquet_file_batch(batch_storage_ctx, full_paths=batch_paths)
        yield S3ParquetReadBatch(
            dataframe=raw_df,
            batch_number=batch_number,
            total_batches=total_batches,
            files_read=len(batch_paths),
            raw_rows=int(len(raw_df)),
        )


def read_s3_parquet_files(
    storage_ctx: StorageContextLike,
    *,
    full_paths: list[str],
    batch_size: int = COMPACT_SILVER_S3_READ_BATCH_SIZE,
) -> pd.DataFrame:
    _validate_s3_read_request(storage_ctx, full_paths)

    batches = [batch.dataframe for batch in iter_s3_parquet_file_batches(storage_ctx, full_paths=full_paths, batch_size=batch_size)]
    raw_df = pd.concat(batches, ignore_index=True) if len(batches) > 1 else batches[0]
    deduped_df = raw_df.drop_duplicates()
    deduped_df.attrs["files_read"] = len(full_paths)
    deduped_df.attrs["raw_rows"] = int(len(raw_df))
    deduped_df.attrs["rows_after_drop_duplicates"] = int(len(deduped_df))
    deduped_df.attrs["output_column_count"] = int(len(deduped_df.columns))
    return deduped_df


__all__ = [
    "COMPACT_SILVER_S3_READ_BATCH_SIZE",
    "S3ParquetReadBatch",
    "iter_s3_parquet_file_batches",
    "read_s3_parquet_file_batch",
    "read_s3_parquet_files",
]
