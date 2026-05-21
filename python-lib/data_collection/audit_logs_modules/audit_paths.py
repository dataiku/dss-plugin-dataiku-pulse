from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _resolve_cached_audit_dir(repo_root: Path) -> Path:
    candidates = [
        repo_root.parent / "audit_data",
        repo_root.parent / "dataiku-pulse.extras" / "audit_data",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve()


def chdir_audit_logs(
    *,
    client: Any,
    plugin_config: dict,
    repo_root: Path,
) -> Path:
    """Change working directory to where audit logs are located.

    - If `PULSE_AUDIT_LOGS_USE_CACHED` env var is truthy, uses the
      static test logs under `{repo_root.parent}/audit_data/`.
    - Otherwise, uses the DSS install data dir path from the API and navigates
      to `<DATA_DIR>/run/audit/`.

    Returns the resolved audit log directory path.
    """

    use_cached = os.environ.get("PULSE_AUDIT_LOGS_USE_CACHED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }

    if use_cached:
        audit_dir = _resolve_cached_audit_dir(repo_root)
        os.chdir(audit_dir)
        return audit_dir

    root_path = client.get_instance_info().raw["dataDirPath"]
    audit_dir = Path(root_path) / "run" / "audit"
    os.chdir(audit_dir)
    return audit_dir


def resolve_audit_logs_dir(
    *,
    client: Any,
    repo_root: Path,
) -> Path:
    """Resolve the audit log directory without mutating process CWD.

    - If `PULSE_AUDIT_LOGS_USE_CACHED` is truthy, uses the static test logs
      under `{repo_root.parent}/audit_data/`.
    - Otherwise, uses the DSS install data dir path and returns
      `<DATA_DIR>/run/audit/`.
    """

    use_cached = os.environ.get("PULSE_AUDIT_LOGS_USE_CACHED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }

    if use_cached:
        return _resolve_cached_audit_dir(repo_root)

    root_path = client.get_instance_info().raw["dataDirPath"]
    return (Path(root_path) / "run" / "audit").resolve()
