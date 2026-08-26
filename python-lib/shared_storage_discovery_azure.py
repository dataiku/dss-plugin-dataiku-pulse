from __future__ import annotations

from typing import Any, Iterator, Protocol

from shared_storage_credentials import connection_info


class StorageContextLike(Protocol):
    bucket_or_container: str | None
    connection_handle: Any
    connection_name: str
    cached_connection_info: dict[str, Any]


def _build_azure_blob_service_client(ctx: StorageContextLike):
    from azure.identity import ClientSecretCredential
    from azure.storage.blob import BlobServiceClient

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


def iter_azure_blob_names(ctx: StorageContextLike, *, physical_prefix: str) -> Iterator[str]:
    client = _build_azure_blob_service_client(ctx)
    container = ctx.bucket_or_container
    for blob in client.get_container_client(container).list_blobs(name_starts_with=physical_prefix):
        name = str(getattr(blob, "name", "") or "")
        if name:
            yield name


__all__ = ["iter_azure_blob_names"]
