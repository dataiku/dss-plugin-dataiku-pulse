from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

import dataikuapi
from flask import Blueprint, current_app, request

from pulse_dashboard.webapp_backend.support import (
    _df_records,
    _ensure_ready_if_enabled,
    _err,
    _has_administration_access,
    _ok,
    _require_duckdb_engine,
)

logger = logging.getLogger(__name__)
if not logger.handlers:
    gunicorn_error_logger = logging.getLogger("gunicorn.error")
    if gunicorn_error_logger.handlers:
        logger.handlers = gunicorn_error_logger.handlers
        logger.setLevel(gunicorn_error_logger.level)
        logger.propagate = False

_MD5_RE = re.compile(r"^[a-fA-F0-9]{32}$")
_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_PULSE_PRIMARY_CONFIG_KEY = "_PULSE_DASHBOARD_PULSE_PRIMARY"
_PROJECT_STANDARDS_CACHE_ROOT = Path(os.environ.get("PULSE_PROJECT_STANDARDS_CACHE_ROOT", "/tmp/pulse/ps"))  # nosec B108 - required ephemeral report cache path
_ACTIVE_RUNS_LOCK = threading.Lock()
_ACTIVE_RUNS: dict[tuple[str, str], dict[str, Any]] = {}
_SENSITIVE_REPORT_KEY_RE = re.compile(r"(api|key|secret|token|password|credential|url)", re.IGNORECASE)


def _is_md5(value: str | None) -> bool:
    if not value:
        return False
    return bool(_MD5_RE.match(str(value).strip()))


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


def _safe_config_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _pulse_primary_config() -> Mapping[str, Any]:
    return _safe_config_mapping(current_app.config.get(_PULSE_PRIMARY_CONFIG_KEY))


def _resolve_asset_identity(query_df: Any, asset_id: str) -> dict[str, str] | None:
    asset_df = query_df(
        """
        SELECT
          instance_name AS instanceName,
          project_key AS projectKey,
          object_type AS objectType,
          object_key AS objectKey
        FROM base_asset_index
        WHERE md5(concat_ws('|', instance_name, project_key, object_type, object_key)) = ?
        LIMIT 1;
        """.strip(),
        [asset_id],
    )
    if len(asset_df.index):
        row = _df_records(asset_df)[0]
        return {
            "instanceName": str(row.get("instanceName") or "").strip(),
            "projectKey": str(row.get("projectKey") or "").strip(),
            "source": "asset",
        }

    product_df = query_df(
        """
        SELECT
          instance_name AS instanceName,
          project_key AS projectKey,
          product_type AS objectType,
          product_key AS objectKey
        FROM final_build_products_catalog
        WHERE product_id = ?
        LIMIT 1;
        """.strip(),
        [asset_id],
    )
    if len(product_df.index):
        row = _df_records(product_df)[0]
        return {
            "instanceName": str(row.get("instanceName") or "").strip(),
            "projectKey": str(row.get("projectKey") or "").strip(),
            "source": "product",
        }
    return None


def _enabled_worker_matches(pulse_primary: Mapping[str, Any], instance_name: str) -> list[Mapping[str, Any]]:
    worker_hosts = pulse_primary.get("worker_hosts")
    if not isinstance(worker_hosts, list):
        return []

    matches: list[Mapping[str, Any]] = []
    for worker in worker_hosts:
        if not isinstance(worker, Mapping):
            continue
        worker_name = str(worker.get("worker_name") or "").strip()
        if worker_name != instance_name:
            continue
        if _as_bool(worker.get("worker_enabled"), default=True):
            matches.append(worker)
    return matches


def _safe_path_component(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or not _SAFE_COMPONENT_RE.match(normalized):
        raise ValueError(f"Unsafe {label} for Project Standards cache")
    return normalized


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _run_key(instance_name: str, project_key: str) -> tuple[str, str]:
    return (instance_name, project_key)


def _write_json_cache_file(target_path: Path, payload: Any) -> Path:
    json_text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=target_path.parent,
        prefix=f".{target_path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temp_path = Path(temp_file.name)
    try:
        with temp_file:
            temp_file.write(json_text)
        temp_path.replace(target_path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
    return target_path


def _write_report_cache(
    *,
    instance_name: str,
    project_key: str,
    payload: Any,
    cache_root: Path | None = None,
) -> Path:
    safe_instance = _safe_path_component(instance_name, "instance_name")
    safe_project = _safe_path_component(project_key, "project_key")
    cache_root = cache_root or _PROJECT_STANDARDS_CACHE_ROOT
    target_path = cache_root / f"{safe_instance}-{safe_project}.json"
    return _write_json_cache_file(target_path, payload)


def _write_error_cache(
    *,
    instance_name: str,
    project_key: str,
    payload: Mapping[str, Any],
    cache_root: Path | None = None,
) -> Path:
    safe_instance = _safe_path_component(instance_name, "instance_name")
    safe_project = _safe_path_component(project_key, "project_key")
    cache_root = cache_root or _PROJECT_STANDARDS_CACHE_ROOT
    target_path = cache_root / f"{safe_instance}-{safe_project}.error.json"
    return _write_json_cache_file(target_path, dict(payload))


def _cache_paths(*, instance_name: str, project_key: str, cache_root: Path | None = None) -> tuple[Path, Path]:
    safe_instance = _safe_path_component(instance_name, "instance_name")
    safe_project = _safe_path_component(project_key, "project_key")
    cache_root = cache_root or _PROJECT_STANDARDS_CACHE_ROOT
    return (
        cache_root / f"{safe_instance}-{safe_project}.json",
        cache_root / f"{safe_instance}-{safe_project}.error.json",
    )


def _read_json_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, Mapping):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _SENSITIVE_REPORT_KEY_RE.search(key_text):
                continue
            safe[key_text] = _json_safe_value(item)
        return safe
    return str(value)


def _non_empty(value: Any) -> bool:
    return value not in (None, "", [], {})


def _numeric_severity(value: Any) -> int | float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _normalize_check(check_id: str, entry: Any) -> dict[str, Any]:
    entry_mapping = entry if isinstance(entry, Mapping) else {}
    check = entry_mapping.get("check") if isinstance(entry_mapping.get("check"), Mapping) else {}
    result = entry_mapping.get("result") if isinstance(entry_mapping.get("result"), Mapping) else {}
    native_id = str(check.get("id") or check.get("checkId") or "").strip()
    stable_id = str(check_id or native_id or "unknown_check").strip()
    name = str(check.get("name") or check.get("label") or stable_id).strip()
    description = str(check.get("description") or check.get("shortDescription") or "").strip()
    severity = _numeric_severity(result.get("severity"))

    result_details: dict[str, Any] = {}
    for key, value in result.items():
        key_text = str(key)
        if key_text in {"status", "severity", "message"}:
            continue
        safe_value = _json_safe_value(value)
        if _non_empty(safe_value):
            result_details[key_text] = safe_value

    parameters = _json_safe_value(entry_mapping.get("expandedCheckParams"))

    return {
        "id": stable_id,
        "name": name,
        "description": description,
        "tags": _string_list(check.get("tags") or check.get("categories")),
        "parameters": parameters if _non_empty(parameters) else None,
        "durationMs": entry_mapping.get("durationMs") if isinstance(entry_mapping.get("durationMs"), (int, float)) else None,
        "executionStatus": str(result.get("status") or "").strip(),
        "severity": severity,
        "message": str(result.get("message") or "").strip(),
        "resultDetails": result_details,
    }


def _normalize_report_payload(*, payload: Any, instance_name: str, project_key: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("Cached Project Standards report is not a JSON object")

    checks_raw = payload.get("bundleChecksRunInfo")
    checks: list[dict[str, Any]] = []
    if isinstance(checks_raw, Mapping):
        checks = [_normalize_check(str(check_id), entry) for check_id, entry in checks_raw.items()]

    return {
        "context": {
            "instanceName": instance_name,
            "projectKey": project_key,
            "scope": str(payload.get("scope") or "").strip(),
            "startTime": str(payload.get("startTime") or "").strip(),
            "totalDurationMs": payload.get("totalDurationMs") if isinstance(payload.get("totalDurationMs"), (int, float)) else None,
        },
        "checks": checks,
    }


def _read_error_sidecar(error_path: Path) -> dict[str, Any] | None:
    if not error_path.exists():
        return None
    payload = _read_json_file(error_path)
    if not isinstance(payload, Mapping):
        return None
    return {
        "runId": str(payload.get("runId") or "").strip(),
        "state": str(payload.get("state") or "").strip(),
        "instanceName": str(payload.get("instanceName") or "").strip(),
        "projectKey": str(payload.get("projectKey") or "").strip(),
        "startedAt": str(payload.get("startedAt") or "").strip(),
        "finishedAt": str(payload.get("finishedAt") or "").strip(),
        "exceptionType": str(payload.get("exceptionType") or "").strip(),
    }


def _remove_error_cache(*, instance_name: str, project_key: str, cache_root: Path | None = None) -> None:
    safe_instance = _safe_path_component(instance_name, "instance_name")
    safe_project = _safe_path_component(project_key, "project_key")
    cache_root = cache_root or _PROJECT_STANDARDS_CACHE_ROOT
    try:
        (cache_root / f"{safe_instance}-{safe_project}.error.json").unlink()
    except FileNotFoundError:
        return


def _start_project_standards_future(worker: Mapping[str, Any], project_key: str, pulse_primary: Mapping[str, Any]) -> Any:
    worker_url = str(worker.get("worker_url") or "").strip()
    worker_api = str(worker.get("worker_api") or "").strip()
    if not worker_url:
        raise ValueError("Matched worker is missing worker_url")
    if not worker_api:
        raise ValueError("Matched worker is missing worker_api")

    client_kwargs: dict[str, Any] = {"host": worker_url, "api_key": worker_api}
    if _as_bool(pulse_primary.get("ignore_certs"), default=False):
        client_kwargs["insecure_tls"] = True
    client = dataikuapi.DSSClient(**client_kwargs)
    project = client.get_project(project_key)
    return project.start_run_project_standards_checks()


def _background_wait_and_cache_report(
    *,
    run_id: str,
    run_key: tuple[str, str],
    future: Any,
    instance_name: str,
    project_key: str,
    started_at: str,
) -> None:
    state = "failed"
    try:
        report = future.wait_for_result()
        payload = report.data
        _write_report_cache(instance_name=instance_name, project_key=project_key, payload=payload)
        _remove_error_cache(instance_name=instance_name, project_key=project_key)
        state = "succeeded"
        logger.info(
            "Project Standards background run finished runId=%s instance_name=%s project_key=%s state=%s",
            run_id,
            instance_name,
            project_key,
            state,
        )
    except Exception as exc:
        error_payload = {
            "runId": run_id,
            "state": "failed",
            "instanceName": instance_name,
            "projectKey": project_key,
            "startedAt": started_at,
            "finishedAt": _utc_now_iso(),
            "exceptionType": type(exc).__name__,
        }
        try:
            _write_error_cache(instance_name=instance_name, project_key=project_key, payload=error_payload)
        except Exception as cache_exc:
            logger.error(
                "Project Standards error artifact write failed runId=%s instance_name=%s project_key=%s exception_type=%s cache_exception_type=%s",
                run_id,
                instance_name,
                project_key,
                type(exc).__name__,
                type(cache_exc).__name__,
            )
        else:
            logger.error(
                "Project Standards background run failed runId=%s instance_name=%s project_key=%s state=%s exception_type=%s",
                run_id,
                instance_name,
                project_key,
                state,
                type(exc).__name__,
            )
    finally:
        with _ACTIVE_RUNS_LOCK:
            active = _ACTIVE_RUNS.get(run_key)
            if active and active.get("runId") == run_id:
                _ACTIVE_RUNS.pop(run_key, None)


def register_routes(bp: Blueprint) -> None:
    @bp.route("/api/project-standards/report", methods=["POST"])
    def get_project_standards_report():
        if not _has_administration_access():
            return _err("Administration access is required", status=403)

        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return _err("Expected JSON request body", status=400)

        asset_id = str(body.get("assetId") or "").strip()
        if not _is_md5(asset_id):
            return _err("Invalid or missing assetId", status=400)

        try:
            query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
            _ensure_ready_if_enabled()
            identity = _resolve_asset_identity(query_df, asset_id)
        except Exception:
            logger.exception("Project Standards cached report asset lookup failed for assetId=%s", asset_id)
            return _err("Unable to resolve selected asset", status=500)

        if identity is None:
            return _err("Asset not found", status=404)

        instance_name = identity["instanceName"]
        project_key = identity["projectKey"]
        if not instance_name:
            return _err("Selected asset does not have an instance_name", status=400)
        if not project_key:
            return _err("Selected asset does not have a project_key", status=400)

        try:
            report_path, error_path = _cache_paths(instance_name=instance_name, project_key=project_key)
            sidecar = _read_error_sidecar(error_path)
            if not report_path.exists():
                return _ok(
                    {
                        "available": False,
                        "instanceName": instance_name,
                        "projectKey": project_key,
                        "cacheState": "missing",
                        "lastError": sidecar,
                    }
                )

            normalized = _normalize_report_payload(
                payload=_read_json_file(report_path),
                instance_name=instance_name,
                project_key=project_key,
            )
        except json.JSONDecodeError:
            logger.error("Project Standards cached report is malformed instance_name=%s project_key=%s", instance_name, project_key)
            return _err("Cached Project Standards report is malformed", status=500)
        except Exception as exc:
            logger.error(
                "Project Standards cached report could not be read instance_name=%s project_key=%s error_type=%s",
                instance_name,
                project_key,
                type(exc).__name__,
            )
            return _err("Cached Project Standards report could not be read", status=500)

        return _ok(
            {
                "available": True,
                "cacheState": "available_with_error" if normalized and sidecar else "available",
                "lastError": sidecar,
                **normalized,
            }
        )

    @bp.route("/api/project-standards/run", methods=["POST"])
    def run_project_standards_report():
        if not _has_administration_access():
            return _err("Administration access is required", status=403)

        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return _err("Expected JSON request body", status=400)

        asset_id = str(body.get("assetId") or "").strip()
        if not _is_md5(asset_id):
            return _err("Invalid or missing assetId", status=400)

        try:
            query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
            _ensure_ready_if_enabled()
            identity = _resolve_asset_identity(query_df, asset_id)
        except Exception:
            logger.exception("Project Standards asset lookup failed for assetId=%s", asset_id)
            return _err("Unable to resolve selected asset", status=500)

        if identity is None:
            return _err("Asset not found", status=404)

        instance_name = identity["instanceName"]
        project_key = identity["projectKey"]
        if not instance_name:
            return _err("Selected asset does not have an instance_name", status=400)
        if not project_key:
            return _err("Selected asset does not have a project_key", status=400)

        run_key = _run_key(instance_name, project_key)
        run_id = uuid.uuid4().hex
        started_at = _utc_now_iso()
        with _ACTIVE_RUNS_LOCK:
            active_run = _ACTIVE_RUNS.get(run_key)
            if active_run:
                return _ok(
                    {
                        "runId": str(active_run.get("runId") or ""),
                        "state": "running",
                        "deduped": True,
                        "instanceName": instance_name,
                        "projectKey": project_key,
                    },
                    status=202,
                )
            _ACTIVE_RUNS[run_key] = {"runId": run_id, "startedAt": started_at, "state": "starting"}

        pulse_primary = _pulse_primary_config()
        matches = _enabled_worker_matches(pulse_primary, instance_name)
        if not matches:
            with _ACTIVE_RUNS_LOCK:
                active = _ACTIVE_RUNS.get(run_key)
                if active and active.get("runId") == run_id:
                    _ACTIVE_RUNS.pop(run_key, None)
            return _err(f"No enabled Project Standards worker is configured for instance_name={instance_name}", status=409)
        if len(matches) > 1:
            with _ACTIVE_RUNS_LOCK:
                active = _ACTIVE_RUNS.get(run_key)
                if active and active.get("runId") == run_id:
                    _ACTIVE_RUNS.pop(run_key, None)
            return _err(f"Multiple enabled Project Standards workers are configured for instance_name={instance_name}", status=409)

        worker = matches[0]
        try:
            future = _start_project_standards_future(worker, project_key, pulse_primary)
        except ValueError as exc:
            with _ACTIVE_RUNS_LOCK:
                active = _ACTIVE_RUNS.get(run_key)
                if active and active.get("runId") == run_id:
                    _ACTIVE_RUNS.pop(run_key, None)
            return _err(str(exc), status=409)
        except Exception as exc:
            with _ACTIVE_RUNS_LOCK:
                active = _ACTIVE_RUNS.get(run_key)
                if active and active.get("runId") == run_id:
                    _ACTIVE_RUNS.pop(run_key, None)
            logger.error(
                "Project Standards start failed runId=%s instance_name=%s project_key=%s error_type=%s",
                run_id,
                instance_name,
                project_key,
                type(exc).__name__,
            )
            try:
                _write_error_cache(
                    instance_name=instance_name,
                    project_key=project_key,
                    payload={
                        "runId": run_id,
                        "state": "failed",
                        "instanceName": instance_name,
                        "projectKey": project_key,
                        "startedAt": started_at,
                        "finishedAt": _utc_now_iso(),
                        "exceptionType": type(exc).__name__,
                    },
                )
            except Exception as cache_exc:
                logger.error(
                    "Project Standards start error artifact write failed runId=%s instance_name=%s project_key=%s exception_type=%s cache_exception_type=%s",
                    run_id,
                    instance_name,
                    project_key,
                    type(exc).__name__,
                    type(cache_exc).__name__,
                )
            return _err("Project Standards run failed", status=502)

        try:
            thread = threading.Thread(
                target=_background_wait_and_cache_report,
                kwargs={
                    "run_id": run_id,
                    "run_key": run_key,
                    "future": future,
                    "instance_name": instance_name,
                    "project_key": project_key,
                    "started_at": started_at,
                },
                name=f"pulse-project-standards-{run_id[:12]}",
                daemon=True,
            )
            thread.start()
            with _ACTIVE_RUNS_LOCK:
                active = _ACTIVE_RUNS.get(run_key)
                if active and active.get("runId") == run_id:
                    active["state"] = "running"
        except Exception as exc:
            with _ACTIVE_RUNS_LOCK:
                active = _ACTIVE_RUNS.get(run_key)
                if active and active.get("runId") == run_id:
                    _ACTIVE_RUNS.pop(run_key, None)
            try:
                _write_error_cache(
                    instance_name=instance_name,
                    project_key=project_key,
                    payload={
                        "runId": run_id,
                        "state": "background_scheduling_failed",
                        "instanceName": instance_name,
                        "projectKey": project_key,
                        "startedAt": started_at,
                        "finishedAt": _utc_now_iso(),
                        "exceptionType": type(exc).__name__,
                    },
                )
            except Exception as cache_exc:
                logger.error(
                    "Project Standards scheduling error artifact write failed runId=%s instance_name=%s project_key=%s exception_type=%s cache_exception_type=%s",
                    run_id,
                    instance_name,
                    project_key,
                    type(exc).__name__,
                    type(cache_exc).__name__,
                )
            logger.error(
                "Project Standards background scheduling failed runId=%s instance_name=%s project_key=%s error_type=%s",
                run_id,
                instance_name,
                project_key,
                type(exc).__name__,
            )
            return _err("Project Standards background run could not be scheduled", status=500)

        logger.info(
            "Project Standards background run started runId=%s instance_name=%s project_key=%s",
            run_id,
            instance_name,
            project_key,
        )
        return _ok({"runId": run_id, "state": "running", "deduped": False, "instanceName": instance_name, "projectKey": project_key}, status=202)
