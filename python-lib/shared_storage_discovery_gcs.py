from __future__ import annotations

from typing import Any, Iterator, Protocol

from shared_storage_credentials import connection_info, resolve_gcs_hmac_credentials


class StorageContextLike(Protocol):
    bucket_or_container: str | None
    project_handle: Any
    connection_handle: Any
    connection_name: str
    cached_connection_info: dict[str, Any]


def _build_gcs_environment_client(ctx: StorageContextLike):
    from google.auth import default as google_auth_default
    from google.cloud import storage

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


def iter_gcs_object_names(ctx: StorageContextLike, *, physical_prefix: str) -> Iterator[str]:
    backend, client = _build_gcs_client(ctx)
    bucket = ctx.bucket_or_container
    if backend == "gcsfs":
        for item in client.find(f"{bucket}/{physical_prefix}", detail=True).values():
            name = str((item or {}).get("name") or "")
            key = name.split("/", 1)[1] if "/" in name else name
            if key:
                yield key
        return

    for blob in client.list_blobs(bucket, prefix=physical_prefix):
        name = str(getattr(blob, "name", "") or "")
        if name:
            yield name


__all__ = ["iter_gcs_object_names"]
