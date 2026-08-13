from __future__ import annotations

from typing import Any

import dataiku
from dataiku.runnables import ResultTable, Runnable

from pulse_init import initialize_dashboard


class _ResultRow:
    def __init__(self, *, step: str, status: str, message: str):
        self.step = step
        self.status = status
        self.message = message


def _steps_to_result_table(steps: list[Any]) -> ResultTable:
    rt = ResultTable()
    rt.add_column(1, "step", "STRING")
    rt.add_column(2, "status", "STRING")
    rt.add_column(3, "message", "STRING")

    for s in steps:
        rt.add_record(
            [
                str(getattr(s, "step", "")),
                str(getattr(s, "status", "")),
                str(getattr(s, "message", "")),
            ]
        )

    return rt


def _normalize_owner_group(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _sync_project_permission_variables(*, standard: dict[str, Any], param_set: dict[str, Any]) -> tuple[list[dict[str, str]], bool]:
    field_names = ["organization_owner", "administration_owner"]
    statuses: list[dict[str, str]] = []
    updated = False

    for field_name in field_names:
        configured_value = _normalize_owner_group(param_set.get(field_name))
        existing_value = standard.get(field_name)
        existing_normalized = None if existing_value is None else str(existing_value)

        if configured_value is None:
            statuses.append(
                {
                    "step": f"project_var:{field_name}",
                    "status": "skipped",
                    "message": f"{field_name} not configured in pulse_primary; existing project variable left unchanged",
                }
            )
            continue

        if existing_normalized == configured_value:
            statuses.append(
                {
                    "step": f"project_var:{field_name}",
                    "status": "unchanged",
                    "message": f"{field_name} already matches configured value '{configured_value}'",
                }
            )
            continue

        standard[field_name] = configured_value
        updated = True
        action = "created" if existing_value is None else "updated"
        statuses.append(
            {
                "step": f"project_var:{field_name}",
                "status": action,
                "message": f"{field_name} {action} with value '{configured_value}'",
            }
        )

    return statuses, updated


class MyRunnable(Runnable):
    """Initialize the Pulse hub (dashboard) project."""

    def __init__(
        self,
        project_key: str,
        config: dict[str, Any] | None,
        plugin_config: dict[str, Any] | None,
    ):
        self.project_key = project_key
        self.config = config or {}
        self.plugin_config = plugin_config or {}

    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        param_set = (
            (self.plugin_config.get("pulse_primary") or {})
            if isinstance(self.plugin_config, dict)
            else {}
        )

        hub_project_key = str(
            param_set.get("pulse_project_key") or "DATAIKU_PULSE_DASHBOARD"
        )
        connection_name = param_set.get("pulse_folder_connection")

        steps = initialize_dashboard(
            project_key=hub_project_key,
            connection_name=connection_name,
            notification_email=param_set.get("notification_email"),
            notification_engine=param_set.get("notification_engine"),
        )

        client = dataiku.api_client()
        project = client.get_project(hub_project_key)
        try:
            variables = project.get_variables() or {}
        except Exception as exc:
            raise RuntimeError(f"Unable to read project variables for {hub_project_key}") from exc

        standard = variables.get("standard") or {}
        if not isinstance(standard, dict):
            standard = {}

        permission_status_rows, permissions_updated = _sync_project_permission_variables(
            standard=standard,
            param_set=param_set,
        )
        for status_row in permission_status_rows:
            steps.append(_ResultRow(**status_row))

        if permissions_updated:
            variables["standard"] = standard
            try:
                project.set_variables(variables)
            except Exception as exc:
                raise RuntimeError(f"Unable to update project variables for {hub_project_key}") from exc


        return _steps_to_result_table(steps)
