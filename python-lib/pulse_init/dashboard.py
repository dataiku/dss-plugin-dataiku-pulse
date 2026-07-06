from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import dataiku

from .notifications import (
    build_failure_reporter,
    ensure_failure_reporter,
    resolve_email_channel_id,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InitStep:
    step: str
    status: str
    message: str | None = None


def _project_owner_login(project: Any) -> str | None:
    """Best-effort project owner login.

    We want the scenario to run as the project owner (not necessarily admin).

    Dataiku's API response keys vary slightly across versions, so we probe a few
    known shapes.
    """

    try:
        summary = project.get_summary() or {}
    except Exception:
        return None

    owner = summary.get("owner")
    if isinstance(owner, dict):
        login = owner.get("login") or owner.get("user")
        if login:
            return str(login)

    for key in ("ownerLogin", "owner_login", "projectOwnerLogin"):
        if summary.get(key):
            return str(summary[key])

    return None


def _folder_id_by_name(project: Any, name: str) -> str | None:
    try:
        for f in project.list_managed_folders() or []:
            if f.get("name") == name and f.get("id"):
                return str(f["id"])
    except Exception:
        return None
    return None


def ensure_managed_folder(
    project: Any,
    *,
    folder_name: str,
    connection_name: str | None,
    project_key: str,
) -> tuple[str, str, str | None]:
    """Ensure a managed folder exists and return (folder_id, status, message)."""

    existing_id = _folder_id_by_name(project, folder_name)
    if existing_id:
        return existing_id, "already_exists", None

    try:
        folder = project.create_managed_folder(
            name=folder_name,
            connection_name=connection_name or "filesystem_folders",
        )
        folder_id = str(folder.id)
        # Also create a local handle for consistency. This makes sure the folder
        # can be resolved by name immediately.
        _ = dataiku.Folder(folder_name, project_key=project_key, ignore_flow=True)
        return folder_id, "created", None
    except Exception as e:
        return "", "error", repr(e)


def ensure_custom_recipe(
    project: Any,
    *,
    recipe_name: str,
    recipe_type: str,
    output_role: str,
    output_ref: str,
) -> InitStep:
    """Create a custom recipe if missing.

    Important: if the recipe already exists, do not modify it.
    """

    try:
        recipe = project.get_recipe(recipe_name=recipe_name)
        recipe.get_settings()
        return InitStep(step=f"recipe:{recipe_name}", status="already_exists")
    except Exception:
        pass

    try:
        recipe = project.create_recipe(
            recipe_proto={"type": recipe_type, "name": recipe_name},
            creation_settings={},
        )
        settings = recipe.get_settings()
        settings.add_output(role=output_role, ref=output_ref)
        settings.save()
        return InitStep(step=f"recipe:{recipe_name}", status="created")
    except Exception as e:
        return InitStep(step=f"recipe:{recipe_name}", status="error", message=repr(e))


def ensure_scenario_gold_refresh(
    project: Any,
    *,
    scenario_name: str,
    gold_folder_id: str,
    run_as_login: str | None,
) -> InitStep:
    """Create the GOLD refresh scenario if missing.

    Important: if the scenario already exists, do not modify it.
    """

    try:
        for s in project.list_scenarios() or []:
            if s.get("name") == scenario_name:
                return InitStep(
                    step=f"scenario:{scenario_name}", status="already_exists"
                )
    except Exception:
        # If listing scenarios fails, fall through to creation attempt.
        pass

    try:
        scenario = project.create_scenario(
            scenario_name=scenario_name, type="step_based"
        )
        settings = scenario.get_settings()

        raw = settings.get_raw()
        if run_as_login:
            raw["runAsUser"] = run_as_login

        raw["active"] = True

        # Create a daily trigger at 00:00 server time.
        del settings.raw_triggers[:]
        settings.add_daily_trigger(hour=0, minute=0, repeat_every=1, timezone="SERVER")

        # Build the GOLD folder (recursive forced build).
        del settings.raw_steps[:]
        settings.raw_steps.append(
            {
                "type": "build_flowitem",
                "name": "build_gold_data",
                "enabled": True,
                "alwaysShowComment": False,
                "runConditionType": "RUN_IF_STATUS_MATCH",
                "runConditionStatuses": ["SUCCESS", "WARNING"],
                "runConditionExpression": "",
                "resetScenarioStatus": False,
                "delayBetweenRetries": 10,
                "maxRetriesOnFail": 0,
                "params": {
                    "builds": [
                        {
                            "type": "MANAGED_FOLDER",
                            "itemId": gold_folder_id,
                            "partitionsSpec": "",
                        }
                    ],
                    "jobType": "RECURSIVE_FORCED_BUILD",
                    "autoUpdateSchemaBeforeEachRecipeRun": False,
                    "stopAtFlowZoneBoundary": False,
                    "refreshHiveMetastore": True,
                    "handleWarningsAs": "WARNING",
                    "proceedOnFailure": False,
                },
            }
        )

        settings.save()

        status = "created" if run_as_login else "created_with_warning"
        message = (
            None
            if run_as_login
            else "Could not resolve project owner login; scenario runAsUser left unset"
        )
        return InitStep(
            step=f"scenario:{scenario_name}", status=status, message=message
        )

    except Exception as e:
        return InitStep(
            step=f"scenario:{scenario_name}", status="error", message=repr(e)
        )


def ensure_scenario_failure_reporter(
    project: Any,
    *,
    scenario_name: str,
    reporter: dict | None,
    remove: bool = False,
) -> InitStep:
    """Upsert (or remove) ONLY the Pulse failure reporter on an existing scenario.

    `ensure_scenario_gold_refresh` is create-once-never-modify, so the reporter
    is retrofitted separately: triggers, steps and user-added reporters are
    never touched. No-op with a skip when the scenario does not exist.
    """

    step_name = f"scenario:{scenario_name}:reporter"

    try:
        scenario_id = None
        for s in project.list_scenarios() or []:
            if s.get("name") == scenario_name:
                scenario_id = s.get("id")
                break
        if not scenario_id:
            return InitStep(
                step=step_name,
                status="skipped",
                message=f"scenario {scenario_name!r} not found",
            )

        scenario = project.get_scenario(scenario_id)
        settings = scenario.get_settings()
        outcome = ensure_failure_reporter(
            settings, reporter=(None if remove else reporter)
        )
        if outcome != "unchanged":
            settings.save()
        return InitStep(step=step_name, status=outcome)
    except Exception as e:
        return InitStep(step=step_name, status="error", message=repr(e))


def initialize_dashboard(
    *,
    project_key: str,
    connection_name: str | None,
    gold_folder_name: str = "gold_data",
    recipe_name: str = "create_gold_tables",
    scenario_name: str = "gold_data_refresh",
    notification_email: str | None = None,
    notification_channel_id: str | None = None,
    instance_url: str | None = None,
) -> list[InitStep]:
    """Initialize the hub (dashboard) project.

    This is intended to be safe to run multiple times:
    - it only creates missing objects
    - it does not delete or modify existing objects
    """

    client = dataiku.api_client()
    project = client.get_project(project_key)

    steps: list[InitStep] = []

    # Managed folders
    partitioned_id, status, msg = ensure_managed_folder(
        project,
        folder_name="partitioned_data",
        connection_name=connection_name,
        project_key=project_key,
    )
    steps.append(InitStep(step="folder:partitioned_data", status=status, message=msg))

    gold_id, status, msg = ensure_managed_folder(
        project,
        folder_name=gold_folder_name,
        connection_name=connection_name,
        project_key=project_key,
    )
    steps.append(
        InitStep(step=f"folder:{gold_folder_name}", status=status, message=msg)
    )

    # Recipe
    if gold_id:
        steps.append(
            ensure_custom_recipe(
                project,
                recipe_name=recipe_name,
                recipe_type="CustomCode_create-gold-tables",
                output_role="gold_tables_folder",
                output_ref=gold_id,
            )
        )
    else:
        steps.append(
            InitStep(
                step=f"recipe:{recipe_name}",
                status="skipped",
                message="gold folder missing; recipe not created",
            )
        )

    # Scenario
    run_as_login = _project_owner_login(project)
    if gold_id:
        steps.append(
            ensure_scenario_gold_refresh(
                project,
                scenario_name=scenario_name,
                gold_folder_id=gold_id,
                run_as_login=run_as_login,
            )
        )
    else:
        steps.append(
            InitStep(
                step=f"scenario:{scenario_name}",
                status="skipped",
                message="gold folder missing; scenario not created",
            )
        )

    # Failure notification reporter (retrofit onto the existing scenario).
    email = str(notification_email or "").strip()
    if not email:
        steps.append(
            ensure_scenario_failure_reporter(
                project, scenario_name=scenario_name, reporter=None, remove=True
            )
        )
        steps.append(
            InitStep(
                step="hub:notifications",
                status="skipped",
                message="notification_email not set; Pulse reporters are removed",
            )
        )
        return steps

    channel_id, channel_kind, skip_reason = resolve_email_channel_id(
        client, preferred_channel_id=(str(notification_channel_id or "").strip() or None)
    )
    if channel_id is None:
        steps.append(
            InitStep(step="hub:notifications", status="skipped", message=skip_reason)
        )
        return steps

    label = str(instance_url or "").strip()
    for prefix in ("https://", "http://"):
        if label.startswith(prefix):
            label = label[len(prefix):]
    label = label.rstrip("/") or "hub"

    reporter, build_skip_reason = build_failure_reporter(
        channel_id=channel_id,
        channel_type=channel_kind or "",
        recipient=email,
        scenario_name=scenario_name,
        project_key=project_key,
        instance_label=label,
        instance_url=instance_url,
    )
    if reporter is None:
        steps.append(
            InitStep(
                step="hub:notifications", status="skipped", message=build_skip_reason
            )
        )
        return steps

    steps.append(
        InitStep(step="hub:notifications", status="ok", message=f"channel={channel_id}")
    )
    steps.append(
        ensure_scenario_failure_reporter(
            project, scenario_name=scenario_name, reporter=reporter
        )
    )

    return steps
