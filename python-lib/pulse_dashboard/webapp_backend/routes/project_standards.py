from __future__ import annotations

import json
import logging
import os
import re
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


def _write_report_cache(
    *,
    instance_name: str,
    project_key: str,
    payload: Any,
    cache_root: Path | None = None,
) -> Path:
    json_text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    safe_instance = _safe_path_component(instance_name, "instance_name")
    safe_project = _safe_path_component(project_key, "project_key")
    cache_root = cache_root or _PROJECT_STANDARDS_CACHE_ROOT
    cache_root.mkdir(parents=True, exist_ok=True)
    target_path = cache_root / f"{safe_instance}-{safe_project}.json"
    temp_path = target_path.with_name(f".{target_path.name}.{os.getpid()}.tmp")
    try:
        temp_path.write_text(json_text, encoding="utf-8")
        temp_path.replace(target_path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
    return target_path


def _run_project_standards(worker: Mapping[str, Any], project_key: str, pulse_primary: Mapping[str, Any]) -> Any:
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
    future = project.start_run_project_standards_checks()
    report = future.wait_for_result()
    return report.data


def register_routes(bp: Blueprint) -> None:
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

        pulse_primary = _pulse_primary_config()
        matches = _enabled_worker_matches(pulse_primary, instance_name)
        if not matches:
            return _err(f"No enabled Project Standards worker is configured for instance_name={instance_name}", status=409)
        if len(matches) > 1:
            return _err(f"Multiple enabled Project Standards workers are configured for instance_name={instance_name}", status=409)

        worker = matches[0]
        try:
            payload = _run_project_standards(worker, project_key, pulse_primary)
        except ValueError as exc:
            return _err(str(exc), status=409)
        except Exception as exc:
            logger.error(
                "Project Standards run failed for instance_name=%s project_key=%s error_type=%s",
                instance_name,
                project_key,
                type(exc).__name__,
            )
            return _err("Project Standards run failed", status=502)

        try:
            _write_report_cache(instance_name=instance_name, project_key=project_key, payload=payload)
        except (TypeError, ValueError) as exc:
            logger.exception("Project Standards report payload/cache identifier invalid for instance_name=%s project_key=%s", instance_name, project_key)
            return _err(f"Project Standards report could not be cached: {exc}", status=500)
        except Exception:
            logger.exception("Project Standards report cache write failed for instance_name=%s project_key=%s", instance_name, project_key)
            return _err("Project Standards report could not be cached", status=500)

        logger.info("Project Standards report cached for instance_name=%s project_key=%s", instance_name, project_key)
        return _ok({"cached": True, "instanceName": instance_name, "projectKey": project_key})
