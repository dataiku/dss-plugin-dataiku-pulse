from __future__ import annotations

import logging
from typing import Any

import dataiku
import pandas as pd
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

    def _open_managed_folder(self, folder_id: str) -> tuple[dataiku.Folder, str, list[str]]:
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
                f"Managed folder {resolved_folder_id!r} was resolved but could not list partitions. "
                "Check managed folder permissions and accessibility."
            ) from exc

        return folder, resolved_folder_id, partitions

    @staticmethod
    def _build_partition_frame(partitions: list[str]) -> pd.DataFrame:
        if not partitions:
            return pd.DataFrame(columns=["node_id", "partition"])

        partition_df = pd.DataFrame({"partition": partitions})
        split_columns = partition_df["partition"].str.split("|", expand=True)
        split_columns = split_columns.rename(columns={0: "node_id"})
        partition_df = pd.concat([partition_df, split_columns], axis=1)
        partition_df["node_id"] = partition_df["node_id"].fillna("").astype(str).str.strip()
        partition_df = partition_df[partition_df["node_id"] != ""].copy()

        return partition_df

    @staticmethod
    def _list_paths_for_partitions(folder: dataiku.Folder, partitions: list[str]) -> list[str]:
        paths: list[str] = []
        for partition in partitions:
            for path in folder.list_paths_in_partition(partition) or []:
                paths.append(str(path))
        return sorted(set(paths))

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
        partition_df = self._build_partition_frame(partitions)

        if partition_df.empty:
            raise ValueError(
                f"Managed folder {resolved_folder_id!r} does not expose any discoverable event-server partitions."
            )

        available_nodes = sorted(partition_df["node_id"].drop_duplicates().tolist())
        matching_partition_df = partition_df[partition_df["node_id"] == node_id].copy()

        if matching_partition_df.empty:
            available_nodes_text = ", ".join(available_nodes) or "<none>"
            raise ValueError(
                f"Event-server node {node_id!r} was not found in managed folder {resolved_folder_id!r}. "
                f"Available nodes: {available_nodes_text}"
            )

        selected_partitions = matching_partition_df["partition"].drop_duplicates().tolist()

        try:
            matching_paths = self._list_paths_for_partitions(folder, selected_partitions)
        except Exception as exc:
            raise ValueError(
                f"Managed folder {resolved_folder_id!r} matched node {node_id!r} but could not list files for its partitions."
            ) from exc

        if not matching_paths:
            raise ValueError(
                f"Managed folder {resolved_folder_id!r} matched node {node_id!r} but no files were found in its partitions."
            )

        return {
            "rows": [
                {
                    "status": "found",
                    "folder_id": folder_id,
                    "resolved_folder_id": resolved_folder_id,
                    "instance_name": instance_name,
                    "requested_node_id": node_id,
                    "node_found": True,
                    "available_node_count": len(available_nodes),
                    "available_nodes": ", ".join(available_nodes),
                    "partition_count": len(partitions),
                    "matched_partition_count": len(selected_partitions),
                    "matched_partitions": ", ".join(selected_partitions),
                    "matched_path_count": len(matching_paths),
                    "message": (
                        f"Event-server node {node_id!r} is available in managed folder {resolved_folder_id!r} "
                        f"with {len(selected_partitions)} matching partitions and {len(matching_paths)} discovered paths."
                    ),
                }
            ]
        }
