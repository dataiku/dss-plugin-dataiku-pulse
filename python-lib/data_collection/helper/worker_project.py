from __future__ import annotations

from typing import Any


def resolve_worker_project_key(client: Any, fallback_project_key: str | None = None) -> str:
    """Return the project key that should own local worker-side cursors.

    In DSS macro runs, this should be the project the macro is executed in.
    Dataiku's Python API exposes that as `client.get_default_project()`.

    If it cannot be resolved (eg. local/unit tests), fall back to the runnable's
    `project_key`.
    """

    try:
        project = client.get_default_project()
        # dataikuapi.dss.project.DSSProject has project_key attribute
        project_key = getattr(project, "project_key", None)
        if project_key:
            return str(project_key)
    except Exception:
        pass

    if fallback_project_key:
        return fallback_project_key

    raise ValueError("Could not resolve worker project key")
