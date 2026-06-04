from __future__ import annotations

import logging
from typing import Any

import dataiku
from dataiku.runnables import Runnable

logger = logging.getLogger(__name__)


class LoadEventServerHistoryRunnable(Runnable):
    def __init__(self, project_key: str, config: dict[str, Any], plugin_config: dict[str, Any]):
        self.project_key = project_key
        self.config = config
        self.plugin_config = plugin_config

    def get_progress_target(self):
        return None

    def _require_string(self, key: str, label: str) -> str:
        value = str(self.config.get(key) or "").strip()
        if not value:
            raise ValueError(f"Missing required parameter: {label}")
        return value

    def _open_managed_folder(self, folder_id: str):
        try:
            folder = dataiku.Folder(
                lookup=folder_id,
                project_key=self.project_key,
                ignore_flow=True,
            )
            resolved_folder_id = folder.get_id()
        except Exception as exc:
            raise ValueError(
                f"Managed folder {folder_id!r} could not be resolved in project {self.project_key!r}. "
                "Check that the folder id is correct and that you have access to it."
            ) from exc

        try:
            partitions = folder.list_partitions() or []
        except Exception as exc:
            raise ValueError(
                f"Managed folder {resolved_folder_id!r} was resolved but could not be listed. "
                "Check managed folder permissions and accessibility."
            ) from exc

        return folder, resolved_folder_id, partitions

    @staticmethod
    def _extract_available_nodes(partitions: list[str]) -> list[str]:
        available_nodes = sorted(
            {
                partition.split("|", 1)[0].strip()
                for partition in partitions
                if partition and partition.split("|", 1)[0].strip()
            }
        )
        return available_nodes

    def run(self, progress_callback):
        folder_id = self._require_string("folder_id", "Managed Folder ID")
        node_id = self._require_string("node_id", "Event Server Node ID")
        instance_name = self._require_string("instance_name", "Instance Name Mapping")

        logger.info(
            "Checking event-server history folder accessibility for folder_id=%s node_id=%s instance_name=%s",
            folder_id,
            node_id,
            instance_name,
        )

        folder, resolved_folder_id, partitions = self._open_managed_folder(folder_id)
        available_nodes = self._extract_available_nodes(partitions)
        node_found = node_id in available_nodes

        if node_found:
            status = "found"
            message = f"Event-server node {node_id!r} is available in managed folder {resolved_folder_id!r}."
        else:
            status = "missing"
            message = f"Event-server node {node_id!r} was not found in managed folder {resolved_folder_id!r}."

        return {
            "rows": [
                {
                    "status": status,
                    "folder_id": folder_id,
                    "resolved_folder_id": resolved_folder_id,
                    "instance_name": instance_name,
                    "requested_node_id": node_id,
                    "node_found": node_found,
                    "available_node_count": len(available_nodes),
                    "available_nodes": ", ".join(available_nodes),
                    "partition_count": len(partitions),
                    "message": message,
                }
            ]
        }
