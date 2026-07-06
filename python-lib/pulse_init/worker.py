from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

import dataiku
import dataikuapi

from .notifications import (
    build_failure_reporter,
    ensure_failure_reporter,
    resolve_email_channel_id,
)

logger = logging.getLogger(__name__)


def _instance_label(url: str) -> str:
    """Human-readable instance label for notification subjects."""

    label = str(url or "").strip()
    for prefix in ("https://", "http://"):
        if label.startswith(prefix):
            label = label[len(prefix):]
    return label.rstrip("/")


@dataclass(frozen=True)
class InitStep:
    step: str
    status: str
    message: str | None = None


def _safe_get(d: Mapping[str, Any] | None, key: str, default: Any = None) -> Any:
    if not isinstance(d, Mapping):
        return default
    return d.get(key, default)


def _resolve_primary_preset_name(local_client: Any) -> tuple[str, str | None]:
    """Return (preset_name, warning_message)."""

    try:
        plugin_handle = local_client.get_plugin(plugin_id="dataiku-pulse")
        plugin_settings = plugin_handle.get_settings()
        pdi_ps = plugin_settings.get_parameter_set(
            parameter_set_name="params-dashboard-instance"
        )
        names = pdi_ps.list_preset_names() or []
        if len(names) == 1:
            return str(names[0]), None
        if names:
            return (
                str(names[0]),
                f"Multiple presets exist in params-dashboard-instance ({', '.join(map(str, names))}); "
                f"using {names[0]!r}. Set 'Parameter Set' per worker host to override.",
            )
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


def _sync_plugin_from_hub(
    *,
    remote_client: Any,
    hub_params: Mapping[str, Any],
    preset_name: str,
    run_as_user: str,
    update_github: bool,
    force_skip_github: bool,
) -> list[InitStep]:
    """Install/update plugin + code env + preset config on a remote DSS."""

    steps: list[InitStep] = []

    repo_url = str(_safe_get(hub_params, "pulse_repo_url", ""))
    repo_branch = str(_safe_get(hub_params, "pulse_repo_branch", "main"))

    if not repo_url:
        return [
            InitStep(
                step="remote:plugin_sync",
                status="error",
                message="pulse_repo_url missing in hub params",
            )
        ]

    plugin_id = "dataiku-pulse"
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
            else:
                steps.append(InitStep(step="remote:update_from_git", status="skipped"))
                steps.append(InitStep(step="remote:update_code_env", status="skipped"))

        # Sync the preset config so worker scenarios reference the correct preset name.
        plugin_handle = remote_client.get_plugin(plugin_id=plugin_id)
        plugin_settings = plugin_handle.get_settings()
        pdi_ps = plugin_settings.get_parameter_set(
            parameter_set_name="params-dashboard-instance"
        )
        preset = pdi_ps.get_preset(preset_name=preset_name)
        if not preset:
            preset = pdi_ps.create_preset(preset_name=preset_name)
        preset.get_raw()["pluginConfig"] = dict(hub_params)
        pdi_ps.save()
        steps.append(
            InitStep(step=f"remote:preset_sync:{preset_name}", status="updated")
        )

    except Exception as e:
        steps.append(
            InitStep(step="remote:plugin_sync", status="error", message=repr(e))
        )

    return steps


def _ensure_project(
    *,
    client: Any,
    project_key: str,
    owner_login: str,
) -> tuple[Any | None, InitStep]:
    try:
        if project_key in (client.list_project_keys() or []):
            return client.get_project(project_key), InitStep(
                step=f"project:{project_key}", status="already_exists"
            )
        project = client.create_project(
            project_key=project_key, name=project_key, owner=owner_login
        )
        return project, InitStep(step=f"project:{project_key}", status="created")
    except Exception as e:
        return None, InitStep(
            step=f"project:{project_key}", status="error", message=repr(e)
        )


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
    name: str,
    runnable_type: str,
    preset_name: str,
    run_as_user: str,
    hour: int,
    frequency: str = "Daily",
    repeat_frequency: int = 1,
    step_config: Mapping[str, Any] | None = None,
    admin_config: Mapping[str, Any] | None = None,
    reporter: Mapping[str, Any] | None = None,
    remove_reporter: bool = False,
) -> list[InitStep]:
    """Create or repair a step-based scenario with a runnable step.

    `reporter` (built by `build_failure_reporter`) is upserted into the
    scenario's reporters; `remove_reporter=True` removes a stale Pulse
    reporter instead (notifications disabled). With neither, existing
    reporters are left untouched. User-added reporters are never modified.
    """

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

        reporter_step: InitStep | None = None
        if reporter is not None or remove_reporter:
            # A reporter problem must never fail scenario creation/repair.
            try:
                outcome = ensure_failure_reporter(
                    settings,
                    reporter=(dict(reporter) if reporter is not None else None),
                )
                reporter_step = InitStep(
                    step=f"scenario:{name}:reporter", status=outcome
                )
            except Exception as e:
                reporter_step = InitStep(
                    step=f"scenario:{name}:reporter",
                    status="error",
                    message=repr(e),
                )

        settings.save()
        steps = [InitStep(step=f"scenario:{name}", status=status)]
        if reporter_step is not None:
            steps.append(reporter_step)
        return steps

    except Exception as e:
        return [InitStep(step=f"scenario:{name}", status="error", message=repr(e))]


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
    run_as_user = str(_safe_get(hub_params, "pulse_dataiku_user", "admin"))
    ignore_certs = bool(_safe_get(hub_params, "ignore_certs", False))

    notification_email = str(_safe_get(hub_params, "notification_email", "") or "").strip()
    notification_channel_id = (
        str(_safe_get(hub_params, "notification_channel_id", "") or "").strip() or None
    )

    worker_hosts = _safe_get(hub_params, "worker_hosts", []) or []

    now_ts = datetime.now(timezone.utc).isoformat()

    # Cursor variables are stored on the worker project.
    for worker in worker_hosts:
        worker_url = str(_safe_get(worker, "worker_url", ""))
        worker_api = _safe_get(worker, "worker_api")

        # Per-worker preset override (the parameter-set subparam was
        # previously ignored).
        worker_preset_name = str(_safe_get(worker, "preset_name", "") or "").strip() or preset_name
        if worker_preset_name != preset_name:
            steps.append(
                InitStep(
                    step=f"worker:{worker_url}:preset_name",
                    status="ok",
                    message=worker_preset_name,
                )
            )

        if not worker_url:
            steps.append(
                InitStep(
                    step="worker:connect", status="error", message="worker_url missing"
                )
            )
            continue

        is_hub = bool(hub_url) and (worker_url == hub_url)

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
                hub_params=hub_params,
                preset_name=worker_preset_name,
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
        project, st = _ensure_project(
            client=client,
            project_key=worker_key,
            owner_login=run_as_user,
        )
        steps.append(
            InitStep(
                step=f"worker:{worker_url}:{st.step}",
                status=st.status,
                message=st.message,
            )
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

        # Failure-notification channel: resolved once per worker instance.
        # A skip (no email configured, 0 or several candidate channels, old
        # DSS, non-mail channel) must NOT fail init — scenarios are still
        # created, only without a Pulse reporter.
        notif_reporter_factory = None
        remove_reporter = False
        if not notification_email:
            remove_reporter = True
            steps.append(
                InitStep(
                    step=f"worker:{worker_url}:notifications",
                    status="skipped",
                    message="notification_email not set; Pulse reporters are removed",
                )
            )
        else:
            try:
                channel_id, channel_kind, skip_reason = resolve_email_channel_id(
                    client, preferred_channel_id=notification_channel_id
                )
            except Exception as e:  # defensive: resolution must never fail init
                channel_id, channel_kind, skip_reason = None, None, repr(e)
            if channel_id is None:
                steps.append(
                    InitStep(
                        step=f"worker:{worker_url}:notifications",
                        status="skipped",
                        message=skip_reason,
                    )
                )
            else:
                def notif_reporter_factory(
                    scenario_name: str,
                    *,
                    _channel_id=channel_id,
                    _channel_kind=channel_kind,
                    _worker_url=worker_url,
                ):
                    return build_failure_reporter(
                        channel_id=_channel_id,
                        channel_type=_channel_kind,
                        recipient=notification_email,
                        scenario_name=scenario_name,
                        project_key=worker_key,
                        instance_label=_instance_label(_worker_url),
                        instance_url=_worker_url,
                    )

                steps.append(
                    InitStep(
                        step=f"worker:{worker_url}:notifications",
                        status="ok",
                        message=f"channel={channel_id}",
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

            reporter = None
            reporter_skip_step: InitStep | None = None
            if notif_reporter_factory is not None:
                reporter, build_skip_reason = notif_reporter_factory(scn_name)
                if reporter is None:
                    reporter_skip_step = InitStep(
                        step=f"worker:{worker_url}:scenario:{scn_name}:reporter",
                        status="skipped",
                        message=build_skip_reason,
                    )

            scn_steps = _ensure_or_repair_scenario(
                project,
                name=scn_name,
                runnable_type=str(scenario_def["runnable_type"]),
                preset_name=worker_preset_name,
                run_as_user=run_as_user,
                hour=int(scenario_def.get("hour", 17)),
                frequency=str(scenario_def.get("frequency", "Daily")),
                repeat_frequency=int(scenario_def.get("repeat_frequency", 1)),
                step_config=scenario_def.get("step_config"),
                admin_config=scenario_def.get("admin_config"),
                reporter=reporter,
                remove_reporter=remove_reporter,
            )
            scn_step = scn_steps[0]
            steps.extend(
                InitStep(
                    step=f"worker:{worker_url}:{s.step}",
                    status=s.status,
                    message=s.message,
                )
                for s in scn_steps
            )
            if reporter_skip_step is not None:
                steps.append(reporter_skip_step)

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
