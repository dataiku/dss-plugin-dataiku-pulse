from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import dataiku


@dataclass(frozen=True)
class StorageContext:
    project_key: str
    folder_lookup: str
    folder_id: str
    connection_name: str
    connection_type: str
    connection_handle: Any
    project_handle: Any
    folder_handle: Any
    folder_root: str
    bucket_or_container: str | None
    blob_header: str


def build_storage_context(*, project_key: str, folder_lookup: str = "partitioned_data") -> StorageContext:
    client = dataiku.api_client()
    project = client.get_project(project_key)

    try:
        folder = dataiku.Folder(
            lookup=folder_lookup,
            project_key=project_key,
            ignore_flow=True,
        )
        folder_id = folder.get_id()
    except Exception as exc:
        raise ValueError(
            f"Managed folder {folder_lookup!r} not found in project {project_key!r} (by name or id)"
        ) from exc

    folder_handle = project.get_managed_folder(folder_id)
    settings_raw = folder_handle.get_settings().get_raw()

    connection_name = (settings_raw.get("params") or {}).get("connection")
    if not connection_name:
        connection_name = ((settings_raw.get("settings") or {}).get("params") or {}).get("connection")

    if not connection_name:
        raise ValueError("Could not resolve managed folder connection name")

    connection_handle = client.get_connection(connection_name)
    conn_info = connection_handle.get_info()
    connection_type = str(conn_info.get("type") or "")

    access_info = dataiku.Folder(
        lookup=folder_lookup,
        project_key=project_key,
        ignore_flow=True,
    ).get_info().get("accessInfo") or {}

    folder_root = str(access_info.get("root") or "").lstrip("/")

    bucket_or_container = None
    if connection_type == "EC2":
        bucket_or_container = access_info.get("bucket")
    elif connection_type == "Azure":
        bucket_or_container = access_info.get("container")
    elif connection_type == "GCS":
        bucket_or_container = access_info.get("bucket")

    if connection_type == "EC2":
        blob_header = "s3"
    elif connection_type == "Azure":
        blob_header = "az"
    elif connection_type == "GCS":
        blob_header = "gs"
    else:
        blob_header = ""

    return StorageContext(
        project_key=project_key,
        folder_lookup=folder_lookup,
        folder_id=folder_id,
        connection_name=connection_name,
        connection_type=connection_type,
        connection_handle=connection_handle,
        project_handle=project,
        folder_handle=folder_handle,
        folder_root=folder_root,
        bucket_or_container=bucket_or_container,
        blob_header=blob_header,
    )
