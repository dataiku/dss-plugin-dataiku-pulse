from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def chdir_audit_logs(
    *,
    client: Any,
    plugin_config: dict,
    repo_root: Path,
) -> Path:
    """Change working directory to where audit logs are located.

    - If `plugin_config.get("pulse_audit_logs_debug", False)` is True, uses the
      static test logs under `{repo_root.parent}/audit_data/`.
    - Otherwise, uses the DSS install data dir path from the API and navigates
      to `<DATA_DIR>/run/audit/`.

    Returns the resolved audit log directory path.
    """

    if bool(plugin_config.get("pulse_audit_logs_debug", False)):
        audit_dir = (repo_root.parent / "audit_data").resolve()
        os.chdir(audit_dir)
        return audit_dir

    root_path = client.get_instance_info().raw["dataDirPath"]
    audit_dir = Path(root_path) / "run" / "audit"
    os.chdir(audit_dir)
    return audit_dir
