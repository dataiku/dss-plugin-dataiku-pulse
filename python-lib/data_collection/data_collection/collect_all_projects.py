from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dataikuapi.dssclient import DSSClient

from data_collection.helper import DSSFolderTarget, chunked

from .collect_project import CollectResult, collect_project_list_methods
from .instance import get_instance_name


logger = logging.getLogger(__name__)


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


def _clone_client(base: Any) -> Any:
    """Build a fresh DSSClient equivalent to `base` for per-thread use.

    A DSSClient wraps a requests.Session, which is not safe to share across
    joblib threads. Falls back to sharing `base` when no credentials are
    recoverable (previous behavior).
    """

    host = getattr(base, "host", None)
    api_key = getattr(base, "api_key", None)
    internal_ticket = getattr(base, "internal_ticket", None)
    try:
        if host and api_key:
            return DSSClient(host, api_key=api_key)
        if host and internal_ticket:
            return DSSClient(host, internal_ticket=internal_ticket)
        import dataiku

        return dataiku.api_client()
    except Exception:
        logger.warning("Could not build a per-thread DSS client; sharing the base client", exc_info=True)
        return base


def collect_all_projects(
    *,
    client: DSSClient,
    output_base_dir: Path,
    project_keys: Optional[List[str]] = None,
    since: "datetime | None" = None,
    debug_dir: Path | None = None,
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

    thread_local = threading.local()

    def _client_for_thread() -> Any:
        # requests.Session (inside DSSClient) is not thread-safe: give each
        # joblib thread its own client in parallel mode.
        if n_jobs <= 1:
            return client
        cached = getattr(thread_local, "client", None)
        if cached is None:
            cached = _clone_client(client)
            thread_local.client = cached
        return cached

    def _collect_one(key: str) -> tuple[str, CollectResult]:
        # One broken project must not abort (or silently skip) the rest of the
        # run: it lands in `per_project` with an error so the caller can clamp
        # the cursor and the project re-qualifies next run.
        try:
            project = _client_for_thread().get_project(key)
            result = collect_project_list_methods(
                project=project,
                project_key=key,
                output_base_dir=output_base_dir,
                instance_name=instance_name,
                run_ts=run_ts,
                run_date=run_date,
                since=since,
                debug_dir=debug_dir,
                output_folder_target=output_folder_target,
            )
        except Exception as exc:
            logger.exception("Project collection failed for %s", key)
            result = CollectResult(
                project_key=key,
                collected=[],
                errors={"__project__": repr(exc)},
                method_results=[],
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
