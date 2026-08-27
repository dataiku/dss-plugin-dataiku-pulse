from __future__ import annotations

from typing import Any, Iterator, Protocol

import boto3
from botocore.config import Config

from shared_storage_credentials import resolve_aws_access


class StorageContextLike(Protocol):
    bucket_or_container: str | None
    connection_handle: Any
    connection_name: str
    cached_connection_info: dict[str, Any]


def iter_s3_object_keys(ctx: StorageContextLike, *, physical_prefix: str) -> Iterator[str]:
    aws_access = resolve_aws_access(ctx)
    client = boto3.client(
        "s3",
        region_name=aws_access["region_name"],
        config=Config(signature_version="s3v4"),
        aws_access_key_id=aws_access["access_key"],
        aws_secret_access_key=aws_access["secret_key"],
        aws_session_token=aws_access["session_token"],
    )
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=ctx.bucket_or_container, Prefix=physical_prefix):
        for item in page.get("Contents") or []:
            key = str(item.get("Key") or "")
            if key:
                yield key


__all__ = ["iter_s3_object_keys"]
