from __future__ import annotations

from typing import Any, Callable, Iterator, Protocol


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


__all__ = [
    "StorageContextLike",
    "collect_managed_folder_snapshot",
    "count_managed_folder_paths",
    "iter_managed_folder_paths",
]
