from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from dataikuapi.dssclient import DSSClient

from data_collection.helper import DSSFolderTarget, chunked

from .collect_project import CollectResult, collect_project_list_methods
from .instance import get_instance_name


@dataclass(frozen=True)
class CollectAllProjectsResult:
    collected_projects: List[str]
    per_project: Dict[str, CollectResult]


def list_project_keys(client: DSSClient) -> List[str]:
    """Return project keys using `list_project_keys` if available."""

    # Some versions expose list_project_keys, others only list_projects.
    if hasattr(client, "list_project_keys"):
        return list(client.list_project_keys())

    projects = client.list_projects()
    return [p["projectKey"] for p in projects if "projectKey" in p]


def collect_all_projects(
    *,
    client: DSSClient,
    output_base_dir: Path,
    project_keys: Optional[List[str]] = None,
    n_jobs: int = 1,
    batch_size: int = 25,
    output_folder_target: DSSFolderTarget = DSSFolderTarget(project_key="DATA_COLLECTION"),
) -> CollectAllProjectsResult:
    if project_keys is None:
        project_keys = list_project_keys(client)

    instance_name = get_instance_name(client)
    if not instance_name:
        raise ValueError("Could not determine instance_name (nodeId/installId)")

    run_dt = datetime.now(timezone.utc)
    run_ts = run_dt.isoformat()
    run_date = run_dt.date()

    per_project: Dict[str, CollectResult] = {}
    collected_projects: List[str] = []

    def _collect_one(key: str) -> tuple[str, CollectResult]:
        project = client.get_project(key)
        result = collect_project_list_methods(
            project=project,
            project_key=key,
            output_base_dir=output_base_dir,
            instance_name=instance_name,
            run_ts=run_ts,
            run_date=run_date,
            output_folder_target=output_folder_target,
        )
        return key, result

    if n_jobs <= 1:
        for key in project_keys:
            k, result = _collect_one(key)
            per_project[k] = result
            collected_projects.append(k)
    else:
        from joblib import Parallel, delayed

        for batch in chunked(project_keys, batch_size):
            results = Parallel(n_jobs=n_jobs, prefer="threads")(
                delayed(_collect_one)(k) for k in batch
            )
            for k, result in results:
                per_project[k] = result
                collected_projects.append(k)

    return CollectAllProjectsResult(
        collected_projects=collected_projects,
        per_project=per_project,
    )
