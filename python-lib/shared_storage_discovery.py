from __future__ import annotations

import base64
from typing import Any, Iterator

import boto3
from botocore.config import Config
from botocore.credentials import Credentials
from botocore.session import Session as BotocoreSession

from shared_duckdb.context import StorageContext
from shared_duckdb.storage_config import _connection_info, decrypt_string


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


def _iter_s3_paths(ctx: StorageContext, *, relative_prefix: str, suffix: str | None) -> Iterator[str]:
    info = _connection_info(ctx, allow_cached=True)
    params = info.get("params") or {}
    credential_mode = params.get("credentialsMode")

    session = BotocoreSession()
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

    session._credentials = credentials
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
    prefix = _physical_prefix(folder_root=ctx.folder_root, relative_prefix=relative_prefix)
    for page in paginator.paginate(Bucket=ctx.bucket_or_container, Prefix=prefix):
        for item in page.get("Contents") or []:
            key = str(item.get("Key") or "")
            rel = _relative_from_physical_key(folder_root=ctx.folder_root, object_key=key)
            if rel is None or rel.endswith("/") or not _matches_suffix(rel, suffix):
                continue
            yield rel


def _build_azure_blob_service_client(ctx: StorageContext):
    from azure.storage.blob import BlobServiceClient
    from azure.identity import ClientSecretCredential

    info = _connection_info(ctx, allow_cached=True)
    params = info.get("params") or {}
    auth_type = params.get("authType")
    account_name = params.get("storageAccount")
    if auth_type == "SHARED_KEY":
        account_url = f"https://{account_name}.blob.core.windows.net"
        return BlobServiceClient(account_url=account_url, credential=params["accessKey"])
    if auth_type == "OAUTH2_APP":
        credential = ClientSecretCredential(
            tenant_id=params["tenantId"],
            client_id=params["appId"],
            client_secret=params["appSecret"],
        )
        account_url = f"https://{account_name}.blob.core.windows.net"
        return BlobServiceClient(account_url=account_url, credential=credential)
    raise RuntimeError(f"Unsupported Azure authentication type for managed-folder discovery: {auth_type}")


def _iter_azure_paths(ctx: StorageContext, *, relative_prefix: str, suffix: str | None) -> Iterator[str]:
    client = _build_azure_blob_service_client(ctx)
    prefix = _physical_prefix(folder_root=ctx.folder_root, relative_prefix=relative_prefix)
    container = ctx.bucket_or_container
    for blob in client.get_container_client(container).list_blobs(name_starts_with=prefix):
        name = str(getattr(blob, "name", "") or "")
        rel = _relative_from_physical_key(folder_root=ctx.folder_root, object_key=name)
        if rel is None or rel.endswith("/") or not _matches_suffix(rel, suffix):
            continue
        yield rel


def _build_gcs_client(ctx: StorageContext):
    from google.cloud import storage
    from google.auth.credentials import AnonymousCredentials
    import gcsfs

    gcs_hmac = ((ctx.project_handle.get_variables() or {}).get("local") or {}).get("gcs_hmac")
    if gcs_hmac:
        salt = base64.b64decode(gcs_hmac["salt"])
        ciphertext = base64.b64decode(gcs_hmac["ciphertext"])
        access_key = gcs_hmac["access_key"]
        hmac_secret = decrypt_string(ciphertext, password="DF2!&sEkm)f4}i99,e&9bS:Wj", salt=salt)
        fs = gcsfs.GCSFileSystem(access=access_key, secret=hmac_secret)
        return ("gcsfs", fs)

    info = _connection_info(ctx, allow_cached=True)
    params = info.get("params") or {}
    credentials_mode = params.get("credentialsMode") or params.get("authType")
    if credentials_mode == "ENVIRONMENT":
        client = storage.Client(credentials=AnonymousCredentials())
        return ("google-cloud-storage", client)

    raise RuntimeError(
        f"Unsupported GCS credential mode for managed-folder discovery: {credentials_mode or 'filesystem'}"
    )


def _iter_gcs_paths(ctx: StorageContext, *, relative_prefix: str, suffix: str | None) -> Iterator[str]:
    backend, client = _build_gcs_client(ctx)
    prefix = _physical_prefix(folder_root=ctx.folder_root, relative_prefix=relative_prefix)
    bucket = ctx.bucket_or_container
    if backend == "gcsfs":
        for item in client.ls(f"{bucket}/{prefix}", detail=True):
            name = str((item or {}).get("name") or "")
            key = name.split("/", 1)[1] if "/" in name else name
            rel = _relative_from_physical_key(folder_root=ctx.folder_root, object_key=key)
            if rel is None or rel.endswith("/") or not _matches_suffix(rel, suffix):
                continue
            yield rel
        return

    for blob in client.list_blobs(bucket, prefix=prefix):
        name = str(getattr(blob, "name", "") or "")
        rel = _relative_from_physical_key(folder_root=ctx.folder_root, object_key=name)
        if rel is None or rel.endswith("/") or not _matches_suffix(rel, suffix):
            continue
        yield rel


def iter_managed_folder_paths(
    storage_ctx: StorageContext,
    *,
    relative_prefix: str,
    suffix: str | None = None,
) -> Iterator[str]:
    connection_type = storage_ctx.connection_type
    if connection_type == "EC2":
        yield from _iter_s3_paths(storage_ctx, relative_prefix=relative_prefix, suffix=suffix)
        return
    if connection_type == "Azure":
        yield from _iter_azure_paths(storage_ctx, relative_prefix=relative_prefix, suffix=suffix)
        return
    if connection_type == "GCS":
        yield from _iter_gcs_paths(storage_ctx, relative_prefix=relative_prefix, suffix=suffix)
        return
    raise RuntimeError(f"Unsupported managed-folder provider for native discovery: {connection_type}")
