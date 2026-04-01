from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from data_collection.helper.dss_folder_writer import DSSFolderTarget, ensure_managed_folder


@dataclass(frozen=True)
class PulseOutputTarget:
    project_key: str
    folder_lookup: str
    connection_name: str | None


def resolve_output_target(param_set: dict[str, Any]) -> PulseOutputTarget:
    """Resolve output target configuration from `pulse_primary` settings."""

    return PulseOutputTarget(
        project_key=str(param_set.get("pulse_project_key") or "DATA_COLLECTION"),
        folder_lookup=str(param_set.get("pulse_partitioned_data") or "partitioned_data"),
        connection_name=param_set.get("pulse_folder_connection"),
    )


def ensure_output_folder(*, param_set: dict[str, Any], remote_client: Any) -> DSSFolderTarget:
    """Ensure the partitioned output folder exists and return a `DSSFolderTarget`.

    All uploads should go through `remote_client` (hub/spoke or same-instance).
    """

    ot = resolve_output_target(param_set)

    target = DSSFolderTarget(
        project_key=ot.project_key,
        folder_lookup=ot.folder_lookup,
        connection_name=ot.connection_name,
        client=remote_client,
    )

    ensure_managed_folder(
        project_key=target.project_key,
        folder_lookup=target.folder_lookup,
        connection_name=target.connection_name,
        client=remote_client,
    )

    return target
