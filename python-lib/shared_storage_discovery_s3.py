from __future__ import annotations

from typing import Any, Iterator, Protocol

import boto3
from botocore.config import Config
from botocore.credentials import Credentials

from shared_storage_credentials import connection_info


class StorageContextLike(Protocol):
    bucket_or_container: str | None
    connection_handle: Any
    connection_name: str
    cached_connection_info: dict[str, Any]


def iter_s3_object_keys(ctx: StorageContextLike, *, physical_prefix: str) -> Iterator[str]:
    info = connection_info(ctx, allow_cached=True)
    params = info.get("params") or {}
    credential_mode = params.get("credentialsMode")

    if credential_mode == "KEYPAIR":
        credentials = Credentials(params["accessKey"], params["secretKey"])
    elif credential_mode in {"STS_ASSUME_ROLE", "ENVIRONMENT"}:
        resolved = info.get("resolvedAWSCredential") or {}
        credentials = Credentials(
            resolved["accessKey"],
            resolved["secretKey"],
            resolved["sessionToken"],
        )
    else:
        raise RuntimeError(f"Unsupported AWS credentials mode for managed-folder discovery: {credential_mode}")

    region_name = params.get("regionOrEndpoint") or None
    client = boto3.client(
        "s3",
        region_name=region_name,
        config=Config(signature_version="s3v4"),
        aws_access_key_id=credentials.access_key,
        aws_secret_access_key=credentials.secret_key,
        aws_session_token=credentials.token,
    )
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=ctx.bucket_or_container, Prefix=physical_prefix):
        for item in page.get("Contents") or []:
            key = str(item.get("Key") or "")
            if key:
                yield key


__all__ = ["iter_s3_object_keys"]
