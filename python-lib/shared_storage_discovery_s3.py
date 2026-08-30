from __future__ import annotations

from typing import Any, Iterator, Protocol

import boto3
from botocore.exceptions import ClientError
from botocore.config import Config

from shared_storage_credentials import resolve_aws_access


class StorageContextLike(Protocol):
    bucket_or_container: str | None
    connection_handle: Any
    connection_name: str
    cached_connection_info: dict[str, Any]


S3_EXPIRED_TOKEN_CODES = {"ExpiredToken"}
S3_EXPIRED_TOKEN_RETRY_LIMIT = 3


def _build_s3_client(ctx: StorageContextLike):
    aws_access = resolve_aws_access(ctx)
    return boto3.client(
        "s3",
        region_name=aws_access["region_name"],
        config=Config(signature_version="s3v4"),
        aws_access_key_id=aws_access["access_key"],
        aws_secret_access_key=aws_access["secret_key"],
        aws_session_token=aws_access["session_token"],
    )


def _is_expired_token_error(exc: ClientError) -> bool:
    return (
        str((exc.response.get("Error") or {}).get("Code") or "")
        in S3_EXPIRED_TOKEN_CODES
    )


def iter_s3_object_keys(
    ctx: StorageContextLike, *, physical_prefix: str
) -> Iterator[str]:
    client = _build_s3_client(ctx)
    continuation_token: str | None = None
    expired_token_retries = 0
    while True:
        kwargs = {"Bucket": ctx.bucket_or_container, "Prefix": physical_prefix}
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token
        try:
            page = client.list_objects_v2(**kwargs)
        except ClientError as exc:
            if not _is_expired_token_error(exc):
                raise
            if expired_token_retries >= S3_EXPIRED_TOKEN_RETRY_LIMIT:
                raise RuntimeError(
                    "S3 object listing failed after refreshing expired credentials"
                ) from exc
            expired_token_retries += 1
            client = _build_s3_client(ctx)
            continue
        expired_token_retries = 0
        for item in page.get("Contents") or []:
            key = str(item.get("Key") or "")
            if key:
                yield key
        if not page.get("IsTruncated"):
            break
        next_token = str(page.get("NextContinuationToken") or "")
        if not next_token:
            raise RuntimeError(
                "S3 object listing was truncated without a continuation token"
            )
        continuation_token = next_token


def iter_s3_child_prefixes(
    ctx: StorageContextLike, *, physical_prefix: str
) -> Iterator[str]:
    client = _build_s3_client(ctx)
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(
        Bucket=ctx.bucket_or_container, Prefix=physical_prefix, Delimiter="/"
    ):
        for item in page.get("CommonPrefixes") or []:
            prefix = str(item.get("Prefix") or "")
            if prefix:
                yield prefix


__all__ = ["iter_s3_child_prefixes", "iter_s3_object_keys"]
