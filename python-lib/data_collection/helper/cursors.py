from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Mapping

import pandas as pd


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CursorSpec:
    """Specification for a cursor stored in DSS project variables."""

    variable_name: str
    # Optional boolean key in `pulse_primary` that forces using the default.
    debug_key: str | None = None


def _read_project_var_ts(client: Any, *, project_key: str, var_name: str) -> pd.Timestamp | None:
    try:
        project = client.get_project(project_key)
        variables = project.get_variables()
        raw = (variables.get("local") or {}).get(var_name)
        if not raw:
            return None
        dt = pd.to_datetime(raw, utc=True, errors="coerce")
        if pd.isna(dt):
            return None
        return dt
    except Exception:
        logger.debug(
            "Failed reading project variable timestamp %s for project %s",
            var_name,
            project_key,
            exc_info=True,
        )
        return None


def _write_project_var(client: Any, *, project_key: str, var_name: str, value: str) -> None:
    try:
        project = client.get_project(project_key)
        variables = project.get_variables()
        local = variables.get("local") or {}
        local[var_name] = value
        variables["local"] = local
        project.set_variables(variables)
        logger.info(
            "Updated project variable %s for project %s to %s",
            var_name,
            project_key,
            value,
        )
    except Exception:
        logger.warning(
            "Failed writing project variable %s for project %s to %s",
            var_name,
            project_key,
            value,
            exc_info=True,
        )
        return


def resolve_cursor_ts(
    *,
    client: Any,
    project_key: str,
    param_set: Mapping[str, Any],
    spec: CursorSpec,
    default_ts: pd.Timestamp,
    local_mode: bool = False,
) -> pd.Timestamp:
    """Resolve the cursor timestamp used to filter payloads.

    Precedence:
    - If `local_mode` is true, always return `default_ts`.
    - If `spec.debug_key` is set and `param_set[debug_key]` is truthy, return `default_ts`.
    - Else try to read `spec.variable_name` from local project variables.
    - Fallback: `default_ts`
    """

    if local_mode:
        return default_ts

    if spec.debug_key and bool(param_set.get(spec.debug_key, False)):
        return default_ts

    var_dt = _read_project_var_ts(client, project_key=project_key, var_name=spec.variable_name)
    return var_dt or default_ts


def update_cursor_ts(
    *,
    client: Any,
    project_key: str,
    spec: CursorSpec,
    value: str,
    enabled: bool = True,
) -> None:
    """Best-effort update of the cursor project variable."""

    if not enabled:
        return
    _write_project_var(client, project_key=project_key, var_name=spec.variable_name, value=value)
