from __future__ import annotations

from typing import Any, Protocol

import pandas as pd

from shared_storage_credentials import resolve_aws_access


class StorageContextLike(Protocol):
    connection_type: str
    connection_name: str
    connection_handle: Any
    cached_connection_info: dict[str, Any]
    project_handle: Any


def read_s3_parquet_files(storage_ctx: StorageContextLike, *, full_paths: list[str]) -> pd.DataFrame:
    if storage_ctx.connection_type != "EC2":
        raise RuntimeError(
            f"S3 parquet reader only supports EC2 managed-folder connections in Phase 3: {storage_ctx.connection_type}"
        )
    if not full_paths:
        raise ValueError("S3 parquet reader requires at least one selected source path")

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
    raw_df = dataset.to_table().to_pandas()
    deduped_df = raw_df.drop_duplicates()
    deduped_df.attrs["files_read"] = len(full_paths)
    deduped_df.attrs["raw_rows"] = int(len(raw_df))
    deduped_df.attrs["rows_after_drop_duplicates"] = int(len(deduped_df))
    deduped_df.attrs["output_column_count"] = int(len(deduped_df.columns))
    return deduped_df


__all__ = ["read_s3_parquet_files"]
