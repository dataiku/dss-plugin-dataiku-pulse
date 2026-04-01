from __future__ import annotations

import gzip
import io
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

import dataiku
from dataikuapi.dssclient import DSSClient

logger = logging.getLogger(__name__)

from .output_layout import as_posix_relative


@dataclass(frozen=True)
class DSSFolderTarget:
    project_key: str
    folder_lookup: str = "partitioned_data"
    connection_name: Optional[str] = None
    # Optional hub/spoke remote upload target.
    host: Optional[str] = None
    api_key: Optional[str] = None
    # Optional pre-initialized DSSClient to reuse.
    client: Any | None = None


def _json_default(o: Any) -> Any:
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.integer, np.floating)):
        return o.item()
    if isinstance(o, (pd.Timestamp, datetime, date)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    return str(o)


def _ensure_partitioning_settings(folder_handle: Any, *, folder_lookup: str) -> None:
    """Best-effort partitioning enforcement for the partitioned_data folder."""

    if folder_lookup != "partitioned_data":
        return

    try:
        settings = folder_handle.get_settings()
        settings.remove_partitioning()
        settings.add_discrete_partitioning_dimension("layer")
        settings.add_discrete_partitioning_dimension("category")
        settings.add_discrete_partitioning_dimension("module")
        settings.add_discrete_partitioning_dimension("instance_name")
        settings.set_partitioning_file_pattern(
            "%{layer}/%{category}/%{module}/%{instance_name}/.*"
        )
        settings.save()
    except Exception:
        # Partitioning is best-effort; avoid blocking uploads.
        return


def _get_or_create_local_folder(target: DSSFolderTarget) -> dataiku.Folder:
    """Return a local managed folder handle, creating it if missing."""

    folder = dataiku.Folder(
        lookup=target.folder_lookup,
        project_key=target.project_key,
        ignore_flow=True,
    )

    try:
        folder.get_id()
        return folder
    except Exception:
        pass

    # Create the managed folder in the target project.
    # In parallel runs, multiple workers may race here; tolerate "already exists".
    client = dataiku.api_client()
    project = client.get_project(target.project_key)

    connection_name = target.connection_name or "filesystem_folders"

    try:
        project.create_managed_folder(
            name=target.folder_lookup,
            connection_name=connection_name,
        )
    except Exception:
        pass

    try:
        fid = None
        for f in project.list_managed_folders():
            if f.get("name") == target.folder_lookup:
                fid = f.get("id")
                break
        if fid:
            folder_handle = project.get_managed_folder(fid)
            _ensure_partitioning_settings(folder_handle, folder_lookup=target.folder_lookup)
    except Exception:
        pass

    folder = dataiku.Folder(
        lookup=target.folder_lookup,
        project_key=target.project_key,
        ignore_flow=True,
    )
    folder.get_id()
    return folder


def _get_or_create_remote_folder(target: DSSFolderTarget):
    """Return a remote managed folder handle, creating it if missing."""

    remote_client = target.client

    if remote_client is None:
        if not target.host:
            raise ValueError("Remote folder requested but target.host is missing")
        if not target.api_key:
            raise ValueError("Remote folder requested but target.api_key is missing")
        remote_client = DSSClient(target.host, api_key=target.api_key)

    project = remote_client.get_project(target.project_key)

    def _resolve_folder_id() -> Optional[str]:
        for f in project.list_managed_folders():
            if f.get("name") == target.folder_lookup:
                return f.get("id")
        return None

    fid = _resolve_folder_id()

    if not fid:
        connection_name = target.connection_name or "filesystem_folders"
        try:
            project.create_managed_folder(
                name=target.folder_lookup,
                connection_name=connection_name,
            )
        except Exception:
            pass
        fid = _resolve_folder_id()

    if not fid:
        raise ValueError(f"Could not resolve remote managed folder id for {target.folder_lookup!r}")

    folder_handle = project.get_managed_folder(fid)
    _ensure_partitioning_settings(folder_handle, folder_lookup=target.folder_lookup)
    return folder_handle


def _get_or_create_folder(target: DSSFolderTarget):
    """Return the managed folder handle, local or remote.

    Remote mode is enabled when:
    - an explicit `target.client` is provided, OR
    - BOTH `target.host` and `target.api_key` are set

    This prevents accidental 401s in DSS-native macro runs when hub/spoke settings
    are partially configured.
    """

    if target.client is not None:
        return _get_or_create_remote_folder(target)

    if target.host and target.api_key:
        return _get_or_create_remote_folder(target)

    if target.host and not target.api_key:
        logger.warning(
            "Ignoring remote host because api_key is missing (host=%s project=%s folder=%s)",
            target.host,
            target.project_key,
            target.folder_lookup,
        )

    return _get_or_create_local_folder(target)



def ensure_managed_folder(
    *,
    project_key: str,
    folder_lookup: str,
    connection_name: Optional[str] = None,
    host: Optional[str] = None,
    api_key: Optional[str] = None,
    client: Any | None = None,
):
    """Ensure a managed folder exists and return its handle.

    - If the folder does not exist, it is created.
    - If `folder_lookup == 'partitioned_data'`, partitioning is applied
      (best-effort) to match the project convention.

    This is the single shared entrypoint that recipes/runnables should call
    before writing outputs.
    """

    return _get_or_create_folder(
        DSSFolderTarget(
            project_key=project_key,
            folder_lookup=folder_lookup,
            connection_name=connection_name,
            host=host,
            api_key=api_key,
            client=client,
        )
    )


def upload_bytes(
    *,
    target: DSSFolderTarget,
    output_path: Path,
    output_base_dir: Path,
    content: bytes,
) -> None:
    folder = _get_or_create_folder(target)
    rel_path = as_posix_relative(output_path, base_dir=output_base_dir)

    # Local in-instance handle: `dataiku.Folder`
    if hasattr(folder, "upload_stream"):
        folder.upload_stream(rel_path, content)
        return

    # Remote handle: `dataikuapi.dss.managedfolder.DSSManagedFolder`
    if hasattr(folder, "put_file"):
        remote_path = rel_path if rel_path.startswith("/") else f"/{rel_path}"
        folder.put_file(remote_path, io.BytesIO(content))
        return

    raise TypeError(f"Unsupported folder handle type: {type(folder)!r}")


def upload_json(
    *,
    target: DSSFolderTarget,
    output_path: Path,
    output_base_dir: Path,
    payload: Any,
    encoding: str = "utf-8",
    indent: Optional[int] = None,
) -> None:
    data = json.dumps(payload, ensure_ascii=False, default=_json_default, indent=indent)
    upload_bytes(
        target=target,
        output_path=output_path,
        output_base_dir=output_base_dir,
        content=data.encode(encoding),
    )


def upload_json_gzip(
    *,
    target: DSSFolderTarget,
    output_path: Path,
    output_base_dir: Path,
    payload: Any,
    encoding: str = "utf-8",
    indent: Optional[int] = None,
) -> None:
    data = json.dumps(payload, ensure_ascii=False, default=_json_default, indent=indent)
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(data.encode(encoding))
    upload_bytes(
        target=target,
        output_path=output_path,
        output_base_dir=output_base_dir,
        content=buf.getvalue(),
    )


def upload_parquet(
    *,
    target: DSSFolderTarget,
    output_path: Path,
    output_base_dir: Path,
    df: pd.DataFrame,
    compression: str = "snappy",
    write_empty: bool = False,
) -> bool:
    if df.shape[0] == 0 and not write_empty:
        return False

    # Import here to reuse existing pyarrow path extension logic.
    from data_collection.helper.parquet_engine import ensure_pyarrow

    ensure_pyarrow()

    buf = io.BytesIO()
    df.to_parquet(buf, index=False, engine="pyarrow", compression=compression)
    upload_bytes(
        target=target,
        output_path=output_path,
        output_base_dir=output_base_dir,
        content=buf.getvalue(),
    )
    return True
