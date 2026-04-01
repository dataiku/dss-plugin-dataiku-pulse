from __future__ import annotations

from typing import Any, Dict, Optional

from dataikuapi.dssclient import DSSClient


def get_instance_info_raw(client: DSSClient) -> Dict[str, Any]:
    """Return raw instance info JSON from DSS."""

    return client.get_instance_info().raw


def get_instance_name(client: DSSClient) -> Optional[str]:
    """Return stable instance name for partitioning.

    Prefers nodeId/nodeID, falls back to installId/installID.
    """

    raw = get_instance_info_raw(client)

    node_id = raw.get("nodeId") or raw.get("nodeID")
    install_id = raw.get("installId") or raw.get("installID")

    return node_id or install_id
