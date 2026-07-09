from __future__ import annotations

import logging
import calendar
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

import dataiku
import dataikuapi

from .notifications import ensure_failure_reporter_on_settings, notification_enabled

logger = logging.getLogger(__name__)


PLUGIN_ID = "dataiku-pulse"
AUTOMATION_CLASSIFICATION = "automation"
DESIGNER_CLASSIFICATION = "designer"
WORKER_BUNDLE_RESOURCE = "dataiku_pulse_worker_bundle_skeleton_v1.zip"
WORKER_OVERRIDE_SUMMARY_FIELDS = (
    "pulse_dataiku_user",
    "ignore_certs",
    "notification_email",
    "notification_engine",
    "do_parallel",
    "cores",
)


@dataclass(frozen=True)
class InitStep:
    step: str
    status: str
    message: str | None = None


def _safe_get(d: Mapping[str, Any] | None, key: str, default: Any = None) -> Any:
    if not isinstance(d, Mapping):
        return default
    return d.get(key, default)


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)

    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "y", "on"}:
        return True
    if normalized in {"false", "0", "no", "n", "off", ""}:
        return False
    return default


def _normalize_worker_classification(value: Any) -> tuple[str, str | None]:
    raw = str(value or "").strip().lower()
    if not raw:
        return DESIGNER_CLASSIFICATION, "worker_classification missing; defaulting to designer"
    if raw in {DESIGNER_CLASSIFICATION, AUTOMATION_CLASSIFICATION}:
        return raw, None
    return (
        DESIGNER_CLASSIFICATION,
        f"worker_classification={raw!r} unsupported; defaulting to designer",
    )


def _subtract_calendar_months(ts: datetime, *, months: int) -> datetime:
    total_month = (ts.year * 12 + (ts.month - 1)) - months
    year = total_month // 12
    month = total_month % 12 + 1
    day = min(ts.day, calendar.monthrange(year, month)[1])
    return ts.replace(year=year, month=month, day=day)


def _default_worker_cursor_ts() -> str:
    return _subtract_calendar_months(
        datetime.now(timezone.utc), months=3
    ).isoformat()


def _resolve_primary_preset_name(local_client: Any) -> tuple[str, str | None]:
    """Return (preset_name, warning_message)."""

    try:
        plugin_handle = local_client.get_plugin(plugin_id=PLUGIN_ID)
        plugin_settings = plugin_handle.get_settings()
        pdi_ps = plugin_settings.get_parameter_set(
            parameter_set_name="params-dashboard-instance"
        )
        names = pdi_ps.list_preset_names() or []
        if names:
            return str(names[0]), None
        return (
            "primary",
            "No preset found in params-dashboard-instance; using 'primary'",
        )
    except Exception as e:
        return "primary", f"Could not resolve preset name; using 'primary': {e!r}"


def _build_remote_client(
    *,
    host: str,
    api_key: str,
    insecure_tls: bool,
) -> dataikuapi.DSSClient:
    if insecure_tls:
        return dataikuapi.DSSClient(host=host, api_key=api_key, insecure_tls=True)
    return dataikuapi.DSSClient(host=host, api_key=api_key)


def _plugin_exists(client: Any, plugin_id: str) -> bool:
    try:
        for p in client.list_plugins() or []:
            if p.get("id") == plugin_id:
                return True
    except Exception:
        return False
    return False


def _attach_plugin_code_env(plugin_handle: Any, plugin_id: str) -> InitStep:
    """Attach the plugin-managed Python code env in plugin settings."""

    try:
        settings = plugin_handle.get_settings()
        raw = settings.get_raw()

        code_env_settings = raw.setdefault("codeEnvSettings", {})
        python_settings = code_env_settings.setdefault("python", {})

        python_settings["envMode"] = "EXPLICIT_ENV"
        python_settings["envName"] = plugin_id

        settings.save()
        return InitStep(
            step="remote:attach_code_env",
            status="updated",
            message=plugin_id,
        )
    except Exception as exc:
        return InitStep(
            step="remote:attach_code_env",
            status="warning",
            message=repr(exc),
        )


def _resolve_worker_override_preset(
    local_client: Any,
    preset_name: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Return hub-local worker override preset config and optional warning."""

    if not preset_name:
        return None, None

    try:
        plugin_handle = local_client.get_plugin(plugin_id=PLUGIN_ID)
        plugin_settings = plugin_handle.get_settings()
        worker_ps = plugin_settings.get_parameter_set(
            parameter_set_name="params-worker-instances"
        )
        preset = worker_ps.get_preset(preset_name)
        if not preset:
            return None, f"Worker preset {preset_name!r} not found on hub"

        raw_config = getattr(preset, "plugin_config", None)
        if not isinstance(raw_config, Mapping):
            return None, f"Worker preset {preset_name!r} has no plugin config"

        return dict(raw_config), None
    except Exception as exc:
        return None, f"Could not read worker preset {preset_name!r} on hub: {exc!r}"


def _build_effective_worker_params(
    hub_params: Mapping[str, Any],
    worker: Mapping[str, Any],
    worker_override: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Merge hub dashboard config with optional hub-side worker overrides."""

    effective_params = dict(hub_params)
    if worker_override:
        effective_params.update(dict(worker_override))

    worker_hosts = list(_safe_get(effective_params, "worker_hosts", []) or [])
    effective_worker = dict(worker)
    worker_hosts = [
        effective_worker
        if str(_safe_get(existing_worker, "worker_url", ""))
        == str(_safe_get(worker, "worker_url", ""))
        else existing_worker
        for existing_worker in worker_hosts
    ]
    effective_params["worker_hosts"] = worker_hosts

    return effective_params


def _format_override_field_summary(worker_override: Mapping[str, Any] | None) -> str | None:
    """Return a compact list of hub override fields applied for a worker."""

    if not worker_override:
        return None

    override_fields = [
        field for field in WORKER_OVERRIDE_SUMMARY_FIELDS if field in worker_override
    ]
    if not override_fields:
        override_fields = sorted(str(key) for key in worker_override.keys())
    if not override_fields:
        return None
    return ", ".join(override_fields)


def _sync_plugin_from_hub(
    *,
    remote_client: Any,
    worker_params: Mapping[str, Any],
    preset_name: str,
    run_as_user: str,
    update_github: bool,
    force_skip_github: bool,
) -> list[InitStep]:
    """Install/update plugin + code env + preset config on a remote DSS."""

    steps: list[InitStep] = []

    repo_url = str(_safe_get(worker_params, "pulse_repo_url", ""))
    repo_branch = str(_safe_get(worker_params, "pulse_repo_branch", "main"))

    if not repo_url:
        return [
            InitStep(
                step="remote:plugin_sync",
                status="error",
                message="pulse_repo_url missing in worker params",
            )
        ]

    plugin_id = PLUGIN_ID
    plugin_client = remote_client

    if not force_skip_github:
        try:
            plugin_client = remote_client.get_user(run_as_user).get_client_as()
            steps.append(
                InitStep(step=f"remote:impersonate:{run_as_user}", status="ok")
            )
        except Exception as e:
            return [
                InitStep(
                    step=f"remote:impersonate:{run_as_user}",
                    status="error",
                    message=repr(e),
                )
            ]

    try:
        exists = _plugin_exists(plugin_client, plugin_id)
    except Exception as e:
        return [
            InitStep(
                step="remote:plugin_check",
                status="error",
                message=repr(e),
            )
        ]

    try:
        if force_skip_github:
            if not exists:
                return [
                    InitStep(
                        step="remote:plugin_sync",
                        status="error",
                        message="Plugin not found; please install by hand",
                    )
                ]

            steps.append(InitStep(step="remote:install_plugin", status="skipped"))
            steps.append(InitStep(step="remote:update_from_git", status="skipped"))
            steps.append(InitStep(step="remote:create_code_env", status="skipped"))
            steps.append(InitStep(step="remote:update_code_env", status="skipped"))

        elif not exists:
            fut = plugin_client.install_plugin_from_git(
                repository_url=repo_url,
                checkout=repo_branch,
                subpath=None,
            )
            res = fut.wait_for_result()
            if not res.get("success", False):
                return [
                    InitStep(
                        step="remote:install_plugin",
                        status="error",
                        message=str(res),
                    )
                ]
            steps.append(InitStep(step="remote:install_plugin", status="created"))

            plugin_handle = plugin_client.get_plugin(plugin_id=plugin_id)
            fut = plugin_handle.create_code_env()
            res = fut.wait_for_result()
            # Some DSS versions return messages under get_result
            try:
                full = fut.get_result()
            except Exception:
                full = {}
            msgs = (full or {}).get("messages") or {}
            if msgs.get("warning") or msgs.get("error") or msgs.get("fatal"):
                steps.append(
                    InitStep(
                        step="remote:create_code_env",
                        status="error",
                        message=str(msgs.get("messages") or msgs),
                    )
                )
            else:
                steps.append(InitStep(step="remote:create_code_env", status="created"))

            steps.append(_attach_plugin_code_env(plugin_handle, plugin_id))

        else:
            steps.append(
                InitStep(step="remote:install_plugin", status="already_exists")
            )

            if update_github:
                plugin_handle = plugin_client.get_plugin(plugin_id=plugin_id)
                fut = plugin_handle.update_from_git(
                    repository_url=repo_url,
                    checkout=repo_branch,
                    subpath=None,
                )
                res = fut.wait_for_result()
                if not res.get("success", False):
                    steps.append(
                        InitStep(
                            step="remote:update_from_git",
                            status="error",
                            message=str(res),
                        )
                    )
                else:
                    steps.append(
                        InitStep(step="remote:update_from_git", status="updated")
                    )

                fut = plugin_handle.update_code_env()
                _ = fut.wait_for_result()
                try:
                    full = fut.get_result()
                except Exception:
                    full = {}
                msgs = (full or {}).get("messages") or {}
                if msgs.get("warning") or msgs.get("error") or msgs.get("fatal"):
                    steps.append(
                        InitStep(
                            step="remote:update_code_env",
                            status="error",
                            message=str(msgs.get("messages") or msgs),
                        )
                    )
                else:
                    steps.append(
                        InitStep(step="remote:update_code_env", status="updated")
                    )

                steps.append(_attach_plugin_code_env(plugin_handle, plugin_id))
            else:
                steps.append(InitStep(step="remote:update_from_git", status="skipped"))
                steps.append(InitStep(step="remote:update_code_env", status="skipped"))
                plugin_handle = plugin_client.get_plugin(plugin_id=plugin_id)
                steps.append(_attach_plugin_code_env(plugin_handle, plugin_id))

        # Sync the preset config so worker scenarios reference the correct preset name.
        plugin_handle = remote_client.get_plugin(plugin_id=plugin_id)
        plugin_settings = plugin_handle.get_settings()
        pdi_ps = plugin_settings.get_parameter_set(
            parameter_set_name="params-dashboard-instance"
        )
        preset = pdi_ps.get_preset(preset_name=preset_name)
        if not preset:
            preset = pdi_ps.create_preset(preset_name=preset_name)
        preset.get_raw()["pluginConfig"] = dict(worker_params)
        pdi_ps.save()
        steps.append(
            InitStep(step=f"remote:preset_sync:{preset_name}", status="updated")
        )

    except Exception as e:
        steps.append(
            InitStep(step="remote:plugin_sync", status="error", message=repr(e))
        )

    return steps


def _get_local_bundle_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "resource"
        / WORKER_BUNDLE_RESOURCE
    )


def _read_bundle_bytes(bundle_path: Path) -> bytes:
    return bundle_path.read_bytes()


def _list_imported_bundle_ids(project: Any) -> list[str]:
    bundle_ids: list[str] = []
    try:
        imported = project.list_imported_bundles() or {}
    except Exception:
        return bundle_ids

    bundles = imported.get("bundles") or []
    for bundle in bundles:
        if isinstance(bundle, str):
            bundle_ids.append(bundle)
            continue
        if isinstance(bundle, Mapping):
            for key in ("bundleId", "bundle_id", "id"):
                value = bundle.get(key)
                if value:
                    bundle_ids.append(str(value))
                    break
    return bundle_ids


def _activate_project_bundle(project: Any, bundle_id: str) -> list[InitStep]:
    steps: list[InitStep] = []
    if not bundle_id:
        return [
            InitStep(
                step="bundle_activate",
                status="error",
                message="Could not determine imported bundle id to activate",
            )
        ]

    try:
        project.preload_bundle(bundle_id)
        steps.append(
            InitStep(step="bundle_preload", status="created", message=bundle_id)
        )
    except Exception as e:
        return [
            InitStep(
                step="bundle_preload",
                status="error",
                message=repr(e),
            )
        ]

    try:
        project.activate_bundle(bundle_id)
        steps.append(
            InitStep(step="bundle_activate", status="created", message=bundle_id)
        )
        return steps
    except Exception as e:
        return [
            InitStep(
                step="bundle_activate",
                status="error",
                message=repr(e),
            )
        ]


def _import_automation_project_bundle(
    *,
    client: Any,
    project_key: str,
) -> tuple[Any | None, list[InitStep]]:
    steps: list[InitStep] = []

    bundle_path = _get_local_bundle_path()
    if not bundle_path.exists():
        return None, [
            InitStep(
                step="project_import",
                status="error",
                message=f"Bundle resource not found: {bundle_path}",
            )
        ]

    bundle_bytes = _read_bundle_bytes(bundle_path)

    try:
        existing_project_keys = client.list_project_keys() or []
    except Exception as e:
        return None, [
            InitStep(step="project_check", status="error", message=repr(e))
        ]

    if project_key in existing_project_keys:
        try:
            project = client.get_project(project_key)
            return project, [
                InitStep(step=f"project:{project_key}", status="already_exists")
            ]
        except Exception as e:
            return None, [
                InitStep(step=f"project:{project_key}", status="error", message=repr(e))
            ]

    try:
        client.create_project_from_bundle_archive(bundle_bytes)
        steps.append(
            InitStep(step="project_import", status="created", message=project_key)
        )
    except Exception as e:
        return None, [
            InitStep(step="project_import", status="error", message=repr(e))
        ]

    try:
        project = client.get_project(project_key)
        steps.append(InitStep(step="project_attach", status="ok", message=project_key))
    except Exception as e:
        return None, steps + [
            InitStep(step="project_attach", status="error", message=repr(e))
        ]

    bundle_ids = _list_imported_bundle_ids(project)
    if not bundle_ids:
        return None, steps + [
            InitStep(
                step="bundle_lookup",
                status="error",
                message=f"No imported bundles found after project creation: {project.list_imported_bundles()!r}",
            )
        ]

    steps.append(
        InitStep(step="bundle_lookup", status="ok", message=bundle_ids[0])
    )

    activation_steps = _activate_project_bundle(project, bundle_ids[0])
    steps.extend(activation_steps)
    if any(step.status == "error" for step in activation_steps):
        return None, steps

    return project, steps


def _ensure_worker_project(
    *,
    client: Any,
    project_key: str,
    owner_login: str,
    worker_classification: str,
) -> tuple[Any | None, list[InitStep]]:
    try:
        if project_key in (client.list_project_keys() or []):
            return client.get_project(project_key), [
                InitStep(step=f"project:{project_key}", status="already_exists")
            ]

        if worker_classification == AUTOMATION_CLASSIFICATION:
            return _import_automation_project_bundle(
                client=client,
                project_key=project_key,
            )

        project = client.create_project(
            project_key=project_key, name=project_key, owner=owner_login
        )
        return project, [
            InitStep(step=f"project:{project_key}", status="created")
        ]
    except Exception as e:
        return None, [
            InitStep(step=f"project:{project_key}", status="error", message=repr(e))
        ]


def _ensure_dss_commits(project: Any) -> InitStep:
    """Ensure the `dss_commits` StatsDB dataset exists."""

    try:
        ds = project.get_dataset("dss_commits")
        if ds.exists():
            return InitStep(step="dataset:dss_commits", status="already_exists")
    except Exception:
        # fall through to creation
        pass

    try:
        ds = project.create_dataset(
            dataset_name="dss_commits",
            type="StatsDB",
            params={
                "view": "COMMITS",
                "orderByDate": False,
                "clusterTasks": {},
                "commits": {},
                "jobs": {},
                "scenarioRuns": {},
                "flowActions": {},
            },
        )
        schema = {
            "columns": [
                {"name": "project_key", "type": "string"},
                {"name": "commit_id", "type": "string"},
                {"name": "author", "type": "string"},
                {"name": "timestamp", "type": "bigint"},
                {"name": "added_files", "type": "int"},
                {"name": "added_lines", "type": "int"},
                {"name": "removed_files", "type": "int"},
                {"name": "removed_lines", "type": "int"},
                {"name": "changed_files", "type": "int"},
            ],
            "userModified": True,
        }
        ds.set_schema(schema=schema)
        return InitStep(step="dataset:dss_commits", status="created")
    except Exception as e:
        return InitStep(step="dataset:dss_commits", status="error", message=repr(e))


def _ensure_project_var_if_missing(
    project: Any,
    *,
    key: str,
    value: str,
) -> InitStep:
    try:
        vars_ = project.get_variables() or {}
        local = vars_.get("local") or {}
        if local.get(key):
            return InitStep(step=f"var:{key}", status="already_exists")
        local[key] = value
        vars_["local"] = local
        project.set_variables(vars_)
        return InitStep(step=f"var:{key}", status="created", message=value)
    except Exception as e:
        return InitStep(step=f"var:{key}", status="error", message=repr(e))


def _ensure_or_repair_scenario(
    project: Any,
    *,
    client: Any,
    name: str,
    runnable_type: str,
    preset_name: str,
    run_as_user: str,
    hour: int,
    frequency: str = "Daily",
    repeat_frequency: int = 1,
    step_config: Mapping[str, Any] | None = None,
    admin_config: Mapping[str, Any] | None = None,
    notification_email: str | None = None,
    notification_engine: str | None = None,
) -> InitStep:
    """Create or repair a step-based scenario with a runnable step."""

    try:
        existing_id = None
        for s in project.list_scenarios() or []:
            if s.get("name") == name:
                existing_id = s.get("id")
                break

        if existing_id:
            scenario = project.get_scenario(existing_id)
            status = "repaired"
        else:
            scenario = project.create_scenario(scenario_name=name, type="step_based")
            status = "created"

        settings = scenario.get_settings()
        raw = settings.get_raw()
        raw["runAsUser"] = run_as_user
        raw["active"] = True

        del settings.raw_triggers[:]
        if str(frequency).lower() == "hourly":
            settings.raw_triggers.append(
                {
                    "type": "temporal",
                    "name": "Time-based",
                    "delay": 5,
                    "active": True,
                    "params": {
                        "repeatFrequency": int(repeat_frequency),
                        "frequency": "Hourly",
                        "minute": 0,
                        "timezone": "SERVER",
                    },
                }
            )
        else:
            settings.add_daily_trigger(
                hour=hour,
                minute=0,
                repeat_every=int(repeat_frequency),
                timezone="SERVER",
            )

        del settings.raw_steps[:]
        settings.raw_steps.append(
            {
                "type": "runnable",
                "name": "run_macro",
                "enabled": True,
                "alwaysShowComment": False,
                "runConditionType": "RUN_IF_STATUS_MATCH",
                "runConditionStatuses": ["SUCCESS", "WARNING"],
                "runConditionExpression": "",
                "resetScenarioStatus": False,
                "delayBetweenRetries": 10,
                "maxRetriesOnFail": 0,
                "params": {
                    "runnableType": runnable_type,
                    "config": dict(
                        step_config
                        if step_config is not None
                        else {
                            "pulse_primary": {
                                "mode": "PRESET",
                                "name": preset_name,
                            }
                        }
                    ),
                    "adminConfig": dict(admin_config or {}),
                    "proceedOnFailure": False,
                },
            }
        )

        reporter_status = None
        reporter_message = None
        if notification_enabled(recipient=notification_email, channel_name=notification_engine):
            reporter_status, reporter_message = ensure_failure_reporter_on_settings(
                client=client,
                settings=settings,
                recipient=notification_email,
                channel_name=notification_engine,
            )

        settings.save()

        message = None
        if reporter_status == "warning" and reporter_message:
            message = reporter_message
        elif reporter_status in {"created", "updated"}:
            message = f"notification reporter {reporter_status}"
        return InitStep(step=f"scenario:{name}", status=status, message=message)

    except Exception as e:
        return InitStep(step=f"scenario:{name}", status="error", message=repr(e))


def initialize_workers(
    *,
    hub_params: Mapping[str, Any],
    update_github: bool,
    force_skip_github: bool,
    force_scenarios: bool,
) -> list[InitStep]:
    """Initialize and sync Pulse worker nodes."""

    steps: list[InitStep] = []

    local_client = dataiku.api_client()

    preset_name, preset_warn = _resolve_primary_preset_name(local_client)
    if preset_warn:
        steps.append(
            InitStep(step="hub:preset_name", status="warning", message=preset_warn)
        )
    else:
        steps.append(InitStep(step="hub:preset_name", status="ok", message=preset_name))

    hub_url = str(_safe_get(hub_params, "pulse_project_url", ""))
    worker_key = str(_safe_get(hub_params, "pulse_worker_key", "DATAIKU_PULSE_WORKER"))

    worker_hosts = _safe_get(hub_params, "worker_hosts", []) or []

    now_ts = _default_worker_cursor_ts()

    # Cursor variables are stored on the worker project.
    for worker in worker_hosts:
        worker_url = str(_safe_get(worker, "worker_url", ""))
        worker_api = _safe_get(worker, "worker_api")
        worker_classification, classification_warning = _normalize_worker_classification(
            _safe_get(worker, "worker_classification", DESIGNER_CLASSIFICATION)
        )
        worker_preset_name = str(_safe_get(worker, "preset_name", "") or "").strip()
        worker_enabled_raw = _safe_get(worker, "worker_enabled", True)
        worker_enabled = _as_bool(worker_enabled_raw, default=True)
        worker_override, worker_override_warning = _resolve_worker_override_preset(
            local_client, worker_preset_name
        )
        worker_override_summary = _format_override_field_summary(worker_override)
        effective_worker_params = _build_effective_worker_params(
            hub_params,
            worker,
            worker_override,
        )
        run_as_user = str(_safe_get(effective_worker_params, "pulse_dataiku_user", "admin"))
        ignore_certs = _as_bool(
            _safe_get(effective_worker_params, "ignore_certs", False),
            default=False,
        )
        worker_notification_email = str(
            _safe_get(effective_worker_params, "notification_email", "") or ""
        )
        worker_notification_engine = str(
            _safe_get(effective_worker_params, "notification_engine", "") or ""
        )

        if not worker_url:
            steps.append(
                InitStep(
                    step="worker:connect", status="error", message="worker_url missing"
                )
            )
            continue

        is_hub = bool(hub_url) and (worker_url == hub_url)

        if classification_warning:
            steps.append(
                InitStep(
                    step=f"worker:{worker_url}:classification",
                    status="warning",
                    message=classification_warning,
                )
            )
        else:
            steps.append(
                InitStep(
                    step=f"worker:{worker_url}:classification",
                    status="ok",
                    message=worker_classification,
                )
            )

        steps.append(
            InitStep(
                step=f"worker:{worker_url}:enabled_resolved",
                status="ok",
                message=f"raw={worker_enabled_raw!r}, resolved={worker_enabled}",
            )
        )

        if worker_override_warning:
            steps.append(
                InitStep(
                    step=f"worker:{worker_url}:preset_override",
                    status="warning",
                    message=worker_override_warning,
                )
            )
        elif worker_preset_name:
            steps.append(
                InitStep(
                    step=f"worker:{worker_url}:preset_override",
                    status="ok",
                    message=(
                        f"{worker_preset_name}: {worker_override_summary}"
                        if worker_override_summary
                        else worker_preset_name
                    ),
                )
            )

        if not worker_enabled:
            steps.append(
                InitStep(
                    step=f"worker:{worker_url}:enabled",
                    status="skipped",
                    message="worker_enabled is false",
                )
            )
            continue

        # Build client
        if is_hub:
            client = local_client
            steps.append(
                InitStep(
                    step=f"worker:{worker_url}:client", status="ok", message="local"
                )
            )
        else:
            if not worker_api:
                steps.append(
                    InitStep(
                        step=f"worker:{worker_url}:client",
                        status="error",
                        message="worker_api missing",
                    )
                )
                continue
            try:
                client = _build_remote_client(
                    host=worker_url,
                    api_key=str(worker_api),
                    insecure_tls=ignore_certs,
                )
                steps.append(InitStep(step=f"worker:{worker_url}:client", status="ok"))
            except Exception as e:
                steps.append(
                    InitStep(
                        step=f"worker:{worker_url}:client",
                        status="error",
                        message=repr(e),
                    )
                )
                continue

        # Sync plugin remotely (skip when worker is hub)
        if not is_hub:
            plugin_steps = _sync_plugin_from_hub(
                remote_client=client,
                worker_params=effective_worker_params,
                preset_name=worker_preset_name or preset_name,
                run_as_user=run_as_user,
                update_github=update_github,
                force_skip_github=force_skip_github,
            )
            steps.extend(
                InitStep(
                    step=f"worker:{worker_url}:{step.step}",
                    status=step.status,
                    message=step.message,
                )
                for step in plugin_steps
            )
            if any(step.status == "error" for step in plugin_steps):
                continue
        else:
            steps.append(
                InitStep(step=f"worker:{worker_url}:plugin_sync", status="skipped")
            )

        # Worker project
        project, project_steps = _ensure_worker_project(
            client=client,
            project_key=worker_key,
            owner_login=run_as_user,
            worker_classification=worker_classification,
        )
        steps.extend(
            InitStep(
                step=f"worker:{worker_url}:{step.step}",
                status=step.status,
                message=step.message,
            )
            for step in project_steps
        )
        if project is None:
            continue

        # Local dataset
        ds_step = _ensure_dss_commits(project)
        steps.append(
            InitStep(
                step=f"worker:{worker_url}:{ds_step.step}",
                status=ds_step.status,
                message=ds_step.message,
            )
        )

        # Cursor vars (create-only, initial value = run timestamp)
        for key in ("projects_delta", "audit_log_delta"):
            var_step = _ensure_project_var_if_missing(project, key=key, value=now_ts)
            steps.append(
                InitStep(
                    step=f"worker:{worker_url}:{var_step.step}",
                    status=var_step.status,
                    message=var_step.message,
                )
            )

        # Scenarios: instance/project daily @ 5PM server, audit hourly, cleanup daily @ 5PM.
        scenario_defs = [
            {
                "name": "data_gather_instance",
                "runnable_type": "pyrunnable_dataiku-pulse_data-gather-instance",
                "hour": 17,
                "frequency": "Daily",
            },
            {
                "name": "data_gather_project",
                "runnable_type": "pyrunnable_dataiku-pulse_data-gather-project",
                "hour": 17,
                "frequency": "Daily",
            },
            {
                "name": "data_gather_audit_logs",
                "runnable_type": "pyrunnable_dataiku-pulse_data-gather-audit-logs",
                "hour": 17,
                "frequency": "Hourly",
                "repeat_frequency": 1,
            },
            {
                "name": "data_gather_cleanup",
                "runnable_type": "pyrunnable_builtin-macros_clear-scenario-logs",
                "hour": 17,
                "frequency": "Daily",
                "step_config": {"age": 3, "performDeletion": True},
                "admin_config": {"allProjects": False},
            },
        ]
        for scenario_def in scenario_defs:
            scn_name = str(scenario_def["name"])
            scn_step = _ensure_or_repair_scenario(
                project,
                client=client,
                name=scn_name,
                runnable_type=str(scenario_def["runnable_type"]),
                preset_name=worker_preset_name or preset_name,
                run_as_user=run_as_user,
                hour=int(scenario_def.get("hour", 17)),
                frequency=str(scenario_def.get("frequency", "Daily")),
                repeat_frequency=int(scenario_def.get("repeat_frequency", 1)),
                step_config=scenario_def.get("step_config"),
                admin_config=scenario_def.get("admin_config"),
                notification_email=worker_notification_email,
                notification_engine=worker_notification_engine,
            )
            steps.append(
                InitStep(
                    step=f"worker:{worker_url}:{scn_step.step}",
                    status=scn_step.status,
                    message=scn_step.message,
                )
            )

            if force_scenarios and scn_step.status in {"created", "repaired"}:
                try:
                    scenario = project.get_scenario(scn_name)
                    scenario.run()
                    steps.append(
                        InitStep(
                            step=f"worker:{worker_url}:run:{scn_name}",
                            status="started",
                        )
                    )
                except Exception as e:
                    steps.append(
                        InitStep(
                            step=f"worker:{worker_url}:run:{scn_name}",
                            status="error",
                            message=repr(e),
                        )
                    )

    return steps
