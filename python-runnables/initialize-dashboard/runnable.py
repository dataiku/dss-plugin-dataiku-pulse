from __future__ import annotations

from typing import Any

from dataiku.runnables import ResultTable, Runnable

from pulse_init import initialize_dashboard


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
        # Read from the canonical Pulse parameter set (`pulse_primary`).
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
            project_key=hub_project_key, connection_name=connection_name
        )

        return _steps_to_result_table(steps)
