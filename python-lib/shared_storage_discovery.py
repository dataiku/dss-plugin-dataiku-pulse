from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterator, Protocol

import boto3
from botocore.config import Config
from botocore.credentials import Credentials

from shared_storage_credentials import connection_info, resolve_gcs_hmac_credentials

if TYPE_CHECKING:
    from shared_duckdb.context import StorageContext


class StorageContextLike(Protocol):
    connection_type: str
    folder_root: str
    bucket_or_container: str | None
    project_handle: Any
    connection_handle: Any
    connection_name: str
    cached_connection_info: dict[str, Any]


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


def _iter_s3_paths(ctx: StorageContextLike, *, relative_prefix: str, suffix: str | None) -> Iterator[str]:
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
    prefix = _physical_prefix(folder_root=ctx.folder_root, relative_prefix=relative_prefix)
    for page in paginator.paginate(Bucket=ctx.bucket_or_container, Prefix=prefix):
        for item in page.get("Contents") or []:
            key = str(item.get("Key") or "")
            rel = _relative_from_physical_key(folder_root=ctx.folder_root, object_key=key)
            if rel is None or rel.endswith("/") or not _matches_suffix(rel, suffix):
                continue
            yield rel


def _build_azure_blob_service_client(ctx: StorageContextLike):
    from azure.storage.blob import BlobServiceClient
    from azure.identity import ClientSecretCredential

    info = connection_info(ctx, allow_cached=True)
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


def _iter_azure_paths(ctx: StorageContextLike, *, relative_prefix: str, suffix: str | None) -> Iterator[str]:
    client = _build_azure_blob_service_client(ctx)
    prefix = _physical_prefix(folder_root=ctx.folder_root, relative_prefix=relative_prefix)
    container = ctx.bucket_or_container
    for blob in client.get_container_client(container).list_blobs(name_starts_with=prefix):
        name = str(getattr(blob, "name", "") or "")
        rel = _relative_from_physical_key(folder_root=ctx.folder_root, object_key=name)
        if rel is None or rel.endswith("/") or not _matches_suffix(rel, suffix):
            continue
        yield rel


def _build_gcs_environment_client(ctx: StorageContextLike):
    from google.cloud import storage
    from google.auth import default as google_auth_default

    info = connection_info(ctx, allow_cached=True)
    params = info.get("params") or {}
    credentials_mode = params.get("credentialsMode") or params.get("authType")
    if credentials_mode == "ENVIRONMENT":
        credentials, project_id = google_auth_default()
        return storage.Client(project=project_id, credentials=credentials)

    raise RuntimeError(
        f"Unsupported GCS credential mode for managed-folder discovery: {credentials_mode or 'filesystem'}"
    )


def _build_gcs_hmac_client(ctx: StorageContextLike):
    import gcsfs

    resolved = resolve_gcs_hmac_credentials(ctx)
    if not resolved:
        raise RuntimeError("Unsupported GCS credential mode for managed-folder discovery: missing_hmac")
    access_key, hmac_secret = resolved
    return gcsfs.GCSFileSystem(access=access_key, secret=hmac_secret)


def _build_gcs_client(ctx: StorageContextLike):
    info = connection_info(ctx, allow_cached=True)
    params = info.get("params") or {}
    credentials_mode = params.get("credentialsMode") or params.get("authType")
    if credentials_mode == "ENVIRONMENT":
        return ("google-cloud-storage", _build_gcs_environment_client(ctx))
    if credentials_mode in {"HMAC", "INTEROP"} or resolve_gcs_hmac_credentials(ctx):
        return ("gcsfs", _build_gcs_hmac_client(ctx))

    raise RuntimeError(
        f"Unsupported GCS credential mode for managed-folder discovery: {credentials_mode or 'filesystem'}"
    )


def _iter_gcs_paths(ctx: StorageContextLike, *, relative_prefix: str, suffix: str | None) -> Iterator[str]:
    backend, client = _build_gcs_client(ctx)
    prefix = _physical_prefix(folder_root=ctx.folder_root, relative_prefix=relative_prefix)
    bucket = ctx.bucket_or_container
    if backend == "gcsfs":
        for item in client.find(f"{bucket}/{prefix}", detail=True).values():
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
    storage_ctx: StorageContextLike,
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
