"""Table names the dashboard backend expects to exist.

Kept separate from full_backend.py so it is importable without Flask — the
contract validator (`data_collection.contracts`) cross-checks these names
against the gold specs and the dashboard dataset/view specs.
"""

from __future__ import annotations

# Objects checked by /api/startup/status to decide the dashboard is "ready".
# Each must have a dataset/view spec under pulse_dashboard/pulse_duckdb/datasets.
EXPECTED_STARTUP_OBJECTS: list[str] = [
    "final_build_catalog",
    "final_build_products_catalog",
    "dev_activity_capability_daily",
    "final_build_development_activity_events",
]

# Best-effort mapping from catalog object types to metadata history tables.
# These tables are expected in the GOLD outputs.
OBJECT_EXTRAS_SOURCES: dict[str, dict[str, object]] = {
    # Build → Assets
    "project": {"table": "base_projects_instance_metadata_history", "key_col": "project_key", "project_scoped": False},
    "dataset": {"table": "base_datasets_project_metadata_history", "key_col": "datasets_name", "project_scoped": True},
    "recipe": {"table": "base_recipes_project_metadata_history", "key_col": "recipes_name", "project_scoped": True},
    "scenario": {"table": "base_scenarios_project_metadata_history", "key_col": "scenarios_id", "project_scoped": True},
    # Build → Products
    "api_service": {"table": "base_api_services_project_metadata_history", "key_col": "api_services_id", "project_scoped": True},
    "agent_tool": {"table": "base_agent_tools_project_metadata_history", "key_col": "agent_tools_id", "project_scoped": True},
    "insight": {"table": "base_insights_project_metadata_history", "key_col": "insights_id", "project_scoped": True},
    "web_application": {"table": "base_webapps_project_metadata_history", "key_col": "webapps_id", "project_scoped": True},
    "dataiku_application": {"table": "base_apps_instance_metadata_history", "key_col": "apps_appid", "project_scoped": False},
}
