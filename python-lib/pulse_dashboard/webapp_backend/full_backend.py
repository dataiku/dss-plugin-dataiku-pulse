from __future__ import annotations

# Dataiku Pulse Dashboard webapp backend.
#
# This backend serves API endpoints and (optionally) serves the React build as
# static assets. The frontend build is stored under the plugin `resource/`
# folder and `webapps/pulse-dashboard/body.html` points to the build's
# `index.html`.

import json
import logging
import math
import re
import sys
import threading
import time
import ast
from collections import Counter
from io import BytesIO
from functools import wraps
from pathlib import Path
from typing import Any, cast

import yaml
import duckdb
from flask import Blueprint, Flask, jsonify, request, send_file, send_from_directory
from shared_duckdb.sql_utils import quote_identifier, validate_identifier

from pulse_dashboard.reporting import build_users_report_pdf_bytes

# Resolve repo paths for local dev static serving.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_BUILD_DIR = _REPO_ROOT / "resource" / "pulse-dashboard" / "build"
_BODY_HTML_PATH = _REPO_ROOT / "webapps" / "pulse-dashboard" / "body.html"

# In DSS, `app` is injected into the module globals by the webapp runner.
# For local dev runs, this will be missing and we create an app below.
bp = Blueprint("pulse_dashboard", __name__)
_IS_LOCAL_DEV = False


logger = logging.getLogger(__name__)
logger.info("pulse_dashboard.webapp_backend.full_backend imported from %s", __file__)

_MAX_PAGE_LIMIT = 250
_MAX_LOOKBACK_DAYS = 365
_MAX_LOOKBACK_MONTHS = 24
_ADVANCED_LLM_MESH_ADDON_KEY = "advancedLLMMesh"
_ADVANCED_LLM_MESH_LICENSE_TABLE = "base_license_addon_licenses_latest"
_ADVANCED_LLM_MESH_DISABLED_CAPABILITY = {
    "enabled": False,
    "licensedInstances": [],
}
_ADVANCED_LLM_MESH_CAPABILITY_SQL = (
    "SELECT instance_name, addon_enabled\n"
    "FROM base_license_addon_licenses_latest\n"
    "WHERE addon_key = ?;"
)
_ADVANCED_LLM_MESH_REQUIRED_COLUMNS = frozenset({"instance_name", "addon_key", "addon_enabled"})
_advanced_llm_mesh_capability_cache: dict[str, Any] | None = None
_USERS_FACETS_INSTANCES_SQL = (
    "SELECT DISTINCT instance_name\n"
    "FROM final_users_directory\n"
    "WHERE instance_name IS NOT NULL\n"
    "ORDER BY 1;"
)
def _users_facets_instances_sql() -> str:
    return _USERS_FACETS_INSTANCES_SQL


def _invalidate_capability_cache() -> None:
    global _advanced_llm_mesh_capability_cache
    _advanced_llm_mesh_capability_cache = None


def _normalize_optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
        return None

    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "1.0"}:
        return True
    if normalized in {"false", "0", "0.0"}:
        return False
    return None


def _normalize_truthy_license_flag(value: Any) -> bool:
    normalized = _normalize_optional_bool(value)
    if normalized is not None:
        return normalized

    text = str(value or "").strip()
    if text in {"yes", "Yes"}:
        return True
    if text in {"no", "No"}:
        return False
    return False


def _relation_columns(query_df, relation_name: str) -> set[str]:
    rows = query_df(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'main' AND table_name = ?
        """.strip(),
        [relation_name],
    )
    if rows is None or rows.empty:
        return set()
    return {
        str(row.get("column_name") or "").strip()
        for row in _df_records(rows)
        if str(row.get("column_name") or "").strip()
    }


def get_advanced_llm_mesh_capability() -> dict[str, Any]:
    global _advanced_llm_mesh_capability_cache

    if _advanced_llm_mesh_capability_cache is not None:
        return {
            "enabled": bool(_advanced_llm_mesh_capability_cache.get("enabled")),
            "licensedInstances": list(_advanced_llm_mesh_capability_cache.get("licensedInstances") or []),
        }

    try:
        query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        if not _duckdb_relation_exists(query_df, _ADVANCED_LLM_MESH_LICENSE_TABLE):
            _advanced_llm_mesh_capability_cache = dict(_ADVANCED_LLM_MESH_DISABLED_CAPABILITY)
            return dict(_ADVANCED_LLM_MESH_DISABLED_CAPABILITY)

        available_columns = _relation_columns(query_df, _ADVANCED_LLM_MESH_LICENSE_TABLE)
        if not _ADVANCED_LLM_MESH_REQUIRED_COLUMNS.issubset(available_columns):
            logger.warning(
                "advanced LLM Mesh capability disabled: %s missing required columns %s (available=%s)",
                _ADVANCED_LLM_MESH_LICENSE_TABLE,
                sorted(_ADVANCED_LLM_MESH_REQUIRED_COLUMNS - available_columns),
                sorted(available_columns),
            )
            _advanced_llm_mesh_capability_cache = dict(_ADVANCED_LLM_MESH_DISABLED_CAPABILITY)
            return dict(_ADVANCED_LLM_MESH_DISABLED_CAPABILITY)

        rows_df = query_df(_ADVANCED_LLM_MESH_CAPABILITY_SQL, [_ADVANCED_LLM_MESH_ADDON_KEY])
        if rows_df is None or rows_df.empty:
            _advanced_llm_mesh_capability_cache = dict(_ADVANCED_LLM_MESH_DISABLED_CAPABILITY)
            return dict(_ADVANCED_LLM_MESH_DISABLED_CAPABILITY)

        licensed_instances = sorted(
            {
                str(row.get("instance_name") or "").strip()
                for row in _df_records(rows_df)
                if str(row.get("instance_name") or "").strip() and _normalize_truthy_license_flag(row.get("addon_enabled"))
            }
        )
        capability = {
            "enabled": bool(licensed_instances),
            "licensedInstances": licensed_instances,
        }
        _advanced_llm_mesh_capability_cache = capability
        return {
            "enabled": capability["enabled"],
            "licensedInstances": list(capability["licensedInstances"]),
        }
    except Exception:
        logger.exception("advanced LLM Mesh capability resolution failed")
        _advanced_llm_mesh_capability_cache = dict(_ADVANCED_LLM_MESH_DISABLED_CAPABILITY)
        return dict(_ADVANCED_LLM_MESH_DISABLED_CAPABILITY)


def _parse_history_groups(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(v).strip() for v in parsed if str(v).strip()]
        except Exception:
            pass
        try:
            parsed = ast.literal_eval(raw)
            if isinstance(parsed, list):
                return [str(v).strip() for v in parsed if str(v).strip()]
        except Exception:
            pass
        return [raw]
    return [str(value).strip()] if str(value).strip() else []


def _normalize_user_directory_record(row: dict[str, Any]) -> dict[str, Any]:
    resulting_profile = str(row.get("users_resultinguserprofile") or "").strip()
    fallback_profile = str(row.get("users_userprofile") or "").strip()
    return {
        "instance_name": row.get("instance_name"),
        "login": row.get("users_login"),
        "display_name": row.get("users_displayname"),
        "email": row.get("users_email"),
        "user_profile": resulting_profile or fallback_profile or None,
        "enabled": _normalize_optional_bool(row.get("users_enabled")),
        "source_type": row.get("users_sourcetype"),
        "creation_date": row.get("users_creationdate"),
        "last_successful_login": row.get("users_lastsuccessfullogin"),
        "last_failed_login": row.get("users_lastfailedlogin"),
        "last_session_activity": row.get("users_lastsessionactivity"),
        "first_commit_date": row.get("users_first_commit_date"),
        "last_commit_date": row.get("users_last_commit_date"),
        "groups": _parse_history_groups(row.get("users_groups")),
        "run_ts": row.get("run_ts"),
        "partition_date": row.get("partition_date"),
    }


def _user_directory_record_rank_key(row: dict[str, Any]) -> tuple[int, int, int, int, str, str, str]:
    return (
        int(bool(str(row.get("display_name") or "").strip())),
        int(bool(str(row.get("email") or "").strip())),
        int(bool(str(row.get("user_profile") or "").strip())),
        int(row.get("enabled") is not None),
        str(row.get("run_ts") or ""),
        str(row.get("partition_date") or ""),
        str(row.get("instance_name") or ""),
    )


def _user_detail_instances_sql(*, include_instance_filter: bool) -> str:
    optional_instance_filter = "AND instance_name = ?" if include_instance_filter else ""
    sql = "\n".join(
        [
            "WITH ranked AS (",
            "    SELECT",
            "        instance_name,",
            "        users_login,",
            "        users_sourcetype,",
            "        users_displayname,",
            "        users_groups,",
            "        users_userprofile,",
            "        users_creationdate,",
            "        users_enabled,",
            "        users_resultinguserprofile,",
            "        users_email,",
            "        users_lastsuccessfullogin,",
            "        users_lastfailedlogin,",
            "        users_lastsessionactivity,",
            "        users_first_commit_date,",
            "        users_last_commit_date,",
            "        run_ts,",
            "        partition_date,",
            "        ROW_NUMBER() OVER (",
            "            PARTITION BY instance_name",
            "            ORDER BY",
            "                run_ts DESC NULLS LAST,",
            "                partition_date DESC NULLS LAST,",
            "                users_lastsessionactivity DESC NULLS LAST",
            "        ) AS rn",
            "    FROM base_users_instance_metadata_history",
            "    WHERE lower(trim(users_login)) = lower(trim(?))",
            f"    {optional_instance_filter}" if optional_instance_filter else "",
            ")",
            "SELECT",
            "    instance_name,",
            "    users_login,",
            "    users_sourcetype,",
            "    users_displayname,",
            "    users_groups,",
            "    users_userprofile,",
            "    users_creationdate,",
            "    users_enabled,",
            "    users_resultinguserprofile,",
            "    users_email,",
            "    users_lastsuccessfullogin,",
            "    users_lastfailedlogin,",
            "    users_lastsessionactivity,",
            "    users_first_commit_date,",
            "    users_last_commit_date,",
            "    run_ts,",
            "    partition_date",
            "FROM ranked",
            "WHERE rn = 1",
            "ORDER BY instance_name ASC;",
        ]
    )
    return "\n".join(line for line in sql.splitlines() if line.strip())


def _users_directory_cte_sql(*, cte_name: str = "directory") -> str:
    lines = [
        cte_name + " AS (",
        "  WITH src AS (",
        "    SELECT",
        "      instance_name,",
        "      users_login AS login,",
        "      lower(trim(users_login)) AS login_norm,",
        "      users_displayname AS display_name,",
        "      users_email AS email,",
        "      users_enabled = 'True' AS enabled,",
        "      users_userprofile AS user_profile,",
        "      users_groups AS group_names,",
        "      run_ts,",
        "      CASE WHEN instance_name IN (" + _hub_instances_sql_list() + ") THEN 1 ELSE 0 END AS is_hub",
        "    FROM base_users_instance_metadata_history",
        "    WHERE users_login IS NOT NULL",
        "      AND length(trim(users_login)) > 0",
        "  ),",
        "  ranked AS (",
        "    SELECT",
        "      *,",
        "      ROW_NUMBER() OVER (",
        "        PARTITION BY login_norm",
        "        ORDER BY run_ts DESC, is_hub DESC, instance_name ASC",
        "      ) AS rn",
        "    FROM src",
        "  )",
        "  SELECT",
        "    instance_name,",
        "    login,",
        "    login_norm,",
        "    display_name,",
        "    email,",
        "    enabled,",
        "    user_profile,",
        "    group_names,",
        "    run_ts",
        "  FROM ranked",
        "  WHERE rn = 1",
        ")",
    ]
    return "\n".join(lines) + "\n"

def _ensure_consumption_products_views(_create_connection) -> None:
    conn = _create_connection(read_only=False)
    try:
        ensure_consumption_product_views(conn)
    finally:
        conn.close()




def _activity_source_kind(_query_df) -> str:
    if _duckdb_relation_exists(_query_df, "final_build_development_activity_events"):
        return "final_build"
    return "fallback_join"


def _current_user_auth_info() -> dict[str, Any] | None:
    try:
        import dataiku

        request_headers = dict(request.headers)
        auth_info = dataiku.api_client().get_auth_info_from_browser_headers(request_headers, with_secrets=False)
        return auth_info if isinstance(auth_info, dict) else None
    except Exception:
        logger.exception("Unable to resolve current DSS user for administration check")
        return None


def _parse_group_names(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(group).strip() for group in value if str(group).strip()]
    return []


def _has_administration_access() -> bool:
    standard = _read_standard_project_variables() or {}
    administration_owner = standard.get("administration_owner")
    if isinstance(administration_owner, dict):
        administration_group = str(administration_owner.get("value") or "").strip().lower()
    else:
        administration_group = str(administration_owner or "").strip().lower()
    if not administration_group:
        return False

    auth_info = _current_user_auth_info() or {}
    groups = _parse_group_names(auth_info.get("groups") or auth_info.get("userGroups") or auth_info.get("groupNames"))
    return any(str(group).strip().lower() == administration_group for group in groups)


def _has_organization_access() -> bool:
    standard = _read_standard_project_variables() or {}
    organization_owner = standard.get("organization_owner")
    if isinstance(organization_owner, dict):
        organization_group = str(organization_owner.get("value") or "").strip().lower()
    else:
        organization_group = str(organization_owner or "").strip().lower()

    if not organization_group:
        return False

    auth_info = _current_user_auth_info() or {}
    groups = _parse_group_names(auth_info.get("groups") or auth_info.get("userGroups") or auth_info.get("groupNames"))
    normalized_groups = {str(group).strip().lower() for group in groups}
    return organization_group in normalized_groups or _has_administration_access()


def _resolve_dashboard_topology_preset(plugin_handle: Any) -> tuple[str, str | None]:
    try:
        plugin_settings = plugin_handle.get_settings()
        parameter_set = plugin_settings.get_parameter_set(parameter_set_name="params-dashboard-instance")
        preset_names = parameter_set.list_preset_names() or []
        if preset_names:
            return str(preset_names[0]), None
        return "primary", "No preset found in params-dashboard-instance; using 'primary'"
    except Exception as exc:
        return "primary", f"Could not resolve preset name; using 'primary': {exc!r}"


def _sanitize_topology_config(plugin_config: dict[str, Any] | None) -> dict[str, Any]:
    config = dict(plugin_config or {})
    hub_url = str(config.get("pulse_project_url") or "").strip()
    raw_workers = config.get("worker_hosts")
    workers: list[dict[str, str]] = []
    invalid_worker_entries = 0

    if isinstance(raw_workers, list):
        for entry in raw_workers:
            if not isinstance(entry, dict):
                invalid_worker_entries += 1
                continue
            worker_url = str(entry.get("worker_url") or "").strip()
            if not worker_url:
                invalid_worker_entries += 1
                continue
            workers.append(
                {
                    "url": worker_url,
                    "classification": str(entry.get("worker_classification") or "").strip(),
                    "presetName": str(entry.get("preset_name") or "").strip(),
                }
            )

    return {
        "hub": {"url": hub_url},
        "workers": workers,
        "diagnostics": {
            "hub_url_found": bool(hub_url),
            "worker_hosts_found": raw_workers is not None,
            "worker_hosts_type": type(raw_workers).__name__ if raw_workers is not None else "missing",
            "worker_entries_seen": len(raw_workers) if isinstance(raw_workers, list) else 0,
            "valid_worker_urls": len(workers),
            "invalid_worker_entries": invalid_worker_entries,
        },
    }


def _activity_source_from_sql(source_kind: str) -> str:
    if source_kind == "final_build":
        return "FROM final_build_development_activity_events\n"
    return (
        "FROM fact_dev_activity_events e\n"
        "LEFT JOIN dim_category_to_capability m\n"
        "  ON lower(trim(m.dataiku_category)) = lower(trim(e.dataiku_category))\n"
    )


def _activity_source_select_sql(source_kind: str) -> str:
    if source_kind == "final_build":
        return ""
    return (
        "    e.timestamp,\n"
        "    e.instance_name,\n"
        "    e.login,\n"
        "    e.project_key,\n"
        "    e.msgtype,\n"
        "    e.msgtypebase AS base_tag,\n"
        "    COALESCE(m.category_display_name, e.dataiku_category) AS dataiku_category,\n"
        "    COALESCE(m.capability_display_name, m.capability, 'Uncategorized') AS capability\n"
    )


def _wrap_activity_query(source_kind: str, body_sql: str) -> str:
    if source_kind == "final_build":
        return body_sql.format(from_sql=_activity_source_from_sql(source_kind))
    return (
        "WITH activity_source AS (\n"
        "  SELECT\n"
        + _activity_source_select_sql(source_kind)
        + _activity_source_from_sql(source_kind)
        + ")\n"
        + body_sql.format(from_sql="FROM activity_source\n")
    )


def _user_drilldown_daily_sql(source_kind: str) -> str:
    return _wrap_activity_query(
        source_kind,
        "SELECT\n"
        "  CAST(CAST(date_trunc('day', timestamp) AS DATE) AS VARCHAR) AS label,\n"
        "  COUNT(*) AS value\n"
        "{from_sql}"
        "WHERE login = ? AND timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY\n"
        "GROUP BY 1\n"
        "ORDER BY 1;",
    )


def _user_drilldown_capabilities_sql(source_kind: str) -> str:
    return _wrap_activity_query(
        source_kind,
        "SELECT\n"
        "  capability AS label,\n"
        "  COUNT(*) AS value\n"
        "{from_sql}"
        "WHERE login = ? AND timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY\n"
        "GROUP BY 1\n"
        "ORDER BY value DESC;",
    )


def _user_drilldown_categories_sql(source_kind: str) -> str:
    return _wrap_activity_query(
        source_kind,
        "SELECT\n"
        "  dataiku_category AS label,\n"
        "  COUNT(*) AS value\n"
        "{from_sql}"
        "WHERE login = ? AND timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY\n"
        "GROUP BY 1\n"
        "ORDER BY value DESC;",
    )


def _user_drilldown_tags_sql(source_kind: str) -> str:
    return _wrap_activity_query(
        source_kind,
        "SELECT\n"
        "  base_tag AS label,\n"
        "  COUNT(*) AS value\n"
        "{from_sql}"
        "WHERE login = ? AND timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY\n"
        "GROUP BY 1\n"
        "ORDER BY value DESC;",
    )


def _development_capability_summary_sql(source_kind: str) -> str:
    return _wrap_activity_query(
        source_kind,
        "SELECT\n"
        "  COUNT(*) AS events,\n"
        "  COUNT(DISTINCT login) AS users,\n"
        "  COUNT(DISTINCT project_key) AS projects,\n"
        "  COUNT(DISTINCT instance_name) AS instances\n"
        "{from_sql}"
        "WHERE capability = ? AND timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY;",
    )


def _development_capability_daily_sql(source_kind: str) -> str:
    return _wrap_activity_query(
        source_kind,
        "SELECT\n"
        "  CAST(CAST(date_trunc('day', timestamp) AS DATE) AS VARCHAR) AS label,\n"
        "  COUNT(*) AS value\n"
        "{from_sql}"
        "WHERE capability = ? AND timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY\n"
        "GROUP BY 1\n"
        "ORDER BY 1;",
    )


def _development_capability_categories_sql(source_kind: str) -> str:
    return _wrap_activity_query(
        source_kind,
        "SELECT\n"
        "  dataiku_category AS label,\n"
        "  COUNT(*) AS value\n"
        "{from_sql}"
        "WHERE capability = ? AND timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY\n"
        "GROUP BY 1\n"
        "ORDER BY value DESC;",
    )


def _development_capability_tags_sql(source_kind: str) -> str:
    return _wrap_activity_query(
        source_kind,
        "SELECT\n"
        "  base_tag AS label,\n"
        "  COUNT(*) AS value\n"
        "{from_sql}"
        "WHERE capability = ? AND timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY\n"
        "GROUP BY 1\n"
        "ORDER BY value DESC;",
    )


def _development_capability_top_users_sql(source_kind: str) -> str:
    return _wrap_activity_query(
        source_kind,
        "SELECT\n"
        "  login AS label,\n"
        "  COUNT(*) AS value\n"
        "{from_sql}"
        "WHERE capability = ? AND timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY\n"
        "GROUP BY 1\n"
        "ORDER BY value DESC\n"
        "LIMIT 50;",
    )


def _development_user_summary_sql(source_kind: str) -> str:
    return _wrap_activity_query(
        source_kind,
        "SELECT\n"
        "  COUNT(*) AS events,\n"
        "  COUNT(DISTINCT project_key) AS projects,\n"
        "  COUNT(DISTINCT instance_name) AS instances\n"
        "{from_sql}"
        "WHERE login = ? AND timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY;",
    )


def _development_activity_daily_sql(source_kind: str) -> str:
    return _wrap_activity_query(
        source_kind,
        "SELECT\n"
        "  CAST(CAST(date_trunc('day', timestamp) AS DATE) AS VARCHAR) AS label,\n"
        "  COUNT(*) AS value\n"
        "{from_sql}"
        "WHERE timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY\n"
        "GROUP BY 1\n"
        "ORDER BY 1;",
    )


def _development_activity_by_capability_sql(source_kind: str) -> str:
    return _wrap_activity_query(
        source_kind,
        "SELECT\n"
        "  capability AS label,\n"
        "  COUNT(*) AS value\n"
        "{from_sql}"
        "WHERE timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY\n"
        "GROUP BY 1\n"
        "ORDER BY value DESC, label;",
    )


def _development_activity_by_category_sql(source_kind: str) -> str:
    return _wrap_activity_query(
        source_kind,
        "SELECT\n"
        "  concat_ws(' / ', capability, dataiku_category) AS label,\n"
        "  COUNT(*) AS value\n"
        "{from_sql}"
        "WHERE timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY\n"
        "GROUP BY 1\n"
        "ORDER BY value DESC, label;",
    )


def _development_activity_top_users_sql(source_kind: str) -> str:
    return _wrap_activity_query(
        source_kind,
        "SELECT\n"
        "  login AS label,\n"
        "  COUNT(*) AS value\n"
        "{from_sql}"
        "WHERE timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY\n"
        "GROUP BY 1\n"
        "ORDER BY value DESC, label\n"
        "LIMIT 50;",
    )


def _consumption_process_usage_summary_sql(source_kind: str) -> str:
    return _wrap_activity_query(
        source_kind,
        "SELECT\n"
        "  COUNT(*) AS events,\n"
        "  COUNT(DISTINCT capability) AS active_capabilities,\n"
        "  COUNT(DISTINCT login) AS active_users,\n"
        "  COUNT(DISTINCT project_key) AS active_projects,\n"
        "  COUNT(DISTINCT instance_name) AS active_instances\n"
        "{from_sql}"
        "WHERE timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY;",
    )


def _consumption_process_usage_by_capability_sql(source_kind: str) -> str:
    return _wrap_activity_query(
        source_kind,
        "SELECT\n"
        "  capability AS label,\n"
        "  COUNT(*) AS value,\n"
        "  COUNT(DISTINCT login) AS activeUsers,\n"
        "  COUNT(DISTINCT project_key) AS projects,\n"
        "  COUNT(DISTINCT instance_name) AS instances\n"
        "{from_sql}"
        "WHERE timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY\n"
        "GROUP BY 1\n"
        "ORDER BY value DESC, label;",
    )


def _consumption_process_usage_daily_sql(source_kind: str) -> str:
    return _wrap_activity_query(
        source_kind,
        "SELECT\n"
        "  CAST(CAST(date_trunc('day', timestamp) AS DATE) AS VARCHAR) AS label,\n"
        "  COUNT(*) AS value\n"
        "{from_sql}"
        "WHERE timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY\n"
        "GROUP BY 1\n"
        "ORDER BY 1;",
    )


def _consumption_process_usage_top_users_sql(source_kind: str) -> str:
    return _wrap_activity_query(
        source_kind,
        "SELECT\n"
        "  capability AS label,\n"
        "  COUNT(DISTINCT login) AS value\n"
        "{from_sql}"
        "WHERE timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY\n"
        "GROUP BY 1\n"
        "ORDER BY value DESC, label;",
    )


def _consumption_process_usage_top_projects_sql(source_kind: str) -> str:
    return _wrap_activity_query(
        source_kind,
        "SELECT\n"
        "  capability AS label,\n"
        "  COUNT(DISTINCT project_key) AS value\n"
        "{from_sql}"
        "WHERE timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY\n"
        "GROUP BY 1\n"
        "ORDER BY value DESC, label;",
    )


def _consumption_process_usage_top_instances_sql(source_kind: str) -> str:
    return _wrap_activity_query(
        source_kind,
        "SELECT\n"
        "  capability AS label,\n"
        "  COUNT(DISTINCT instance_name) AS value\n"
        "{from_sql}"
        "WHERE timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY\n"
        "GROUP BY 1\n"
        "ORDER BY value DESC, label;",
    )


def _consumption_capability_summary_sql(source_kind: str) -> str:
    return _wrap_activity_query(
        source_kind,
        "SELECT\n"
        "  COUNT(*) AS events,\n"
        "  COUNT(DISTINCT login) AS users,\n"
        "  COUNT(DISTINCT project_key) AS projects,\n"
        "  COUNT(DISTINCT instance_name) AS instances\n"
        "{from_sql}"
        "WHERE capability = ? AND timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY;",
    )


def _consumption_capability_daily_sql(source_kind: str) -> str:
    return _wrap_activity_query(
        source_kind,
        "SELECT\n"
        "  CAST(CAST(date_trunc('day', timestamp) AS DATE) AS VARCHAR) AS label,\n"
        "  COUNT(*) AS value\n"
        "{from_sql}"
        "WHERE capability = ? AND timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY\n"
        "GROUP BY 1\n"
        "ORDER BY 1;",
    )


def _consumption_capability_top_users_sql(source_kind: str) -> str:
    return _wrap_activity_query(
        source_kind,
        "SELECT\n"
        "  login AS label,\n"
        "  COUNT(*) AS value\n"
        "{from_sql}"
        "WHERE capability = ? AND timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY\n"
        "GROUP BY 1\n"
        "ORDER BY value DESC\n"
        "LIMIT 50;",
    )


def _consumption_capability_top_projects_sql(source_kind: str) -> str:
    return _wrap_activity_query(
        source_kind,
        "SELECT\n"
        "  concat_ws(':', instance_name, project_key) AS label,\n"
        "  COUNT(*) AS value\n"
        "{from_sql}"
        "WHERE capability = ? AND timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY\n"
        "GROUP BY 1\n"
        "ORDER BY value DESC\n"
        "LIMIT 50;",
    )


def _development_activity_source_sql(_query_df) -> str:
    if _duckdb_relation_exists(_query_df, "final_build_development_activity_events"):
        return "final_build_development_activity_events"
    return (
        "(\n"
        "  SELECT\n"
        "    e.timestamp,\n"
        "    e.instance_name,\n"
        "    e.login,\n"
        "    e.project_key,\n"
        "    e.msgtype,\n"
        "    e.msgtypebase AS base_tag,\n"
        "    COALESCE(m.category_display_name, e.dataiku_category) AS dataiku_category,\n"
        "    COALESCE(m.capability_display_name, m.capability, 'Uncategorized') AS capability\n"
        "  FROM fact_dev_activity_events e\n"
        "  LEFT JOIN dim_category_to_capability m\n"
        "    ON lower(trim(m.dataiku_category)) = lower(trim(e.dataiku_category))\n"
        ")"
    )

def _metadata_completeness_sql(*, name_col: str, key_col: str, owner_col: str, updated_col: str) -> tuple[str, str]:
    score_sql = (
        "("
        f"CASE WHEN {name_col} IS NOT NULL AND length(trim(CAST({name_col} AS VARCHAR))) > 0 THEN 1 ELSE 0 END + "
        f"CASE WHEN {key_col} IS NOT NULL AND length(trim(CAST({key_col} AS VARCHAR))) > 0 THEN 1 ELSE 0 END + "
        f"CASE WHEN {owner_col} IS NOT NULL AND length(trim(CAST({owner_col} AS VARCHAR))) > 0 THEN 1 ELSE 0 END + "
        f"CASE WHEN {updated_col} IS NOT NULL THEN 1 ELSE 0 END"
        ")"
    )
    status_sql = f"CASE WHEN {score_sql} = 4 THEN 'complete' WHEN {score_sql} >= 2 THEN 'partial' ELSE 'sparse' END"
    return score_sql, status_sql


def _non_empty_sql(column_sql: str) -> str:
    return f"CASE WHEN {column_sql} IS NOT NULL AND length(trim(CAST({column_sql} AS VARCHAR))) > 0 THEN 1 ELSE 0 END"


def _non_empty_value_sql(column_sql: str) -> str:
    return f"NULLIF(trim(CAST({column_sql} AS VARCHAR)), '')"


def _high_signal_product_name_sql(column_sql: str) -> str:
    return (
        "CASE "
        f"WHEN {column_sql} IS NULL THEN NULL "
        f"WHEN length(trim(CAST({column_sql} AS VARCHAR))) < 5 THEN NULL "
        f"WHEN lower(trim(CAST({column_sql} AS VARCHAR))) IN ('view', 'demo', 'test', 'dash', 'prez') THEN NULL "
        f"ELSE trim(CAST({column_sql} AS VARCHAR)) END"
    )


def _non_empty_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return bool(str(value).strip())


def _metadata_status_from_score(score: float) -> str:
    if score >= 99.9:
        return "complete"
    if score >= 50.0:
        return "partial"
    return "sparse"


def _asset_inventory_metadata_score(row: dict[str, Any]) -> float:
    checks = [
        _non_empty_value(row.get("objectName")),
        _non_empty_value(row.get("objectKey")),
        _non_empty_value(row.get("ownerLogin")),
        row.get("createdAt") is not None,
        row.get("updatedAt") is not None,
    ]
    if row.get("objectType") in {"dataset", "recipe", "scenario"}:
        checks.insert(2, _non_empty_value(row.get("objectSubtype")))
    return 100.0 * (sum(1 for ok in checks if ok) / len(checks)) if checks else 0.0


def _product_inventory_metadata_score(row: dict[str, Any]) -> float:
    checks = [
        _non_empty_value(row.get("productName")),
        _non_empty_value(row.get("productKey")),
        _non_empty_value(row.get("productType")),
        _non_empty_value(row.get("ownerLogin")),
        row.get("createdAt") is not None,
        row.get("updatedAt") is not None,
    ]
    return 100.0 * (sum(1 for ok in checks if ok) / len(checks)) if checks else 0.0


def _metadata_completeness_sql_for_asset_type() -> tuple[str, str]:
    score_sql = (
        "CASE\n"
        "  WHEN object_type = 'project' THEN 20 * ("
        + " + ".join(
            [
                _non_empty_sql("object_name"),
                _non_empty_sql("object_key"),
                _non_empty_sql("owner_login"),
                "CASE WHEN created_at IS NOT NULL THEN 1 ELSE 0 END",
                "CASE WHEN updated_at IS NOT NULL THEN 1 ELSE 0 END",
            ]
        )
        + ")\n"
        "  WHEN object_type IN ('dataset', 'recipe', 'scenario') THEN 100.0 * (("
        + " + ".join(
            [
                _non_empty_sql("object_name"),
                _non_empty_sql("object_key"),
                _non_empty_sql("object_subtype"),
                _non_empty_sql("owner_login"),
                "CASE WHEN created_at IS NOT NULL THEN 1 ELSE 0 END",
                "CASE WHEN updated_at IS NOT NULL THEN 1 ELSE 0 END",
            ]
        )
        + ") / 6.0)\n"
        "  ELSE 25 * ("
        + " + ".join(
            [
                _non_empty_sql("object_name"),
                _non_empty_sql("object_key"),
                _non_empty_sql("owner_login"),
                "CASE WHEN updated_at IS NOT NULL THEN 1 ELSE 0 END",
            ]
        )
        + ")\n"
        "END"
    )
    status_sql = (
        f"CASE WHEN ({score_sql}) >= 99.9 THEN 'complete' "
        f"WHEN ({score_sql}) >= 50 THEN 'partial' ELSE 'sparse' END"
    )
    return score_sql, status_sql


def _metadata_completeness_sql_for_product_type() -> tuple[str, str]:
    score_sql = (
        "100.0 * (("
        + " + ".join(
            [
                _non_empty_sql("product_name"),
                _non_empty_sql("product_key"),
                _non_empty_sql("product_type"),
                _non_empty_sql("owner_login"),
                "CASE WHEN created_at IS NOT NULL THEN 1 ELSE 0 END",
                "CASE WHEN updated_at IS NOT NULL THEN 1 ELSE 0 END",
            ]
        )
        + ") / 6.0)"
    )
    status_sql = (
        f"CASE WHEN ({score_sql}) >= 99.9 THEN 'complete' "
        f"WHEN ({score_sql}) >= 50 THEN 'partial' ELSE 'sparse' END"
    )
    return score_sql, status_sql


def _metadata_summary_payload_from_queries(_query_df, *, summary_sql: str, by_type_sql: str) -> dict[str, Any]:
    summary_df = _query_df(summary_sql)
    summary = _df_records(summary_df)[0] if len(summary_df.index) else {}
    total_assets = int(summary.get("total_assets") or 0)
    by_type_df = _query_df(by_type_sql)

    return {
        "summary": {
            "totalAssets": total_assets,
            "avgScore": float(summary.get("avg_score") or 0.0),
            "completeCount": int(summary.get("complete_count") or 0),
            "partialCount": int(summary.get("partial_count") or 0),
            "sparseCount": int(summary.get("sparse_count") or 0),
            "completeRate": ((int(summary.get("complete_count") or 0) / total_assets) if total_assets else 0.0),
            "sparseRate": ((int(summary.get("sparse_count") or 0) / total_assets) if total_assets else 0.0),
        },
        "byType": _df_records(by_type_df),
    }


_startup_init_lock = threading.Lock()
_startup_init_started = False
_startup_check_completed = False
_backend_started_at = time.time()
_startup_init_status: dict[str, Any] = {
    "state": "idle",
    "normalizedState": "NOT_STARTED",
    "retryAllowed": True,
    "message": "Waiting to check DuckDB startup state",
    "phase": "idle",
    "startedAt": None,
    "finishedAt": None,
    "durationSec": None,
    "backendStartedAt": _backend_started_at,
    "dbPath": None,
    "metadataPath": None,
    "dbMtime": None,
    "currentFileNumber": None,
    "totalFileCount": None,
    "startupCheckPerformed": False,
    "stale": False,
    "staleReason": None,
    "rebuildTriggeredBy": None,
    "report": None,
    "error": None,
}


def _is_backend_local_timeout_error(exc: BaseException) -> bool:
    message = str(exc or "")
    lowered = message.lower()
    return any(
        token in lowered
        for token in (
            "timed out",
            "timeout",
            "read timed out",
            "bad gateway",
            "502",
        )
    )


def _update_startup_init_message(message: str) -> None:
    _startup_init_status["message"] = str(message)


def _update_startup_init_phase(phase: str, message: str) -> None:
    _startup_init_status["phase"] = str(phase)
    _startup_init_status["message"] = str(message)


def _normalized_startup_state(state: str | None) -> str:
    value = str(state or "").strip().lower()
    if value == "ready":
        return "READY"
    if value in {"running", "initializing"}:
        return "INITIALIZING"
    if value in {"failed", "unavailable"}:
        return "FAILED"
    return "NOT_STARTED"


def _refresh_startup_status_metadata() -> None:
    normalized_state = _normalized_startup_state(_startup_init_status.get("state"))
    _startup_init_status["normalizedState"] = normalized_state
    _startup_init_status["retryAllowed"] = normalized_state in {"NOT_STARTED", "FAILED"}

    report = _startup_init_status.get("report")
    if isinstance(report, dict):
        total = report.get("total")
        if total is None:
            loaded = report.get("loaded")
            failed = report.get("failed")
            if isinstance(loaded, list) or isinstance(failed, list):
                total = len(loaded or []) + len(failed or [])
        loaded_count = report.get("loadedCount")
        if loaded_count is None:
            loaded = report.get("loaded")
            if isinstance(loaded, list):
                loaded_count = len(loaded)
        _startup_init_status["totalFileCount"] = int(total) if isinstance(total, int) else None
        _startup_init_status["currentFileNumber"] = int(loaded_count) if isinstance(loaded_count, int) else None
    else:
        _startup_init_status["totalFileCount"] = None
        _startup_init_status["currentFileNumber"] = None


class RequestValidationError(ValueError):
    """Raised when request inputs are present but invalid."""


# This backend runs in two contexts:
# - In Dataiku DSS: the Standard webapp backend runner injects a Flask `app`.
#   In that case, DO NOT create a new Flask app here, just register routes.
# - In local development: you may run this module directly and we create a Flask app.

# Shared dashboard backend logic lives under python-lib to keep the webapp folder small.
# In DSS, python-lib is automatically available; in local dev we add it to sys.path.
try:
    from pulse_dashboard import settings as pulse_settings  # type: ignore
    from pulse_dashboard.pulse_duckdb.engine import ReadOnlySQLError, create_connection, ensure_database_ready, is_initialization_in_progress, query_df  # type: ignore
    from pulse_dashboard.pulse_duckdb.engine.init_db import ensure_consumption_product_views, read_duckdb_metadata  # type: ignore
except Exception:
    try:
        repo_root = Path(__file__).resolve().parents[2]
        python_lib = repo_root / "python-lib"
        if python_lib.is_dir():
            sys.path.insert(0, str(python_lib))

        from pulse_dashboard import settings as pulse_settings  # type: ignore
        from pulse_dashboard.pulse_duckdb.engine import ReadOnlySQLError, create_connection, ensure_database_ready, is_initialization_in_progress, query_df  # type: ignore
        from pulse_dashboard.pulse_duckdb.engine.init_db import ensure_consumption_product_views, read_duckdb_metadata  # type: ignore
    except Exception:
        logger.exception("Failed to import Pulse dashboard libraries")
        pulse_settings = None
        create_connection = None
        ensure_database_ready = None
        is_initialization_in_progress = None
        query_df = None
        read_duckdb_metadata = None
        ReadOnlySQLError = None  # type: ignore[assignment,misc]

if pulse_settings is not None:
    setattr(pulse_settings, "PULSE_INIT_STATUS_CALLBACK", _update_startup_init_phase)
    setattr(pulse_settings, "PULSE_BACKEND_STARTED_AT", _backend_started_at)


@bp.route("/__ping", endpoint="pulse_dashboard_ping")
def pulse_dashboard_ping():
    return "OK"


@bp.route("/api/status")
def status():
    return jsonify({"status": "Online", "msg": "Backend is running"})


def _read_standard_project_variables() -> dict[str, Any]:
    """Best-effort read of DSS project `standard` variables.

    In local dev runs (outside DSS), this returns an empty dict.
    """

    try:
        import dataiku

        client = dataiku.api_client()
        project = client.get_project(dataiku.default_project_key())
        vars_ = project.get_variables() or {}
        standard = vars_.get("standard") or {}
        return standard if isinstance(standard, dict) else {}
    except Exception:
        return {}


def _read_license_groups() -> dict[str, list[str]]:
    """Read plugin-owned license grouping config from terminology.yaml.

    Expected keys:
    - license_creator
    - license_consumer
    - license_admin

    Any user profile not explicitly mapped into one of the above groups is
    treated as `license_other` by downstream consumers.
    """

    path = Path(__file__).resolve().parents[1] / "configs" / "terminology.yaml"
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        doc = {}

    groups = doc.get("license_groups") if isinstance(doc, dict) else None
    if not isinstance(groups, dict):
        groups = {}

    def _list(name: str) -> list[str]:
        value = groups.get(name, [])
        if not isinstance(value, list):
            return []
        return [str(item).strip().upper() for item in value if str(item).strip()]

    return {
        "license_creator": _list("license_creator"),
        "license_consumer": _list("license_consumer"),
        "license_admin": _list("license_admin"),
    }


def _read_user_profile_exclude_consumer(_standard_vars: dict[str, Any]) -> list[str]:
    """Profiles excluded by the `no_consumer` license filter.

    This is derived from the plugin-owned `license_consumer` group defined in
    `pulse_dashboard/configs/terminology.yaml`.
    """

    groups = _read_license_groups()
    return groups.get("license_consumer", []) or ["READER", "AI_CONSUMER"]


_WINDOW_TO_MONTHS = {
    "this_month": 1,
    "last_3_months": 3,
    "last_12_months": 12,
}


def _parse_window_months(value: str | None) -> int | None:
    if not value:
        return None
    value = str(value).strip().lower()
    months = _WINDOW_TO_MONTHS.get(value)
    return int(months) if months else None


def _parse_license_filter(value: str | None) -> str:
    value = (value or "").strip().lower()
    allowed = {
        "all_enabled",
        "no_consumer",
        "license_creator",
        "license_consumer",
        "license_admin",
        "license_other",
    }
    aliases = {
        "exclude_consumer": "no_consumer",
        "exclude-consumer": "no_consumer",
        "non_consumer": "no_consumer",
        "non-consumer": "no_consumer",
        "exclude_readers": "no_consumer",
    }
    normalized = aliases.get(value, value)
    return normalized if normalized in allowed else "all_enabled"


def _parse_activity_filter(value: str | None) -> str:
    value = (value or "").strip().lower()
    allowed = {"license_creator", "license_consumer"}
    aliases = {
        "creator": "license_creator",
        "creators": "license_creator",
        "consumer": "license_consumer",
        "consumers": "license_consumer",
    }
    normalized = aliases.get(value, value)
    return normalized if normalized in allowed else "license_creator"


def _resolve_license_filter_clause(license_filter: str) -> tuple[str, list[str]]:
    groups = _read_license_groups()
    creator = groups.get("license_creator", [])
    consumer = groups.get("license_consumer", [])
    admin = groups.get("license_admin", [])
    known = sorted({*creator, *consumer, *admin})

    if license_filter == "no_consumer":
        if not consumer:
            return "", []
        placeholders = _sql_placeholders(len(consumer))
        return f" AND coalesce(upper(trim({{profile_expr}})), '') NOT IN ({placeholders})", list(consumer)

    if license_filter in {"license_creator", "license_consumer", "license_admin"}:
        target = groups.get(license_filter, [])
        if not target:
            return " AND 1 = 0", []
        placeholders = _sql_placeholders(len(target))
        return f" AND coalesce(upper(trim({{profile_expr}})), '') IN ({placeholders})", list(target)

    if license_filter == "license_other":
        if not known:
            return "", []
        placeholders = _sql_placeholders(len(known))
        return f" AND coalesce(upper(trim({{profile_expr}})), '') NOT IN ({placeholders})", list(known)

    return "", []


def _format_license_filter_clause(template: str, *, profile_expr: str) -> str:
    return template.format(profile_expr=profile_expr) if template else ""

def _license_group_case_sql(profile_expr: str) -> str:
    groups = _read_license_groups()
    creator = groups.get("license_creator", [])
    consumer = groups.get("license_consumer", [])
    admin = groups.get("license_admin", [])

    clauses: list[str] = []
    if creator:
        clauses.append(
            f"WHEN coalesce(upper(trim({profile_expr})), '') IN ({_sql_string_literals(creator)}) THEN 'Creator Licenses'"
        )
    if consumer:
        clauses.append(
            f"WHEN coalesce(upper(trim({profile_expr})), '') IN ({_sql_string_literals(consumer)}) THEN 'Consumer Licenses'"
        )
    if admin:
        clauses.append(
            f"WHEN coalesce(upper(trim({profile_expr})), '') IN ({_sql_string_literals(admin)}) THEN 'Admin Licenses'"
        )

    if not clauses:
        return "'Other Licenses'"

    when_sql = "\n      ".join(clauses)
    return "CASE\n      " + when_sql + "\n      ELSE 'Other Licenses'\n    END"


def _license_profile_normalize_sql(profile_expr: str) -> str:
    return f"upper(regexp_replace(coalesce(trim({profile_expr}), 'UNKNOWN'), '[^A-Za-z0-9]+', '', 'g'))"


def _truthy_license_feature(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    normalized = str(value).strip().lower()
    return normalized in {"true", "t", "1", "yes", "y", "enabled"}


def _license_status_display_value(field_name: str, raw_value: Any) -> str | None:
    if raw_value is None:
        return None

    text = str(raw_value).strip()
    if not text:
        return None

    if field_name in {"valid", "expired", "has_license", "community"}:
        lowered = text.lower()
        if lowered in {"true", "t", "1", "yes", "y"}:
            return "True"
        if lowered in {"false", "f", "0", "no", "n"}:
            return "False"
        return text

    if field_name == "expires_on":
        if text.isdigit():
            try:
                ts = int(text)
                if ts > 10_000_000_000:
                    ts = ts // 1000
                return time.strftime("%Y-%m-%d", time.gmtime(ts))
            except Exception:
                return text
        return text

    if field_name == "emitted_on":
        if len(text) == 8 and text.isdigit():
            return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
        return text

    return text


def _addon_service_label(addon_key: Any) -> str:
    text = str(addon_key or "").strip()
    if not text:
        return "Unknown Add-on"

    custom_labels = {
        "advancedGovern": "Advanced Govern",
        "advancedLLMMesh": "Advanced LLM Mesh",
        "stories": "Stories",
    }
    if text in custom_labels:
        return custom_labels[text]

    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    spaced = spaced.replace("_", " ").replace("-", " ")
    return " ".join(part.capitalize() for part in spaced.split())




def _require_debug_access() -> None:
    if _IS_LOCAL_DEV:
        return
    if not _has_administration_access():
        raise PermissionError("Administration access is required.")


def _require_organization_access() -> None:
    if _IS_LOCAL_DEV:
        return
    if not _has_organization_access():
        raise PermissionError("Organization access is required.")


def _handle_request_errors(route_name: str):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except RequestValidationError as exc:
                return _err(str(exc), status=400)
            except PermissionError as exc:
                return _err(str(exc), status=403)
            except Exception as exc:
                logger.exception("%s failed", route_name)
                return _err(str(exc), status=500)

        return wrapper

    return decorator


def _parse_int_arg(
    name: str,
    *,
    default: int | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
    required: bool = False,
) -> int | None:
    raw = request.args.get(name)
    if raw is None or str(raw).strip() == "":
        if required:
            raise RequestValidationError(f"Missing query parameter '{name}'")
        return default

    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise RequestValidationError(f"Invalid integer for '{name}'") from exc

    if minimum is not None and value < minimum:
        raise RequestValidationError(f"'{name}' must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise RequestValidationError(f"'{name}' must be <= {maximum}")
    return value


def _parse_pagination(*, default_limit: int = 25, max_limit: int = _MAX_PAGE_LIMIT) -> tuple[int, int]:
    limit = _parse_int_arg("limit", default=default_limit, minimum=1, maximum=max_limit)
    offset = _parse_int_arg("offset", default=0, minimum=0)
    return int(limit or default_limit), int(offset or 0)


def _parse_days_arg(*, default: int = 30, maximum: int = _MAX_LOOKBACK_DAYS) -> int:
    return int(_parse_int_arg("days", default=default, minimum=1, maximum=maximum) or default)


def _resolve_window_params(
    *,
    default_days: int = 30,
    max_days: int = _MAX_LOOKBACK_DAYS,
    max_months: int = _MAX_LOOKBACK_MONTHS,
) -> tuple[int | None, int | None]:
    window = request.args.get("window")
    months = _parse_window_months(window)
    if window and months is None:
        allowed = ", ".join(sorted(_WINDOW_TO_MONTHS))
        raise RequestValidationError(f"Invalid 'window'. Expected one of: {allowed}")

    if months is not None:
        return months, None

    explicit_months = _parse_int_arg("months", default=None, minimum=1, maximum=max_months)
    if explicit_months is not None:
        return explicit_months, None

    return None, _parse_days_arg(default=default_days, maximum=max_days)




@bp.route("/api/startup/flags")
def startup_flags():
    """Feature flags + small config exposed to the React SPA.

    Flags are read from DSS project standard variables so features can be enabled
    per project.

    Currently supported flags:
    - `userActivity`: always enabled.

    Config values:
    - `userProfileExcludeConsumer`: resolved exclusion list for the `no_consumer` toggle,
      read from `pulse_dashboard/configs/terminology.yaml` `license_groups.license_consumer`.

    Note:
    - If we cannot resolve the default project / variables, we default to disabled.
    """

    try:
        standard = _read_standard_project_variables()
        user_activity_enabled = True
        advanced_llm_mesh_capability = get_advanced_llm_mesh_capability()
        excluded_profiles = _read_user_profile_exclude_consumer(standard)
        return _ok(
            {
                "flags": {
                    "userActivity": user_activity_enabled,
                    "llmMesh": advanced_llm_mesh_capability.get("enabled") is True,
                },
                "capabilities": {
                    "advancedLLMMesh": advanced_llm_mesh_capability,
                },
                "config": {
                    "userProfileExcludeConsumer": excluded_profiles,
                    "licenseGroups": _read_license_groups(),
                },
            }
        )
    except Exception:
        logger.exception("Failed reading startup flags")
        return _ok(
            {
                "flags": {"userActivity": True, "llmMesh": False},
                "capabilities": {"advancedLLMMesh": dict(_ADVANCED_LLM_MESH_DISABLED_CAPABILITY)},
                "config": {"userProfileExcludeConsumer": ["READER", "AI_CONSUMER"], "licenseGroups": {"license_creator": [], "license_consumer": ["READER", "AI_CONSUMER"], "license_admin": []}},
            }
        )


@bp.route("/api/startup/duckdb")
def startup_duckdb():
    """Compatibility startup endpoint that only schedules background init."""

    if ensure_database_ready is None:
        return _err("pulse_duckdb not available", status=500)

    _maybe_schedule_startup_duckdb_init()

    init = dict(_startup_init_status)
    state = str(init.get("state") or "idle")
    pending = state in {"idle", "running"}
    duration_sec = init.get("durationSec")
    if pending and duration_sec is None:
        started_at = init.get("startedAt")
        if started_at is not None:
            try:
                duration_sec = round(time.time() - float(started_at), 3)
            except Exception:
                duration_sec = None
            init["durationSec"] = duration_sec

    if state == "failed":
        return _err(
            "DuckDB initialization failed",
            status=500,
            hint=json.dumps({"durationSec": duration_sec, "load": init.get("report"), "error": init.get("error")}),
        )

    return _ok(
        {
            "load": {
                "ok": state == "ready",
                "pending": pending,
                "message": init.get("message"),
                "state": state,
                "report": init.get("report"),
            },
            "durationSec": duration_sec,
            "pending": pending,
            "init": init,
        }
    )


@bp.route("/api/startup/status")
def startup_status():
    """Read-only health snapshot for the DuckDB-backed dashboard.

    Important: this endpoint should not *create* the DB. It only inspects the
    DuckDB file if it already exists.
    """

    duckdb_path = None
    if pulse_settings is not None:
        duckdb_path = str(getattr(pulse_settings, "DUCKDB_PATH", "") or "")

    exists = False
    size_bytes = None
    if duckdb_path:
        try:
            p = Path(duckdb_path)
            exists = p.exists()
            if exists:
                size_bytes = p.stat().st_size
        except Exception:
            exists = False

    expected_objects = [
        "final_build_catalog",
        "final_build_products_catalog",
        "final_build_development_activity_events",
    ]

    tables: list[str] = []
    present_expected: list[str] = []
    missing_expected: list[str] = []

    if exists and create_connection is not None:
        if _duckdb_init_in_progress():
            missing_expected = list(expected_objects)
            return _ok(
                {
                    "duckdb": {
                        "path": duckdb_path,
                        "exists": exists,
                        "sizeBytes": size_bytes,
                        "initializing": True,
                    },
                    "ready": False,
                    "expected": {"present": present_expected, "missing": missing_expected},
                    "tables": tables,
                }
            )
        try:
            conn = create_connection(read_only=True)
            try:
                rows = conn.execute("PRAGMA show_tables;").fetchall()
                tables = sorted([str(r[0]) for r in rows])
            finally:
                conn.close()

            present_expected = [t for t in expected_objects if t in set(tables)]
            missing_expected = [t for t in expected_objects if t not in set(tables)]
        except Exception as e:
            # If we can't open the DB read-only, treat as not-ready.
            missing_expected = list(expected_objects)
            return _ok(
                {
                    "duckdb": {
                        "path": duckdb_path,
                        "exists": exists,
                        "sizeBytes": size_bytes,
                        "openError": str(e),
                    },
                    "ready": False,
                    "expected": {"present": present_expected, "missing": missing_expected},
                    "tables": tables,
                }
            )

    ready = bool(exists and not missing_expected)

    return _ok(
        {
            "duckdb": {"path": duckdb_path, "exists": exists, "sizeBytes": size_bytes},
            "ready": ready,
            "expected": {"present": present_expected, "missing": missing_expected},
            "tables": tables,
        }
    )


def _run_startup_duckdb_init() -> None:
    global _startup_check_completed

    if ensure_database_ready is None:
        _startup_init_status.update(
            {
                "state": "unavailable",
                "phase": "unavailable",
                "message": "DuckDB engine unavailable",
                "finishedAt": time.time(),
                "error": "DuckDB engine unavailable",
            }
        )
        _refresh_startup_status_metadata()
        logger.warning("Pulse webapp startup init skipped: DuckDB engine unavailable")
        _startup_check_completed = True
        return

    try:
        started_at = time.time()
        _startup_init_status.update(
            {
                "state": "running",
                "phase": "bootstrap",
                "message": "Initializing DuckDB and loading GOLD tables",
                "startedAt": started_at,
                "finishedAt": None,
                "durationSec": None,
                "error": None,
                "report": None,
            }
        )
        _refresh_startup_status_metadata()
        logger.info("Pulse webapp startup: initializing DuckDB in background")
        report = cast(
            dict[str, Any],
            ensure_database_ready(
                load_gold_tables=True,
                replace_gold_tables=getattr(pulse_settings, "PULSE_AUTO_LOAD_REPLACE", False)
                if pulse_settings is not None
                else False,
            ),
        )
        finished_at = time.time()
        duration_sec = round(finished_at - started_at, 3)
        if bool(report.get("ok", False)):
            _startup_init_status.update(
                {
                    "state": "ready",
                    "phase": "frontend_ready",
                    "message": "DuckDB initialization complete",
                    "finishedAt": finished_at,
                    "durationSec": duration_sec,
                    "report": report,
                }
            )
            _refresh_startup_status_metadata()
            logger.info("Pulse webapp startup: DuckDB initialization finished in %ss", duration_sec)
        else:
            _startup_init_status.update(
                {
                    "state": "failed",
                    "phase": "failed",
                    "message": "DuckDB initialization reported a failure",
                    "finishedAt": finished_at,
                    "durationSec": duration_sec,
                    "report": report,
                    "error": json.dumps(report),
                }
            )
            _refresh_startup_status_metadata()
            logger.warning(
                "Pulse webapp startup: DuckDB initialization reported failure after %ss: %s",
                duration_sec,
                report,
            )
        _startup_check_completed = True
    except Exception:
        finished_at = time.time()
        duration_sec = None
        if _startup_init_status.get("startedAt") is not None:
            try:
                duration_sec = round(finished_at - float(_startup_init_status["startedAt"]), 3)
            except Exception:
                duration_sec = None
        _startup_init_status.update(
            {
                "state": "failed",
                "phase": "failed",
                "message": "DuckDB initialization failed",
                "finishedAt": finished_at,
                "durationSec": duration_sec,
                "error": "DuckDB initialization failed. Check backend logs.",
            }
        )
        _refresh_startup_status_metadata()
        logger.exception("Pulse webapp startup: DuckDB initialization failed")
        _startup_check_completed = True


def _safe_duckdb_metadata() -> dict[str, Any]:
    if read_duckdb_metadata is None:
        return {}
    try:
        payload = cast(dict[str, Any], read_duckdb_metadata())
    except Exception:
        logger.warning("Pulse webapp startup: failed reading DuckDB metadata", exc_info=True)
        return {}
    return payload if isinstance(payload, dict) else {}


def _evaluate_startup_duckdb_state(duckdb_path: Path) -> dict[str, Any]:
    metadata_path = Path(getattr(pulse_settings, "DUCKDB_METADATA_PATH", f"{duckdb_path}.meta.json"))
    metadata = _safe_duckdb_metadata()
    exists = duckdb_path.exists()
    db_mtime = duckdb_path.stat().st_mtime if exists else None
    tolerance_sec = float(getattr(pulse_settings, "PULSE_DUCKDB_STARTUP_STALE_TOLERANCE_SEC", 5.0) or 0.0)

    stale = False
    stale_reason = "missing"
    if exists:
        stale_reason = None
        rebuild_on_restart = bool(getattr(pulse_settings, "PULSE_DUCKDB_REBUILD_ON_STARTUP_STALE", True))
        if rebuild_on_restart and db_mtime is not None and db_mtime < (_backend_started_at - tolerance_sec):
            stale = True
            stale_reason = "older_than_backend_start"

    return {
        "exists": exists,
        "dbMtime": db_mtime,
        "metadataPath": str(metadata_path),
        "metadata": metadata,
        "stale": stale if exists else True,
        "staleReason": stale_reason,
    }


def _delete_stale_duckdb(duckdb_path: Path, metadata_path: Path) -> None:
    if duckdb_path.exists():
        duckdb_path.unlink()
    if metadata_path.exists():
        metadata_path.unlink()


def _maybe_schedule_startup_duckdb_init() -> None:
    global _startup_check_completed, _startup_init_started

    if pulse_settings is None or ensure_database_ready is None:
        _startup_init_status.update(
            {
                "state": "unavailable",
                "phase": "unavailable",
                "message": "DuckDB settings unavailable",
                "error": "DuckDB settings unavailable",
            }
        )
        _refresh_startup_status_metadata()
        return

    duckdb_path = Path(getattr(pulse_settings, "DUCKDB_PATH", "") or "")
    if not duckdb_path:
        _startup_init_status.update(
            {
                "state": "unavailable",
                "phase": "unavailable",
                "message": "DuckDB path is not configured",
                "error": "DuckDB path is not configured",
            }
        )
        return
    _startup_init_status["dbPath"] = str(duckdb_path)
    startup_db_state = _evaluate_startup_duckdb_state(duckdb_path)
    _startup_init_status.update(
        {
            "metadataPath": startup_db_state.get("metadataPath"),
            "dbMtime": startup_db_state.get("dbMtime"),
            "startupCheckPerformed": True,
            "stale": bool(startup_db_state.get("stale", False)),
            "staleReason": startup_db_state.get("staleReason"),
        }
    )

    if _startup_check_completed:
        return

    if startup_db_state.get("exists") and not startup_db_state.get("stale"):
        _startup_init_status.update(
            {
                "state": "ready",
                "phase": "frontend_ready",
                "message": "DuckDB file already present and fresh for this backend",
                "finishedAt": time.time(),
                "durationSec": 0.0,
                "error": None,
            }
        )
        _refresh_startup_status_metadata()
        _startup_check_completed = True
        return

    if startup_db_state.get("exists") and startup_db_state.get("stale"):
        try:
            _delete_stale_duckdb(duckdb_path, Path(str(startup_db_state.get("metadataPath") or f"{duckdb_path}.meta.json")))
            _startup_init_status["rebuildTriggeredBy"] = "startup_stale"
            logger.info(
                "Pulse webapp startup: deleted stale DuckDB at %s because %s",
                duckdb_path,
                startup_db_state.get("staleReason"),
            )
        except Exception as exc:
            _startup_init_status.update(
                {
                    "state": "failed",
                    "phase": "failed",
                    "message": "Failed deleting stale DuckDB before startup rebuild",
                    "finishedAt": time.time(),
                    "error": str(exc),
                    "stale": True,
                    "staleReason": "delete_failed",
                }
            )
            _startup_check_completed = True
            logger.exception("Pulse webapp startup: failed deleting stale DuckDB at %s", duckdb_path)
            return
    else:
        _startup_init_status["rebuildTriggeredBy"] = "missing"

    with _startup_init_lock:
        if _startup_init_started:
            return
        _startup_init_started = True

    logger.info("Pulse webapp startup: DuckDB missing at %s; initializing now", duckdb_path)
    thread = threading.Thread(target=_run_startup_duckdb_init, name="pulse-duckdb-startup-init", daemon=True)
    thread.start()


@bp.route("/api/startup/init-status")
def startup_init_status():
    if not bool(_startup_init_status.get("startupCheckPerformed")) and not _startup_check_completed:
        _maybe_schedule_startup_duckdb_init()
    _refresh_startup_status_metadata()
    return _ok({"init": dict(_startup_init_status)})


# ----------------------------------------------------------------------------
# Static frontend (local dev convenience)
# ----------------------------------------------------------------------------
# In DSS, the HTML/JS are handled by `body.html` + `app.js`.
# For local runs (gunicorn/flask), we serve `body.html` too so local dev mirrors
# the DSS loader behavior (warmup + manifest-driven asset injection).


@bp.route("/resource/pulse-dashboard/build/<path:filename>")
def serve_packaged_build(filename: str):  # pragma: no cover
    if not _BUILD_DIR.is_dir():
        return (
            "Pulse dashboard build not found. Expected: "
            f"{_BUILD_DIR}. Run scripts/webapp/sync_pulse_dashboard_build.sh to populate it.",
            404,
        )

    filename = (filename or "").lstrip("/")
    candidate = (_BUILD_DIR / filename).resolve()

    try:
        candidate.relative_to(_BUILD_DIR)
    except ValueError:
        return _err("Invalid path", status=400)

    if not candidate.exists() or not candidate.is_file():
        return _err("Not found", status=404)

    return send_from_directory(_BUILD_DIR, filename)


@bp.route("/", defaults={"path": ""})
@bp.route("/<path:path>")
def serve_frontend(path: str):  # pragma: no cover
    # Mirror DSS by serving the same loader HTML.
    if _IS_LOCAL_DEV:
        return send_file(_BODY_HTML_PATH)

    # Fallback: if someone hits this backend outside DSS, try serving the
    # packaged React build directly.
    if not _BUILD_DIR.is_dir():
        return (
            "Pulse dashboard build not found. Expected: "
            f"{_BUILD_DIR}. Run scripts/webapp/sync_pulse_dashboard_build.sh to populate it.",
            404,
        )

    path = (path or "").lstrip("/")
    candidate = (_BUILD_DIR / path).resolve()

    # Avoid path traversal outside build dir.
    try:
        candidate.relative_to(_BUILD_DIR)
    except ValueError:
        return _err("Invalid path", status=400)

    if path and candidate.exists() and candidate.is_file():
        return send_from_directory(_BUILD_DIR, path)

    index_path = _BUILD_DIR / "index.html"
    if index_path.exists():
        return send_file(index_path)

    return (
        "Pulse dashboard index.html not found. Build looks incomplete at: "
        f"{_BUILD_DIR}",
        404,
    )


def _df_records(df):
    return json.loads(df.to_json(orient="records", date_format="iso"))


def _safe_json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, default=str)
    except Exception:
        return repr(value)


_READ_ONLY_QUERY_LIMIT = 200
_READ_ONLY_SQL_RE = re.compile(r"^\s*(select|with|show|describe|desc|explain)\b", re.IGNORECASE)
_ALLOWED_READ_ONLY_STATEMENT_TYPES = (
    duckdb.StatementType.SELECT,
    duckdb.StatementType.EXPLAIN,
)


def _normalize_debug_sql(sql: Any) -> str:
    if sql is None:
        raise RequestValidationError("Missing SQL query")
    normalized = str(sql).strip()
    if not normalized:
        raise RequestValidationError("Missing SQL query")
    return normalized


def _validate_read_only_debug_sql(sql: str) -> str:
    normalized = _normalize_debug_sql(sql)
    stripped = normalized.rstrip().rstrip(';').strip()
    if not stripped:
        raise RequestValidationError("Missing SQL query")
    if ';' in stripped:
        raise RequestValidationError("Only a single SQL statement is allowed")
    if not _READ_ONLY_SQL_RE.match(stripped):
        raise RequestValidationError("Only read-only SELECT/SHOW/DESCRIBE/EXPLAIN queries are allowed")
    try:
        statements = duckdb.extract_statements(stripped)
    except Exception as exc:
        raise RequestValidationError(f"Invalid SQL query: {exc}") from exc
    if len(statements) != 1:
        raise RequestValidationError("Only a single SQL statement is allowed")
    if statements[0].type not in _ALLOWED_READ_ONLY_STATEMENT_TYPES:
        raise RequestValidationError("Only read-only SQL is allowed")
    return stripped


def _ok(payload: dict[str, Any] | None = None, *, status: int = 200):
    data = {"ok": True}
    if payload:
        data.update(payload)
    return jsonify(data), status


def _err(message: str, *, status: int = 400, hint: str | None = None):
    payload: dict[str, Any] = {"ok": False, "error": message}
    if hint:
        payload["hint"] = hint
    return jsonify(payload), status


def _parse_csv_list(value: str | None) -> list[str]:
    if not value:
        return []
    parts = [p.strip() for p in value.split(",")]
    return [p for p in parts if p]


_MD5_RE = re.compile(r"^[a-fA-F0-9]{32}$")


def _is_md5(value: str | None) -> bool:
    if not value:
        return False
    return bool(_MD5_RE.match(str(value).strip()))


def _extract_description_from_extras(extras: str | None) -> str | None:
    if not extras:
        return None

    try:
        payload = json.loads(extras)
    except Exception:
        return None

    if isinstance(payload, dict):
        for key in ["description", "desc", "short_description", "shortDescription"]:
            v = payload.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()

        for k, v in payload.items():
            if "description" in str(k).lower() and isinstance(v, str) and v.strip():
                return v.strip()

    return None


def _description_present_sql(extras_col: str) -> str:
    checks = [
        f"json_extract_string({extras_col}, '$.description')",
        f"json_extract_string({extras_col}, '$.desc')",
        f"json_extract_string({extras_col}, '$.short_description')",
        f"json_extract_string({extras_col}, '$.shortDescription')",
    ]
    return (
        "CASE WHEN "
        + " OR ".join(f"length(trim(coalesce({expr}, ''))) > 0" for expr in checks)
        + " THEN 1 ELSE 0 END"
    )


def _metadata_completeness_sql_for_asset_inventory() -> tuple[str, str]:
    score_sql = (
        "CASE\n"
        "  WHEN object_type = 'project' THEN 100.0 * (("
        + " + ".join(
            [
                _non_empty_sql("object_name"),
                _non_empty_sql("object_key"),
                _non_empty_sql("owner_login"),
                "CASE WHEN created_at IS NOT NULL THEN 1 ELSE 0 END",
                "CASE WHEN updated_at IS NOT NULL THEN 1 ELSE 0 END",
            ]
        )
        + ") / 5.0)\n"
        "  WHEN object_type = 'dataset' THEN 100.0 * (("
        + " + ".join(
            [
                _non_empty_sql("object_name"),
                _non_empty_sql("object_key"),
                _non_empty_sql("object_subtype"),
                _non_empty_sql("owner_login"),
                "CASE WHEN created_at IS NOT NULL THEN 1 ELSE 0 END",
                "CASE WHEN updated_at IS NOT NULL THEN 1 ELSE 0 END",
            ]
        )
        + ") / 6.0)\n"
        "  WHEN object_type = 'recipe' THEN 100.0 * (("
        + " + ".join(
            [
                _non_empty_sql("object_name"),
                _non_empty_sql("object_key"),
                _non_empty_sql("object_subtype"),
                _non_empty_sql("owner_login"),
                "CASE WHEN created_at IS NOT NULL THEN 1 ELSE 0 END",
                "CASE WHEN updated_at IS NOT NULL THEN 1 ELSE 0 END",
            ]
        )
        + ") / 6.0)\n"
        "  WHEN object_type = 'scenario' THEN 100.0 * (("
        + " + ".join(
            [
                _non_empty_sql("object_name"),
                _non_empty_sql("object_key"),
                _non_empty_sql("object_subtype"),
                _non_empty_sql("owner_login"),
                "CASE WHEN created_at IS NOT NULL THEN 1 ELSE 0 END",
                "CASE WHEN updated_at IS NOT NULL THEN 1 ELSE 0 END",
            ]
        )
        + ") / 6.0)\n"
        "  ELSE 25 * ("
        + " + ".join(
            [
                _non_empty_sql("object_name"),
                _non_empty_sql("object_key"),
                _non_empty_sql("owner_login"),
                "CASE WHEN updated_at IS NOT NULL THEN 1 ELSE 0 END",
            ]
        )
        + ")\n"
        "END"
    )
    status_sql = (
        f"CASE WHEN ({score_sql}) >= 99.9 THEN 'complete' "
        f"WHEN ({score_sql}) >= 50 THEN 'partial' ELSE 'sparse' END"
    )
    return score_sql, status_sql
def _metadata_completeness_sql_for_product_inventory() -> tuple[str, str]:
    score_sql = (
        "100.0 * (("
        + " + ".join(
            [
                _non_empty_sql("product_name"),
                _non_empty_sql("product_key"),
                _non_empty_sql("product_type"),
                _non_empty_sql("owner_login"),
                "CASE WHEN created_at IS NOT NULL THEN 1 ELSE 0 END",
                "CASE WHEN updated_at IS NOT NULL THEN 1 ELSE 0 END",
            ]
        )
        + ") / 6.0)"
    )
    status_sql = (
        f"CASE WHEN ({score_sql}) >= 99.9 THEN 'complete' "
        f"WHEN ({score_sql}) >= 50 THEN 'partial' ELSE 'sparse' END"
    )
    return score_sql, status_sql


# Best-effort mapping from catalog object types to metadata history tables.
# These tables are expected in the GOLD outputs.
_OBJECT_EXTRAS_SOURCES: dict[str, dict[str, object]] = {
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


# Map product catalog types to the activity-event object_type.
_PRODUCT_TO_EVENT_OBJECT_TYPE = {
    "api_service": "api_service",
    "insight": "insight",
    "agent_tool": "agent_tool",
}


def _safe_ident(name: str) -> str:
    return validate_identifier(name)


def _duckdb_init_in_progress() -> bool:
    if is_initialization_in_progress is None:
        return False
    try:
        return bool(is_initialization_in_progress())
    except Exception:
        return False


def _duckdb_busy_response(message: str = "DuckDB is initializing"):
    return _ok({"ok": False, "busy": True, "initializing": True, "error": message}, status=503)


def _require_duckdb_engine():
    if query_df is None or create_connection is None or ensure_database_ready is None:
        raise RuntimeError("pulse_duckdb not available")

    return (
        cast(Any, query_df),
        cast(Any, create_connection),
        cast(Any, ensure_database_ready),
    )


def _ensure_ready_if_enabled() -> dict[str, Any] | None:
    """Best-effort auto-init for endpoints needing DuckDB."""

    if pulse_settings is None or ensure_database_ready is None:
        return None

    if not getattr(pulse_settings, "PULSE_AUTO_INIT_DUCKDB", False):
        return None

    try:
        return cast(dict[str, Any], ensure_database_ready())
    except Exception:
        logger.exception("DuckDB auto-init failed")
        return {"ok": False, "error": "auto-init failed"}


@bp.route("/api/duckdb/query")
@_handle_request_errors("duckdb query")
def duckdb_query():
    _require_debug_access()

    if query_df is None:
        return _err("pulse_duckdb not available", status=500)

    q = (request.args.get("q") or "").strip()
    if not q:
        return _err("Missing query parameter 'q'", status=400)

    _ensure_ready_if_enabled()

    try:
        df = query_df(q)
        return _ok({"rows": _df_records(df)})
    except Exception as e:
        if ReadOnlySQLError is not None and isinstance(e, ReadOnlySQLError):
            return _err(
                str(e),
                status=400,
                hint="This endpoint only runs read statements (SELECT/EXPLAIN).",
            )
        msg = str(e)
        if "database does not exist" in msg:
            return _err(
                msg,
                status=503,
                hint="DuckDB not initialized yet. Load GOLD tables or enable auto-init.",
            )
        raise


@bp.route("/api/debug/duckdb/reload", methods=["POST"])
@_handle_request_errors("duckdb reload")
def debug_duckdb_reload():
    _require_debug_access()
    _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()

    started = time.time()
    _startup_init_status.update(
        {
            "state": "running",
            "phase": "bootstrap",
            "message": "Reloading DuckDB and refreshing GOLD tables",
            "startedAt": started,
            "finishedAt": None,
            "durationSec": None,
            "error": None,
            "report": None,
        }
    )
    _refresh_startup_status_metadata()

    load_report = cast(
        dict[str, Any],
        _ensure_database_ready(load_gold_tables=True, replace_gold_tables=True),
    )
    _invalidate_capability_cache()

    duration_sec = round(time.time() - started, 3)
    _startup_init_status.update(
        {
            "state": "ready" if bool(load_report.get("ok", False)) else "failed",
            "phase": "frontend_ready" if bool(load_report.get("ok", False)) else "failed",
            "message": "DuckDB reload complete" if bool(load_report.get("ok", False)) else "DuckDB reload failed",
            "finishedAt": time.time(),
            "durationSec": duration_sec,
            "error": None if bool(load_report.get("ok", False)) else _safe_json_dumps(load_report),
            "report": load_report,
        }
    )
    _refresh_startup_status_metadata()

    return _ok({"load": load_report})


@bp.route("/api/debug/duckdb/tables")
@_handle_request_errors("duckdb tables")
def debug_duckdb_tables():
    _require_debug_access()
    _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
    _ensure_ready_if_enabled()
    if _duckdb_init_in_progress():
        return _duckdb_busy_response()
    conn = _create_connection(read_only=True)
    try:
        rows = conn.execute("PRAGMA show_tables;").fetchall()
        table_names = sorted([str(r[0]) for r in rows])
        table_stats: list[dict[str, Any]] = []

        for table_name in table_names:
            safe_table_name = _safe_ident(table_name)
            columns_df = conn.execute(f"PRAGMA table_info({quote_identifier(safe_table_name)});").df()  # nosec B608 (table_name is validated)
            row_count_row = conn.execute(f"SELECT COUNT(*) AS n FROM {quote_identifier(safe_table_name)};").fetchone()  # nosec B608 (table_name is validated)
            size_row = conn.execute(
                """
                SELECT estimated_size
                FROM duckdb_tables()
                WHERE schema_name = 'main' AND table_name = ?
                LIMIT 1;
                """,
                [safe_table_name],
            ).fetchone()

            table_stats.append(
                {
                    "table": table_name,
                    "columnCount": int(len(columns_df.index)),
                    "rowCount": int(row_count_row[0]) if row_count_row else 0,
                    "estimatedSizeBytes": int(size_row[0]) if size_row and size_row[0] is not None else None,
                }
            )
    finally:
        conn.close()

    return _ok({"tables": table_names, "tableStats": table_stats})


@bp.route("/api/debug/duckdb/table/<table_name>")
@_handle_request_errors("duckdb table info")
def debug_duckdb_table(table_name: str):
    _require_debug_access()
    _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
    _ensure_ready_if_enabled()
    table_name = _safe_ident(table_name)

    if _duckdb_init_in_progress():
        return _duckdb_busy_response()

    conn = _create_connection(read_only=True)
    try:
        cols_df = conn.execute(f"PRAGMA table_info({quote_identifier(table_name)});").df()  # nosec B608 (table_name is validated)
        sample_df = conn.execute(f"SELECT * FROM {quote_identifier(table_name)} LIMIT 10;").df()  # nosec B608 (table_name is validated)
    finally:
        conn.close()

    return _ok({"columns": _df_records(cols_df), "sample": _df_records(sample_df)})


@bp.route("/api/debug/duckdb/query", methods=["GET", "POST"])
def debug_duckdb_query():
    try:
        _require_debug_access()
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        if _duckdb_init_in_progress():
            return _duckdb_busy_response()

        payload = request.get_json(silent=True) or {}
        sql_input = request.args.get("sql") if request.method == "GET" else payload.get("sql")
        sql = _validate_read_only_debug_sql(sql_input)

        conn = _create_connection(read_only=True)
        try:
            df = conn.execute(sql).df()
        except ReadOnlySQLError as exc:
            raise RequestValidationError(str(exc)) from exc
        except Exception as exc:
            raise RequestValidationError(str(exc)) from exc
        finally:
            conn.close()

        truncated = int(len(df.index)) > _READ_ONLY_QUERY_LIMIT
        limited_df = df.head(_READ_ONLY_QUERY_LIMIT).copy()

        return jsonify(
            {
                "ok": True,
                "sql": sql,
                "limit": _READ_ONLY_QUERY_LIMIT,
                "columns": list(limited_df.columns),
                "rows": _df_records(limited_df),
                "rowCount": int(len(df.index)),
                "returnedRowCount": int(len(limited_df.index)),
                "truncated": truncated,
            }
        )
    except RequestValidationError as exc:
        return jsonify(
            {
                "ok": False,
                "error": {
                    "code": "DUCKDB_PREVIEW_QUERY_INVALID",
                    "message": str(exc),
                },
            }
        ), 400
    except PermissionError as exc:
        return jsonify(
            {
                "ok": False,
                "error": {
                    "code": "ADMIN_ACCESS_REQUIRED",
                    "message": str(exc),
                },
            }
        ), 403
    except Exception:
        logger.exception("DuckDB preview query failed")
        return jsonify(
            {
                "ok": False,
                "error": {
                    "code": "DUCKDB_PREVIEW_QUERY_FAILED",
                    "message": "The preview query could not be completed.",
                },
            }
        ), 500


logger.info("Registering route decorator for /api/admin/pulse-topology from %s", __file__)
@bp.route("/api/admin/pulse-topology")
def admin_pulse_topology():
    try:
        if not _has_administration_access():
            return jsonify(
                {
                    "ok": False,
                    "error": {
                        "code": "ADMIN_ACCESS_REQUIRED",
                        "message": "Administration access is required.",
                    },
                    "counts": {"hubs": 0, "workers": 0},
                    "warnings": [],
                    "diagnostics": {"route_reached": True},
                }
            ), 403

        import dataiku

        client = dataiku.api_client()
        plugin_handle = client.get_plugin(plugin_id="dataiku-pulse")
        parameter_set = plugin_handle.get_settings().get_parameter_set(parameter_set_name="params-dashboard-instance")
        preset_name, preset_warning = _resolve_dashboard_topology_preset(plugin_handle)
        preset = parameter_set.get_preset(preset_name)

        if not preset:
            return jsonify(
                {
                    "ok": False,
                    "error": {
                        "code": "TOPOLOGY_CONFIG_NOT_FOUND",
                        "message": "Pulse topology configuration is unavailable.",
                    },
                    "diagnostics": {
                        "parameter_set_found": True,
                        "preset_found": False,
                    },
                }
            ), 404

        raw_config = getattr(preset, "plugin_config", None)
        if not isinstance(raw_config, dict):
            return jsonify(
                {
                    "ok": False,
                    "error": {
                        "code": "TOPOLOGY_CONFIG_INVALID",
                        "message": "Pulse topology configuration is unavailable.",
                    },
                    "diagnostics": {
                        "parameter_set_found": True,
                        "preset_found": True,
                        "plugin_config_type": type(raw_config).__name__ if raw_config is not None else "missing",
                    },
                }
            ), 500

        topology = _sanitize_topology_config(raw_config)
        workers = topology.get("workers") or []
        warnings: list[str] = []
        if preset_warning:
            warnings.append(preset_warning)

        return jsonify(
            {
                "ok": True,
                "hub": topology.get("hub") or {"url": ""},
                "workers": workers,
                "counts": {
                    "hubs": 1 if (topology.get("hub") or {}).get("url") else 0,
                    "workers": len(workers),
                },
                "warnings": warnings,
                "diagnostics": {
                    "parameter_set_found": True,
                    "preset_found": True,
                    **(topology.get("diagnostics") or {}),
                },
                "summary": {
                    "hubCount": 1 if (topology.get("hub") or {}).get("url") else 0,
                    "workerCount": len(workers),
                    "classifications": dict(
                        Counter(
                            worker.get("classification")
                            for worker in workers
                            if str(worker.get("classification") or "").strip()
                        )
                    ),
                },
                "topology": {
                    "hub": {
                        "url": str((topology.get("hub") or {}).get("url") or "").strip(),
                        "label": "Pulse Hub",
                    },
                    "spokes": [
                        {
                            "url": str(worker.get("url") or "").strip(),
                            "classification": str(worker.get("classification") or "").strip(),
                            "presetName": str(worker.get("presetName") or "").strip(),
                        }
                        for worker in workers
                    ],
                },
            }
        )
    except Exception:
        logger.exception("Unable to read Pulse topology configuration")
        return jsonify(
            {
                "ok": False,
                "error": {
                    "code": "TOPOLOGY_ENDPOINT_FAILED",
                    "message": "Unable to load the Pulse topology configuration.",
                },
                "counts": {"hubs": 0, "workers": 0},
                "warnings": [],
                "diagnostics": {"route_reached": True},
            }
        ), 500


@bp.route("/api/build/assets/facets")
def build_assets_facets():
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        assets_ready, init_report = _ensure_relation_ready(_query_df, "final_build_catalog")
        if not assets_ready:
            return _ok({"available": False, "reason": "final_build_catalog_missing", "init": init_report})

        instances = (
            _query_df("SELECT DISTINCT instance_name FROM final_build_catalog ORDER BY 1;")["instance_name"]
            .dropna()
            .astype(str)
            .tolist()
        )
        projects = (
            _query_df("SELECT DISTINCT project_key FROM final_build_catalog ORDER BY 1;")["project_key"]
            .dropna()
            .astype(str)
            .tolist()
        )
        types = (
            _query_df("SELECT DISTINCT object_type FROM final_build_catalog ORDER BY 1;")["object_type"]
            .dropna()
            .astype(str)
            .tolist()
        )
        owners = (
            _query_df("SELECT DISTINCT owner_login FROM final_build_catalog ORDER BY 1;")["owner_login"]
            .dropna()
            .astype(str)
            .tolist()
        )

        return _ok({"instances": instances, "projects": projects, "types": types, "owners": owners})
    except Exception as e:
        logger.exception("assets facets failed")
        return _err(str(e), status=500)


@bp.route("/api/build/assets")
def build_assets():
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        assets_ready, init_report = _ensure_relation_ready(_query_df, "final_build_catalog")
        if not assets_ready:
            return _ok({"available": False, "reason": "final_build_catalog_missing", "init": init_report})

        q = (request.args.get("q") or "").strip()
        owner = (request.args.get("owner") or "").strip()
        instances = _parse_csv_list(request.args.get("instances"))
        projects = _parse_csv_list(request.args.get("projects"))
        types = _parse_csv_list(request.args.get("types"))
        completeness_status = (request.args.get("completenessStatus") or "").strip().lower()

        sort = (request.args.get("sort") or "updated_desc").strip()
        limit, offset = _parse_pagination(default_limit=25, max_limit=5000)
        score_sql, status_sql = _metadata_completeness_sql_for_asset_inventory()

        # Guardrails
        limit = max(1, min(5000, limit))
        offset = max(0, offset)

        where = []
        params: list[Any] = []

        if q:
            where.append("(lower(object_name) LIKE ? OR lower(object_key) LIKE ?)")
            qq = f"%{q.lower()}%"
            params.extend([qq, qq])

        if owner:
            where.append("owner_login = ?")
            params.append(owner)

        if instances:
            where.append(f"instance_name IN ({','.join(['?'] * len(instances))})")
            params.extend(instances)

        if projects:
            where.append(f"project_key IN ({','.join(['?'] * len(projects))})")
            params.extend(projects)

        if types:
            where.append(f"object_type IN ({','.join(['?'] * len(types))})")
            params.extend(types)

        if completeness_status:
            if completeness_status not in {"complete", "partial", "sparse"}:
                return _err(f"Invalid completenessStatus: {completeness_status}")
            where.append(f"{status_sql} = ?")
            params.append(completeness_status)

        where_sql = (" WHERE " + " AND ".join(where)) if where else ""

        order_by = {
            "updated_desc": "updated_at DESC NULLS LAST",
            "updated_asc": "updated_at ASC NULLS LAST",
            "activity_desc": "activity_30d DESC NULLS LAST, updated_at DESC NULLS LAST",
            "name_asc": "object_name ASC NULLS LAST",
            "completeness_desc": f"{score_sql} DESC, updated_at DESC NULLS LAST",
            "completeness_asc": f"{score_sql} ASC, updated_at DESC NULLS LAST",
        }.get(sort)
        if order_by is None:
            return _err(f"Invalid sort: {sort}")

        count_sql = f"SELECT COUNT(*) AS n FROM final_build_catalog{where_sql};"  # nosec B608 (where_sql is parameterized)
        total = int(_query_df(count_sql, params).iloc[0]["n"])

        sql = (
            "SELECT\n"
            "  asset_id AS assetId,\n"
            "  object_name AS objectName,\n"
            "  object_key AS objectKey,\n"
            "  object_type AS objectType,\n"
            "  instance_name AS instanceName,\n"
            "  project_key AS projectKey,\n"
            "  owner_login AS ownerLogin,\n"
            "  updated_at AS updatedAt,\n"
            "  activity_30d AS activity30d,\n"
            f"  ROUND(({score_sql}), 1) AS metadataCompletenessScore,\n"
            f"  {status_sql} AS metadataCompletenessStatus\n"
            f"FROM final_build_catalog{where_sql}\n"  # nosec B608 (where_sql is parameterized)
            f"ORDER BY {order_by}\n"  # nosec B608 (order_by from allowlist)
            "LIMIT ? OFFSET ?;"
        )
        rows = _query_df(sql, [*params, limit, offset])

        return _ok({"rows": _df_records(rows), "total": total})

    except Exception as e:
        logger.exception("assets query failed")
        return _err(str(e), status=500)


@bp.route("/api/build/assets/metadata-summary")
def build_assets_metadata_summary():
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        assets_ready, init_report = _ensure_relation_ready(_query_df, "final_build_catalog")
        if not assets_ready:
            return _ok({"available": False, "reason": "final_build_catalog_missing", "init": init_report})

        rows_df = _query_df(
            (
                "SELECT\n"
                "  object_type AS objectType,\n"
                "  object_name AS objectName,\n"
                "  object_key AS objectKey,\n"
                "  object_subtype AS objectSubtype,\n"
                "  owner_login AS ownerLogin,\n"
                "  created_at AS createdAt,\n"
                "  updated_at AS updatedAt\n"
                "FROM final_build_catalog;"
            )
        )
        source_rows = _df_records(rows_df)
        rows: list[dict[str, Any]] = []
        for row in source_rows:
            score = round(_asset_inventory_metadata_score(row), 1)
            rows.append(
                {
                    "label": str(row.get("objectType") or "Unknown"),
                    "avgScore": score,
                    "metadataStatus": _metadata_status_from_score(score),
                }
            )

        total_assets = len(rows)
        avg_score = round(
            sum(float(row.get("avgScore") or 0.0) for row in rows) / total_assets,
            1,
        ) if total_assets else 0.0
        complete_count = sum(1 for row in rows if row.get("metadataStatus") == "complete")
        partial_count = sum(1 for row in rows if row.get("metadataStatus") == "partial")
        sparse_count = sum(1 for row in rows if row.get("metadataStatus") == "sparse")

        by_type: dict[str, dict[str, Any]] = {}
        for row in rows:
            label = str(row.get("label") or "Unknown")
            bucket = by_type.setdefault(
                label,
                {
                    "label": label,
                    "totalAssets": 0,
                    "_scoreSum": 0.0,
                    "completeCount": 0,
                    "partialCount": 0,
                    "sparseCount": 0,
                },
            )
            bucket["totalAssets"] += 1
            bucket["_scoreSum"] += float(row.get("avgScore") or 0.0)
            status = row.get("metadataStatus")
            if status == "complete":
                bucket["completeCount"] += 1
            elif status == "partial":
                bucket["partialCount"] += 1
            else:
                bucket["sparseCount"] += 1

        by_type_rows: list[dict[str, Any]] = []
        for bucket in by_type.values():
            total = int(bucket["totalAssets"])
            score_sum = float(bucket.pop("_scoreSum"))
            bucket["avgScore"] = round(score_sum / total, 1) if total else 0.0
            by_type_rows.append(bucket)
        by_type_rows.sort(key=lambda item: (-int(item["totalAssets"]), str(item["label"])))

        return _ok(
            {
                "summary": {
                    "totalAssets": total_assets,
                    "avgScore": avg_score,
                    "completeCount": complete_count,
                    "partialCount": partial_count,
                    "sparseCount": sparse_count,
                    "completeRate": (complete_count / total_assets) if total_assets else 0.0,
                    "sparseRate": (sparse_count / total_assets) if total_assets else 0.0,
                },
                "byType": by_type_rows,
            }
        )
    except Exception as e:
        logger.exception("assets metadata summary failed")
        return _err(str(e), status=500)


def _fetch_usage_and_related_assets(
    _query_df,
    *,
    project_key: str | None,
    object_type: str,
    object_key: str,
) -> tuple[int, list[dict[str, Any]]]:
    params: list[Any] = [object_type, object_key]
    where = "object_type = ? AND object_key = ?"
    if project_key is not None:
        where += " AND project_key = ?"
        params.append(project_key)

    usage_df = _query_df(
        f"SELECT COUNT(*) AS n FROM v_object_activity_events WHERE {where};",  # nosec B608 (where uses placeholders)
        params,
    )
    usage = int(usage_df.iloc[0]["n"]) if len(usage_df.index) else 0

    related_df = _query_df(
        f"""
        SELECT
          instance_name AS instanceName,
          project_key AS projectKey,
          COUNT(*) AS eventCount
        FROM v_object_activity_events
        WHERE {where}
        GROUP BY 1, 2
        ORDER BY eventCount DESC, instanceName, projectKey;
        """.strip(),  # nosec B608 (where uses placeholders)
        params,
    )

    return usage, _df_records(related_df)


def _fetch_description(
    _query_df,
    *,
    instance_name: str,
    project_key: str | None,
    object_type: str,
    object_key: str,
) -> str | None:
    spec = _OBJECT_EXTRAS_SOURCES.get(object_type)
    if not spec:
        return None

    table = str(spec["table"])
    key_col = str(spec["key_col"])
    project_scoped = bool(spec.get("project_scoped", False))

    where = ["instance_name = ?", f"{key_col} = ?"]
    params: list[Any] = [instance_name, object_key]

    if project_scoped:
        if not project_key:
            return None
        where.append("project_key = ?")
        params.append(project_key)

    df = _query_df(
        f"SELECT extras FROM {table} WHERE {' AND '.join(where)} LIMIT 1;",  # nosec B608 (table from allowlist)
        params,
    )
    if not len(df.index):
        return None

    extras = df.iloc[0].get("extras")
    return _extract_description_from_extras(extras if isinstance(extras, str) else None)


@bp.route("/api/build/assets/details")
def build_assets_details():
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        asset_id = (request.args.get("assetId") or "").strip()
        if not _is_md5(asset_id):
            return _err("Invalid or missing assetId", status=400)

        df = _query_df(
            """
            SELECT
              instance_name AS instanceName,
              project_key AS projectKey,
              object_type AS objectType,
              object_key AS objectKey,
              object_name AS objectName,
              owner_login AS ownerLogin,
              updated_at AS updatedAt,
              activity_30d AS activity30d
            FROM final_build_catalog
            WHERE asset_id = ?
            LIMIT 1;
            """.strip(),
            [asset_id],
        )

        if not len(df.index):
            return _err("Asset not found", status=404)

        row = _df_records(df)[0]
        instance_name = str(row.get("instanceName") or "")
        project_key = str(row.get("projectKey") or "")
        object_type = str(row.get("objectType") or "")
        object_key = str(row.get("objectKey") or "")

        usage, related_assets = _fetch_usage_and_related_assets(
            _query_df,
            project_key=project_key or None,
            object_type=object_type,
            object_key=object_key,
        )

        description = None
        try:
            description = _fetch_description(
                _query_df,
                instance_name=instance_name,
                project_key=project_key or None,
                object_type=object_type,
                object_key=object_key,
            )
        except Exception:
            description = None

        return _ok(
            {
                "asset": row,
                "capturedInfo": {"description": description},
                "usageSummary": {"eventsAllTime": usage},
                "relatedAssets": related_assets,
            }
        )

    except Exception as e:
        logger.exception("assets details failed")
        return _err(str(e), status=500)


@bp.route("/api/build/products/details")
def build_products_details():
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        asset_id = (request.args.get("assetId") or "").strip()
        if not _is_md5(asset_id):
            return _err("Invalid or missing assetId", status=400)

        df = _query_df(
            """
            SELECT
              instance_name AS instanceName,
              project_key AS projectKey,
              product_type AS objectType,
              product_key AS objectKey,
              product_name AS objectName,
              owner_login AS ownerLogin,
              updated_at AS updatedAt,
              activity_30d AS activity30d
            FROM final_build_products_catalog
            WHERE product_id = ?
            LIMIT 1;
            """.strip(),
            [asset_id],
        )

        if not len(df.index):
            return _err("Product not found", status=404)

        row = _df_records(df)[0]
        instance_name = str(row.get("instanceName") or "")
        project_key = str(row.get("projectKey") or "")
        product_type = str(row.get("objectType") or "")
        product_key = str(row.get("objectKey") or "")

        event_object_type = _PRODUCT_TO_EVENT_OBJECT_TYPE.get(product_type, product_type)

        usage, related_assets = _fetch_usage_and_related_assets(
            _query_df,
            project_key=project_key or None,
            object_type=event_object_type,
            object_key=product_key,
        )

        description = None
        try:
            description = _fetch_description(
                _query_df,
                instance_name=instance_name,
                project_key=project_key or None,
                object_type=product_type,
                object_key=product_key,
            )
        except Exception:
            description = None

        return _ok(
            {
                "asset": row,
                "capturedInfo": {"description": description},
                "usageSummary": {"eventsAllTime": usage},
                "relatedAssets": related_assets,
            }
        )

    except Exception as e:
        logger.exception("products details failed")
        return _err(str(e), status=500)


@bp.route("/api/build/products/facets")
def build_products_facets():
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        instances = (
            _query_df("SELECT DISTINCT instance_name FROM final_build_products_catalog ORDER BY 1;")["instance_name"]
            .dropna()
            .astype(str)
            .tolist()
        )
        projects = (
            _query_df("SELECT DISTINCT project_key FROM final_build_products_catalog ORDER BY 1;")["project_key"]
            .dropna()
            .astype(str)
            .tolist()
        )
        types = (
            _query_df("SELECT DISTINCT product_type FROM final_build_products_catalog ORDER BY 1;")["product_type"]
            .dropna()
            .astype(str)
            .tolist()
        )
        owners = (
            _query_df("SELECT DISTINCT owner_login FROM final_build_products_catalog ORDER BY 1;")["owner_login"]
            .dropna()
            .astype(str)
            .tolist()
        )

        return _ok({"instances": instances, "projects": projects, "types": types, "owners": owners})
    except Exception as e:
        logger.exception("products facets failed")
        return _err(str(e), status=500)


@bp.route("/api/build/products")
def build_products():
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        q = (request.args.get("q") or "").strip()
        owner = (request.args.get("owner") or "").strip()
        instances = _parse_csv_list(request.args.get("instances"))
        projects = _parse_csv_list(request.args.get("projects"))
        types = _parse_csv_list(request.args.get("types"))
        completeness_status = (request.args.get("completenessStatus") or "").strip().lower()

        sort = (request.args.get("sort") or "updated_desc").strip()
        limit, offset = _parse_pagination(default_limit=25, max_limit=5000)
        score_sql, status_sql = _metadata_completeness_sql_for_product_inventory()

        # Guardrails
        limit = max(1, min(5000, limit))
        offset = max(0, offset)

        where = []
        params: list[Any] = []

        if q:
            where.append("(lower(product_name) LIKE ? OR lower(product_key) LIKE ?)")
            qq = f"%{q.lower()}%"
            params.extend([qq, qq])

        if owner:
            where.append("owner_login = ?")
            params.append(owner)

        if instances:
            where.append(f"instance_name IN ({','.join(['?'] * len(instances))})")
            params.extend(instances)

        if projects:
            where.append(f"project_key IN ({','.join(['?'] * len(projects))})")
            params.extend(projects)

        if types:
            where.append(f"product_type IN ({','.join(['?'] * len(types))})")
            params.extend(types)

        if completeness_status:
            if completeness_status not in {"complete", "partial", "sparse"}:
                return _err(f"Invalid completenessStatus: {completeness_status}")
            where.append(f"{status_sql} = ?")
            params.append(completeness_status)

        where_sql = (" WHERE " + " AND ".join(where)) if where else ""

        order_by = {
            "updated_desc": "updated_at DESC NULLS LAST",
            "updated_asc": "updated_at ASC NULLS LAST",
            "activity_desc": "activity_30d DESC NULLS LAST, updated_at DESC NULLS LAST",
            "name_asc": "product_name ASC NULLS LAST",
            "completeness_desc": f"{score_sql} DESC, updated_at DESC NULLS LAST",
            "completeness_asc": f"{score_sql} ASC, updated_at DESC NULLS LAST",
        }.get(sort)
        if order_by is None:
            return _err(f"Invalid sort: {sort}")

        count_sql = f"SELECT COUNT(*) AS n FROM final_build_products_catalog{where_sql};"  # nosec B608 (where_sql is parameterized)
        total = int(_query_df(count_sql, params).iloc[0]["n"])

        sql = (
            "SELECT\n"
            "  product_id AS assetId,\n"
            "  product_name AS objectName,\n"
            "  product_key AS objectKey,\n"
            "  product_type AS objectType,\n"
            "  instance_name AS instanceName,\n"
            "  project_key AS projectKey,\n"
            "  owner_login AS ownerLogin,\n"
            "  updated_at AS updatedAt,\n"
            "  activity_30d AS activity30d,\n"
            f"  ROUND(({score_sql}), 1) AS metadataCompletenessScore,\n"
            f"  {status_sql} AS metadataCompletenessStatus\n"
            f"FROM final_build_products_catalog{where_sql}\n"  # nosec B608 (where_sql is parameterized)
            f"ORDER BY {order_by}\n"  # nosec B608 (order_by from allowlist)
            "LIMIT ? OFFSET ?;"
        )
        rows = _query_df(sql, [*params, limit, offset])

        return _ok({"rows": _df_records(rows), "total": total})
    except Exception as e:
        logger.exception("products query failed")
        return _err(str(e), status=500)


@bp.route("/api/build/products/metadata-summary")
def build_products_metadata_summary():
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        rows_df = _query_df(
            (
                "SELECT\n"
                "  product_type AS productType,\n"
                "  product_name AS productName,\n"
                "  product_key AS productKey,\n"
                "  owner_login AS ownerLogin,\n"
                "  created_at AS createdAt,\n"
                "  updated_at AS updatedAt\n"
                "FROM final_build_products_catalog;"
            )
        )
        source_rows = _df_records(rows_df)
        rows: list[dict[str, Any]] = []
        for row in source_rows:
            score = round(_product_inventory_metadata_score(row), 1)
            rows.append(
                {
                    "label": str(row.get("productType") or "Unknown"),
                    "avgScore": score,
                    "metadataStatus": _metadata_status_from_score(score),
                }
            )

        total_assets = len(rows)
        avg_score = round(
            sum(float(row.get("avgScore") or 0.0) for row in rows) / total_assets,
            1,
        ) if total_assets else 0.0
        complete_count = sum(1 for row in rows if row.get("metadataStatus") == "complete")
        partial_count = sum(1 for row in rows if row.get("metadataStatus") == "partial")
        sparse_count = sum(1 for row in rows if row.get("metadataStatus") == "sparse")

        by_type: dict[str, dict[str, Any]] = {}
        for row in rows:
            label = str(row.get("label") or "Unknown")
            bucket = by_type.setdefault(
                label,
                {
                    "label": label,
                    "totalAssets": 0,
                    "_scoreSum": 0.0,
                    "completeCount": 0,
                    "partialCount": 0,
                    "sparseCount": 0,
                },
            )
            bucket["totalAssets"] += 1
            bucket["_scoreSum"] += float(row.get("avgScore") or 0.0)
            status = row.get("metadataStatus")
            if status == "complete":
                bucket["completeCount"] += 1
            elif status == "partial":
                bucket["partialCount"] += 1
            else:
                bucket["sparseCount"] += 1

        by_type_rows: list[dict[str, Any]] = []
        for bucket in by_type.values():
            total = int(bucket["totalAssets"])
            score_sum = float(bucket.pop("_scoreSum"))
            bucket["avgScore"] = round(score_sum / total, 1) if total else 0.0
            by_type_rows.append(bucket)
        by_type_rows.sort(key=lambda item: (-int(item["totalAssets"]), str(item["label"])))

        return _ok(
            {
                "summary": {
                    "totalAssets": total_assets,
                    "avgScore": avg_score,
                    "completeCount": complete_count,
                    "partialCount": partial_count,
                    "sparseCount": sparse_count,
                    "completeRate": (complete_count / total_assets) if total_assets else 0.0,
                    "sparseRate": (sparse_count / total_assets) if total_assets else 0.0,
                },
                "byType": by_type_rows,
            }
        )
    except Exception as e:
        logger.exception("products metadata summary failed")
        return _err(str(e), status=500)


@bp.route("/api/build/products/type-metrics")
def build_products_type_metrics():
    """Deeper metrics/charts for a single product type (30d window)."""

    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        product_type = (request.args.get("type") or "").strip()
        if not product_type:
            return _err("Missing type", status=400)

        days = _parse_days_arg(default=30)

        # Basic allowlist: product types are plugin-controlled (terminology.yaml)
        allowed_df = _query_df("SELECT DISTINCT product_type FROM final_build_products_catalog ORDER BY 1;")
        allowed = set(str(r.get("product_type") or "") for r in _df_records(allowed_df))
        if product_type not in allowed:
            return _err("Invalid type", status=400)

        # Inventory-level KPIs (fast, from catalog)
        kpis_df = _query_df(
            (
                "SELECT\n"
                "  COUNT(*) AS total_products,\n"
                "  COUNT(*) FILTER (WHERE activity_30d > 0) AS active_products_30d,\n"
                "  SUM(activity_30d) AS events_30d,\n"
                "  MAX(last_activity_at) AS last_activity_at\n"
                "FROM final_build_products_catalog\n"
                "WHERE product_type = ?;"
            ),
            [product_type],
        )
        kpis_row = _df_records(kpis_df)[0] if len(kpis_df.index) else {}

        # Owner concentration (inventory)
        owners_df = _query_df(
            (
                "SELECT\n"
                "  owner_login AS label,\n"
                "  COUNT(*) AS value\n"
                "FROM final_build_products_catalog\n"
                "WHERE product_type = ?\n"
                "GROUP BY 1\n"
                "ORDER BY value DESC\n"
                "LIMIT 12;"
            ),
            [product_type],
        )

        # Top products by activity_30d (inventory-level)
        top_products_df = _query_df(
            (
                "SELECT\n"
                "  product_id AS productId,\n"
                "  product_name AS label,\n"
                "  activity_30d AS value\n"
                "FROM final_build_products_catalog\n"
                "WHERE product_type = ?\n"
                "ORDER BY value DESC NULLS LAST, product_name\n"
                "LIMIT 12;"
            ),
            [product_type],
        )

        # Use event table for distinct users + daily time series.
        event_type = _PRODUCT_TO_EVENT_OBJECT_TYPE.get(product_type, product_type)
        totals_df = _query_df(
            (
                "SELECT\n"
                "  COUNT(*) AS events,\n"
                "  COUNT(DISTINCT login) AS active_users\n"
                "FROM v_object_activity_events\n"
                "WHERE timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY\n"
                "  AND object_type = ?;"
            ),
            [days, event_type],
        )
        totals = _df_records(totals_df)[0] if len(totals_df.index) else {}

        daily_df = _query_df(
            (
                "SELECT\n"
                "  CAST(CAST(date_trunc('day', timestamp) AS DATE) AS VARCHAR) AS label,\n"
                "  COUNT(*) AS value\n"
                "FROM v_object_activity_events\n"
                "WHERE timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY\n"
                "  AND object_type = ?\n"
                "GROUP BY 1\n"
                "ORDER BY 1;"
            ),
            [days, event_type],
        )

        by_project_df = _query_df(
            (
                "SELECT\n"
                "  project_key AS label,\n"
                "  COUNT(*) AS value\n"
                "FROM v_object_activity_events\n"
                "WHERE timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY\n"
                "  AND object_type = ?\n"
                "GROUP BY 1\n"
                "ORDER BY value DESC\n"
                "LIMIT 12;"
            ),
            [days, event_type],
        )

        by_instance_df = _query_df(
            (
                "SELECT\n"
                "  instance_name AS label,\n"
                "  COUNT(*) AS value\n"
                "FROM v_object_activity_events\n"
                "WHERE timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY\n"
                "  AND object_type = ?\n"
                "GROUP BY 1\n"
                "ORDER BY value DESC\n"
                "LIMIT 12;"
            ),
            [days, event_type],
        )

        return _ok(
            {
                "type": product_type,
                "windowDays": days,
                "kpis": {
                    "totalProducts": int(kpis_row.get("total_products") or 0),
                    "activeProducts30d": int(kpis_row.get("active_products_30d") or 0),
                    "events30d": int(kpis_row.get("events_30d") or 0),
                    "activeUsers30d": int(totals.get("active_users") or 0),
                    "lastActivityAt": kpis_row.get("last_activity_at"),
                },
                "charts": {
                    "activityDaily": _df_records(daily_df),
                    "topOwnersByProducts": _df_records(owners_df),
                    "topProductsByEvents": _df_records(top_products_df),
                    "topProjectsByEvents": _df_records(by_project_df),
                    "eventsByInstance": _df_records(by_instance_df),
                },
            }
        )

    except Exception as e:
        logger.exception("products type metrics failed")
        return _err(str(e), status=500)


_CONSUMPTION_PRODUCT_OBJECT_TYPES = (
    "agent_tool",
    "api_service",
    "dashboard",
    "dataiku_application",
    "insight",
    "retrieval_augmented_llm",
    "saved_model",
    "web_application",
)

_CONSUMPTION_PRODUCT_OBJECT_TYPES_SQL = ",".join(f"'{t}'" for t in _CONSUMPTION_PRODUCT_OBJECT_TYPES)


def _parse_string_list_param(value: str | None) -> list[str]:
    if not value:
        return []
    items = [v.strip() for v in str(value).split(",")]
    return [v for v in items if v]


def _build_in_clause(column: str, values: list[str], params: list[Any]) -> str:
    if not values:
        return ""
    placeholders = ", ".join(["?"] * len(values))
    params.extend(values)
    return f" AND {column} IN ({placeholders})"


def _build_like_clause(column: str, value: str | None, params: list[Any]) -> str:
    if not value or not str(value).strip():
        return ""
    params.append(f"%{str(value).strip()}%")
    return f" AND {column} ILIKE ?"


def _consumption_product_types_filter_sql(column_sql: str) -> str:
    return f"{column_sql} IN ({_CONSUMPTION_PRODUCT_OBJECT_TYPES_SQL})"


def _consumption_product_catalog_cte_sql(alias: str = "catalog") -> str:
    product_type_filter_sql = _consumption_product_types_filter_sql("product_type")
    sql = f"""
{alias} AS (
  SELECT
    product_id,
    instance_name,
    project_key,
    product_type,
    product_key,
    product_name,
    owner_login,
    product_subtype,
    created_at,
    updated_at
  FROM final_build_products_catalog
  WHERE {product_type_filter_sql}
    AND product_key IS NOT NULL
)
    """.strip()  # nosec B608 - interpolated fragments come from fixed internal allowlists; request values remain parameterized
    return sql


def _consumption_product_matched_events_cte_sql(
    event_filters_sql: str,
    catalog_alias: str = "catalog",
    events_alias: str = "matched_events",
    event_source_alias: str = "e",
) -> str:
    object_type_filter_sql = _consumption_product_types_filter_sql(f"{event_source_alias}.object_type")
    sql = f"""
catalog_exact_keys AS (
  SELECT DISTINCT instance_name, project_key, product_type, product_key FROM {catalog_alias}
),
catalog_fallback_keys AS (
  SELECT
    instance_name,
    product_type,
    product_key,
    MIN(product_id) AS product_id,
    COUNT(*) AS candidate_count
  FROM {catalog_alias}
  GROUP BY 1,2,3
),
{events_alias} AS (
  SELECT
    {event_source_alias}.*,
    COALESCE(exact.product_id, fallback.product_id) AS matched_product_id
  FROM v_object_activity_events {event_source_alias}
  LEFT JOIN {catalog_alias} exact
    ON exact.instance_name = {event_source_alias}.instance_name
   AND exact.project_key IS NOT DISTINCT FROM {event_source_alias}.project_key
   AND exact.product_type = {event_source_alias}.object_type
   AND exact.product_key = {event_source_alias}.object_key
  LEFT JOIN catalog_fallback_keys fallback
    ON fallback.instance_name = {event_source_alias}.instance_name
   AND fallback.product_type = {event_source_alias}.object_type
   AND fallback.product_key = {event_source_alias}.object_key
   AND fallback.candidate_count = 1
   AND exact.product_id IS NULL
  WHERE {event_filters_sql}
    AND {object_type_filter_sql}
    AND {event_source_alias}.object_key IS NOT NULL
)
    """.strip()  # nosec B608 - interpolated fragments come from internal matcher helpers; request values remain parameterized
    return sql


def _consumption_product_matching_ctes_sql(event_filters_sql: str, catalog_alias: str = "catalog", events_alias: str = "matched_events", event_source_alias: str = "e") -> str:
    return ",\n".join([
        _consumption_product_catalog_cte_sql(catalog_alias),
        _consumption_product_matched_events_cte_sql(event_filters_sql, catalog_alias=catalog_alias, events_alias=events_alias, event_source_alias=event_source_alias),
    ])


def _consumption_product_search_filter_sql(params: list[Any], q: str | None, owner: str | None, alias: str = "catalog") -> str:
    clauses: list[str] = []
    if owner and owner.strip():
        params.append(f"%{owner.strip()}%")
        clauses.append(f"{alias}.owner_login ILIKE ?")
    if q and q.strip():
        qq = f"%{q.strip()}%"
        params.extend([qq, qq])
        clauses.append(f"({alias}.product_name ILIKE ? OR {alias}.product_key ILIKE ?)")
    if not clauses:
        return ""
    return " AND " + " AND ".join(clauses)


@bp.route("/api/consumption/products/facets")
def consumption_products_facets():
    """Facets for consumption filters."""

    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()
        _ensure_consumption_products_views(_create_connection)

        def _facet_values(column: str) -> list[str]:
            product_type_filter_sql = _consumption_product_types_filter_sql("product_type")
            sql = f"""
SELECT DISTINCT trim(CAST({column} AS VARCHAR)) AS value
FROM final_build_products_catalog
WHERE {product_type_filter_sql}
  AND {column} IS NOT NULL
  AND length(trim(CAST({column} AS VARCHAR))) > 0
ORDER BY 1;
            """.strip()  # nosec B608 - interpolated column comes from a fixed local allowlist in this function
            df = _query_df(sql)
            return [str(row.get("value") or "") for row in _df_records(df) if str(row.get("value") or "").strip()]

        return _ok(
            {
                "instances": _facet_values("instance_name"),
                "projects": _facet_values("project_key"),
                "types": _facet_values("product_type"),
                "owners": _facet_values("owner_login"),
            }
        )

    except Exception as e:
        logger.exception("consumption products facets failed")
        return _err(str(e), status=500)


@bp.route("/api/consumption/products/details")
def consumption_products_details():
    """Consumption drilldown for a single product (time window)."""

    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()
        _ensure_consumption_products_views(_create_connection)

        product_id = (request.args.get("productId") or "").strip()
        if not _is_md5(product_id):
            return _err("Invalid or missing productId", status=400)

        days = _parse_days_arg(default=30)

        row_df = _query_df(
            """
            SELECT
              instance_name AS instanceName,
              project_key AS projectKey,
              product_type AS productType,
              product_key AS productKey,
              product_name AS productName,
              owner_login AS ownerLogin,
              product_subtype AS productSubtype,
              created_at AS createdAt,
              updated_at AS updatedAt
            FROM final_build_products_catalog
            WHERE product_id = ?
            LIMIT 1;
            """.strip(),
            [product_id],
        )
        if not len(row_df.index):
            return _err("Product not found", status=404)

        row = _df_records(row_df)[0]
        matching_ctes_sql = _consumption_product_matching_ctes_sql(
            "e.timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY"
        )
        params: list[Any] = [days, product_id]

        details_totals_sql = f"""
WITH {matching_ctes_sql}
SELECT
  COUNT(*) AS events,
  COUNT(DISTINCT login) AS active_users,
  MAX(timestamp) AS last_activity_at,
  CASE WHEN COUNT(*) >= 5 THEN 1 ELSE 0 END AS repeat_use_status,
  CASE WHEN COUNT(DISTINCT login) >= 2 THEN 1 ELSE 0 END AS collaborative_status
FROM matched_events
WHERE matched_product_id = ?;
        """.strip()  # nosec B608 - interpolated matcher CTE is generated by internal helpers; request values remain parameterized
        totals_df = _query_df(details_totals_sql, params)
        totals = _df_records(totals_df)[0] if len(totals_df.index) else {}

        details_daily_sql = f"""
WITH {matching_ctes_sql}
SELECT
  CAST(CAST(date_trunc('day', timestamp) AS DATE) AS VARCHAR) AS label,
  COUNT(*) AS value
FROM matched_events
WHERE matched_product_id = ?
GROUP BY 1
ORDER BY 1;
        """.strip()  # nosec B608 - interpolated matcher CTE is generated by internal helpers; request values remain parameterized
        daily_df = _query_df(details_daily_sql, params)

        top_users_sql = f"""
WITH {matching_ctes_sql}
SELECT
  login AS label,
  COUNT(*) AS value
FROM matched_events
WHERE matched_product_id = ?
GROUP BY 1
ORDER BY value DESC
LIMIT 25;
        """.strip()  # nosec B608 - interpolated matcher CTE is generated by internal helpers; request values remain parameterized
        top_users_df = _query_df(top_users_sql, params)

        activity_daily_rows = []
        for r in _df_records(daily_df):
            label = r.get("label") or r.get("day")
            if label is None:
                continue
            activity_daily_rows.append({"label": label, "value": r.get("value")})

        total_events = int(totals.get("events") or 0)
        active_users = int(totals.get("active_users") or 0)
        repeat_use_status = total_events >= 5
        collaborative_status = active_users >= 2
        adoption_tier = (
            "Tier 1 · 1 user"
            if active_users == 1
            else "Tier 2 · 2+ users, <5 events"
            if active_users >= 2 and total_events < 5
            else "Tier 3 · 2+ users, 5+ events"
            if active_users >= 2 and total_events >= 5
            else "Unclassified"
        )
        breadth_score = min(active_users / 5.0, 1.0) * 100 if active_users > 0 else 0.0
        repeat_score = (100.0 if repeat_use_status else (total_events / 5.0) * 100) if total_events > 0 else 0.0
        collaboration_score = (100.0 if collaborative_status else (active_users / 2.0) * 100) if active_users > 0 else 0.0
        concentration_score = max(0.0, 100.0 - (total_events / max(total_events, 10)) * 100) if total_events > 0 else 0.0
        maturity_components = {
            "breadthScore": round(min(breadth_score, 100.0), 1),
            "repeatScore": round(min(repeat_score, 100.0), 1),
            "collaborationScore": round(min(collaboration_score, 100.0), 1),
            "concentrationScore": round(min(concentration_score, 100.0), 1),
        }
        maturity_score = round(sum(maturity_components.values()) / len(maturity_components), 1)
        maturity_tier = "scaled" if maturity_score >= 75 else "growing" if maturity_score >= 45 else "emerging"

        return _ok(
            {
                "windowDays": days,
                "product": row,
                "totals": {
                    "events": total_events,
                    "activeUsers": active_users,
                    "lastActivityAt": totals.get("last_activity_at"),
                    "repeatUseStatus": repeat_use_status,
                    "collaborativeStatus": collaborative_status,
                },
                "adoptionTier": adoption_tier,
                "maturity": {
                    "score": maturity_score,
                    "tier": maturity_tier,
                    "components": maturity_components,
                },
                "activityDaily": activity_daily_rows,
                "topUsers": _df_records(top_users_df),
            }
        )

    except Exception as e:
        logger.exception("consumption products details failed")
        return _err(str(e), status=500)


@bp.route("/api/consumption/products/summary")
def consumption_products_summary():
    """Consumption overview for product-level objects.

    Supports optional filters:
    - days: int (1..365)
    - q: substring search over product key/name
    - instances: CSV list
    - projects: CSV list
    - types: CSV list (subset of product object types)
    - owner: substring match over owner_login
    """

    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()
        _ensure_consumption_products_views(_create_connection)

        days = _parse_days_arg(default=30)

        q = (request.args.get("q") or "").strip()
        instances = _parse_string_list_param(request.args.get("instances"))
        projects = _parse_string_list_param(request.args.get("projects"))
        types = _parse_string_list_param(request.args.get("types"))
        owner = (request.args.get("owner") or "").strip()

        allowed_types = set(_CONSUMPTION_PRODUCT_OBJECT_TYPES)
        types = [t for t in types if t in allowed_types]

        params: list[Any] = [days]

        where = " WHERE timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY"

        if types:
            where += _build_in_clause("object_type", types, params)
        else:
            quoted_types = ", ".join("'" + t.replace("'", "''") + "'" for t in _CONSUMPTION_PRODUCT_OBJECT_TYPES)
            where += f" AND object_type IN ({quoted_types})"

        where += " AND object_key IS NOT NULL"
        where += _build_in_clause("instance_name", instances, params)
        where += _build_in_clause("project_key", projects, params)
        matching_ctes_sql = _consumption_product_matching_ctes_sql(where[len(' WHERE '):])

        # Use the catalog to filter by owner and query string so the filter space
        # stays aligned with supported product types.
        idx_filters = " WHERE 1=1"
        idx_params: list[Any] = []
        idx_filters += _build_like_clause("owner_login", owner, idx_params)
        if q:
            qq = f"%{q.strip()}%"
            idx_filters += " AND (product_name ILIKE ? OR product_key ILIKE ?)"
            idx_params.extend([qq, qq])

        idx_df = None
        idx_pairs: set[tuple[str, str, str, str]] | None = None
        if idx_filters != " WHERE 1=1":
            product_type_filter_sql = _consumption_product_types_filter_sql("product_type")
            idx_catalog_filters_sql = idx_filters.replace(" WHERE 1=1", " AND 1=1")
            idx_catalog_sql = f"""
SELECT instance_name, project_key, product_type, product_key
FROM final_build_products_catalog
WHERE {product_type_filter_sql}{idx_catalog_filters_sql};
            """.strip()  # nosec B608 - interpolated filters come from internal parameterized builders; request values remain parameterized
            idx_df = _query_df(idx_catalog_sql, idx_params)
            idx_pairs = set(
                (
                    str(r.get("instance_name") or ""),
                    str(r.get("project_key") or ""),
                    str(r.get("product_type") or ""),
                    str(r.get("product_key") or ""),
                )
                for r in _df_records(idx_df)
            )

        def _apply_idx_pairs_sql(alias: str) -> tuple[str, list[Any]]:
            if not idx_pairs:
                return "", []
            pairs = list(idx_pairs)
            sub_params: list[Any] = []
            clauses = []
            for inst, proj, typ, key in pairs:
                clauses.append(f"({alias}.instance_name = ? AND {alias}.project_key = ? AND {alias}.object_type = ? AND {alias}.object_key = ?)")
                sub_params.extend([inst, proj, typ, key])
            return " AND (" + " OR ".join(clauses) + ")", sub_params

        idx_where_sql, idx_where_params = _apply_idx_pairs_sql("e")

        totals_filter_sql = idx_where_sql.replace("object_type", "e.object_type").replace("object_key", "e.object_key").replace(
            "instance_name", "e.instance_name"
        ).replace("project_key", "e.project_key")
        totals_sql = f"""
WITH {matching_ctes_sql}
SELECT
  COUNT(*) AS events,
  COUNT(DISTINCT login) AS active_users,
  COUNT(DISTINCT matched_product_id) FILTER (WHERE matched_product_id IS NOT NULL) AS active_products
FROM matched_events e{totals_filter_sql};
        """.strip()  # nosec B608 - interpolated matcher/filter fragments are internal SQL builders; request values remain parameterized
        totals_df = _query_df(totals_sql, [*params, *idx_where_params])
        totals = _df_records(totals_df)[0] if len(totals_df.index) else {}

        totals_detail_sql = "".join(
            [
                "WITH product_stats AS (\n",
                "  SELECT\n",
                "    concat_ws('|', e.instance_name, e.project_key, e.object_type, e.object_key) AS product_id,\n",
                "    COUNT(*) AS events,\n",
                "    COUNT(DISTINCT e.login) AS active_users\n",
                "  FROM v_object_activity_events e\n",
                where,
                idx_where_sql,
                "\n  GROUP BY 1\n",
                "),\n",
                "user_product_stats AS (\n",
                "  SELECT\n",
                "    e.login,\n",
                "    COUNT(*) AS events,\n",
                "    COUNT(DISTINCT concat_ws('|', e.instance_name, e.project_key, e.object_type, e.object_key)) AS active_products\n",
                "  FROM v_object_activity_events e\n",
                where,
                idx_where_sql,
                "\n  GROUP BY 1\n",
                "),\n",
                "product_rollup AS (\n",
                "  SELECT\n",
                "    avg(active_users) AS avg_users_per_product,\n",
                "    COUNT(*) FILTER (WHERE active_users >= 2) AS collaborative_products,\n",
                "    COUNT(*) FILTER (WHERE active_users = 1) AS single_user_products,\n",
                "    COUNT(*) FILTER (WHERE active_users >= 2 AND events < 5) AS multi_user_light_products,\n",
                "    COUNT(*) FILTER (WHERE events >= 5) AS repeat_products,\n",
                "    COUNT(*) FILTER (WHERE active_users >= 2 AND events >= 5) AS adopted_products,\n",
                "    MAX(events) AS top_product_events\n",
                "  FROM product_stats\n",
                "),\n",
                "product_concentration AS (\n",
                "  SELECT\n",
                "    COALESCE(SUM(events) FILTER (WHERE product_rank = 1), 0) AS top1_product_events,\n",
                "    COALESCE(SUM(events) FILTER (WHERE product_rank <= 5), 0) AS top5_product_events\n",
                "  FROM (\n",
                "    SELECT\n",
                "      events,\n",
                "      ROW_NUMBER() OVER (ORDER BY events DESC, product_id) AS product_rank\n",
                "    FROM product_stats\n",
                "  ) ranked_products\n",
                "),\n",
                "user_rollup AS (\n",
                "  SELECT\n",
                "    avg(active_products) AS avg_products_per_user,\n",
                "    MAX(active_products) AS top_user_products\n",
                "  FROM user_product_stats\n",
                "),\n",
                "user_concentration AS (\n",
                "  SELECT\n",
                "    COALESCE(SUM(events) FILTER (WHERE user_rank = 1), 0) AS top1_user_events,\n",
                "    COALESCE(SUM(events) FILTER (WHERE user_rank <= 5), 0) AS top5_user_events\n",
                "  FROM (\n",
                "    SELECT\n",
                "      events,\n",
                "      ROW_NUMBER() OVER (ORDER BY events DESC, login) AS user_rank\n",
                "    FROM user_product_stats\n",
                "  ) ranked_users\n",
                ")\n",
                "SELECT\n",
                "  p.avg_users_per_product,\n",
                "  p.collaborative_products,\n",
                "  p.single_user_products,\n",
                "  p.multi_user_light_products,\n",
                "  p.repeat_products,\n",
                "  p.adopted_products,\n",
                "  p.top_product_events,\n",
                "  pc.top1_product_events,\n",
                "  pc.top5_product_events,\n",
                "  u.avg_products_per_user,\n",
                "  u.top_user_products,\n",
                "  uc.top1_user_events,\n",
                "  uc.top5_user_events\n",
                "FROM product_rollup p\n",
                "CROSS JOIN product_concentration pc\n",
                "CROSS JOIN user_rollup u\n",
                "CROSS JOIN user_concentration uc;",
            ]
        )
        totals_detail_df = _query_df(
            totals_detail_sql,  # nosec B608
            [*params, *idx_where_params, *params, *idx_where_params],
        )
        totals_detail = _df_records(totals_detail_df)[0] if len(totals_detail_df.index) else {}

        total_events = int(totals.get("events") or 0)
        top1_product_events = int(totals_detail.get("top1_product_events") or 0)
        top5_product_events = int(totals_detail.get("top5_product_events") or 0)
        top1_user_events = int(totals_detail.get("top1_user_events") or 0)
        top5_user_events = int(totals_detail.get("top5_user_events") or 0)

        breadth_ratio = (
            float(totals_detail.get("avg_users_per_product") or 0.0) / float(totals.get("active_users") or 0)
            if int(totals.get("active_users") or 0) > 0
            else 0.0
        )
        repeat_ratio = (
            int(totals_detail.get("repeat_products") or 0) / int(totals.get("active_products") or 0)
            if int(totals.get("active_products") or 0) > 0
            else 0.0
        )
        collaboration_ratio = (
            int(totals_detail.get("collaborative_products") or 0) / int(totals.get("active_products") or 0)
            if int(totals.get("active_products") or 0) > 0
            else 0.0
        )
        concentration_health = max(
            0.0,
            1.0 - ((top5_product_events + top5_user_events) / (2 * total_events) if total_events > 0 else 0.0),
        )

        maturity_components = {
            "breadthScore": round(breadth_ratio * 100, 1),
            "repeatScore": round(repeat_ratio * 100, 1),
            "collaborationScore": round(collaboration_ratio * 100, 1),
            "concentrationScore": round(concentration_health * 100, 1),
        }
        maturity_score = round(sum(maturity_components.values()) / len(maturity_components), 1)
        if maturity_score >= 75:
            maturity_tier = "scaled"
        elif maturity_score >= 45:
            maturity_tier = "growing"
        else:
            maturity_tier = "emerging"

        lifecycle = {}

        by_type_filter_sql = idx_where_sql.replace("object_type", "e.object_type").replace("object_key", "e.object_key").replace(
            "instance_name", "e.instance_name"
        ).replace("project_key", "e.project_key")
        by_type_sql = f"""
WITH {matching_ctes_sql},
product_stats AS (
  SELECT
    e.object_type AS product_type,
    e.matched_product_id AS product_id,
    COUNT(*) AS events,
    COUNT(DISTINCT e.login) AS active_users
  FROM matched_events e
  WHERE 1=1{by_type_filter_sql}
    AND e.matched_product_id IS NOT NULL
  GROUP BY 1,2
),
type_rollup AS (
  SELECT
    product_type AS label,
    SUM(events) AS events,
    COUNT(DISTINCT product_id) AS active_products,
    COUNT(*) FILTER (WHERE active_users >= 2 AND events >= 5) AS adoption_count,
    AVG(active_users) AS avg_users_per_product,
    MAX(active_users) AS max_users_on_product
  FROM product_stats
  GROUP BY 1
),
type_user_rollup AS (
  SELECT
    e.object_type AS label,
    COUNT(DISTINCT e.login) AS active_users
  FROM matched_events e
  WHERE 1=1{by_type_filter_sql}
    AND e.matched_product_id IS NOT NULL
  GROUP BY 1
),
type_maturity AS (
  SELECT
    product_type AS label,
    AVG(maturity_score) AS avg_maturity_score,
    MAX(maturity_score) AS max_maturity_score
  FROM (
    SELECT
      product_type,
      25.0 * (
        LEAST(active_users / 5.0, 1.0) +
        CASE WHEN events >= 5 THEN 1.0 ELSE events / 5.0 END +
        CASE WHEN active_users >= 2 THEN 1.0 ELSE active_users / 2.0 END +
        CASE
          WHEN events <= 0 THEN 0.0
          ELSE GREATEST(0.0, 1.0 - (events / GREATEST(events, 10.0)))
        END
      ) AS maturity_score
    FROM product_stats
  ) scored
  GROUP BY 1
)
SELECT
  tr.label,
  tr.events,
  COALESCE(tur.active_users, 0) AS active_users,
  tr.active_products,
  tr.avg_users_per_product,
  tr.max_users_on_product,
  COALESCE(tm.avg_maturity_score, 0) AS avg_maturity_score,
  COALESCE(tm.max_maturity_score, 0) AS max_maturity_score,
  tr.adoption_count
FROM type_rollup tr
LEFT JOIN type_user_rollup tur ON tur.label = tr.label
LEFT JOIN type_maturity tm ON tm.label = tr.label
ORDER BY tr.events DESC;
        """.strip()  # nosec B608 - interpolated matcher/filter fragments are internal SQL builders; request values remain parameterized
        by_type_df = _query_df(by_type_sql, [*params, *idx_where_params])

        activity_daily_sql = (
            "SELECT\n"  # nosec B608 (where/idx_where_sql use placeholders)
            "  CAST(CAST(date_trunc('day', timestamp) AS DATE) AS VARCHAR) AS label,\n"
            "  COUNT(*) AS value\n"
            "FROM v_object_activity_events e\n"
            + where
            + idx_where_sql
            + "\nGROUP BY 1\n"
            "ORDER BY 1;"
        )
        activity_daily_params = [*params]
        if idx_where_sql:
            activity_daily_params.extend(idx_where_params)
        activity_daily_df = _query_df(activity_daily_sql, activity_daily_params)

        top_products_filter_sql = idx_where_sql.replace("object_type", "e.object_type").replace(
            "object_key", "e.object_key"
        ).replace("instance_name", "e.instance_name").replace("project_key", "e.project_key")
        top_products_sql = f"""
WITH {matching_ctes_sql},
act AS (
  SELECT
    e.matched_product_id AS product_id,
    COUNT(*) AS events,
    COUNT(DISTINCT e.login) AS active_users,
    MAX(e.timestamp) AS last_activity_at
  FROM matched_events e
  WHERE 1=1{top_products_filter_sql}
    AND e.matched_product_id IS NOT NULL
  GROUP BY 1
)
SELECT
  c.product_id AS productId,
  c.instance_name AS instanceName,
  c.project_key AS projectKey,
  c.product_type AS productType,
  c.product_key AS productKey,
  COALESCE({_high_signal_product_name_sql('c.product_name')}, {_non_empty_value_sql('c.product_name')}, c.product_key) AS productName,
  {_non_empty_value_sql('c.owner_login')} AS ownerLogin,
  act.events AS events,
  act.active_users AS activeUsers,
  act.last_activity_at AS lastActivityAt
FROM act
INNER JOIN final_build_products_catalog c ON c.product_id = act.product_id
ORDER BY act.events DESC NULLS LAST, act.last_activity_at DESC NULLS LAST
LIMIT 50;
        """.strip()  # nosec B608 - interpolated fragments are trusted internal SQL helpers; request values remain parameterized
        top_products_df = _query_df(top_products_sql, [*params, *idx_where_params])

        activity_daily_rows = []
        for row in _df_records(activity_daily_df):
            label = row.get("label") or row.get("day")
            if label is None:
                continue
            activity_daily_rows.append({"label": label, "value": row.get("value")})

        return _ok(
            {
                "windowDays": days,
                "totals": {
                    "events": int(totals.get("events") or 0),
                    "activeUsers": int(totals.get("active_users") or 0),
                    "activeProducts": int(totals.get("active_products") or 0),
                    "avgUsersPerProduct": float(totals_detail.get("avg_users_per_product") or 0.0),
                    "collaborativeProducts": int(totals_detail.get("collaborative_products") or 0),
                    "repeatProducts": int(totals_detail.get("repeat_products") or 0),
                    "singleUserProducts": int(totals_detail.get("single_user_products") or 0),
                    "multiUserLightProducts": int(totals_detail.get("multi_user_light_products") or 0),
                    "adoptedProducts": int(totals_detail.get("adopted_products") or 0),
                    "topProductEvents": int(totals_detail.get("top_product_events") or 0),
                    "topUserProducts": int(totals_detail.get("top_user_products") or 0),
                    "avgProductsPerUser": float(totals_detail.get("avg_products_per_user") or 0.0),
                    "top1ProductEvents": top1_product_events,
                    "top5ProductEvents": top5_product_events,
                    "top1UserEvents": top1_user_events,
                    "top5UserEvents": top5_user_events,
                    "maturityScore": maturity_score,
                    "maturityTier": maturity_tier,
                    "maturityComponents": maturity_components,
                },
                "byType": _df_records(by_type_df),
                "activityDaily": activity_daily_rows,
                "topProducts": _df_records(top_products_df),
            }
        )

    except Exception as e:
        logger.exception("consumption products summary failed")
        return _err(str(e), status=500)


@bp.route("/api/consumption/products/lifecycle-summary")
def consumption_products_lifecycle_summary():
    """Observed lifecycle latency for products.

    v1 definition uses platform-observed milestones only:
    - start: product `created_at`
    - first consumption: first observed product activity event
    - multi-user adoption: first day cumulative distinct users >= 2
    - repeat use: first day cumulative events >= 5
    """

    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()
        _ensure_consumption_products_views(_create_connection)

        days = _parse_days_arg(default=365)
        instances = _parse_string_list_param(request.args.get("instances"))
        projects = _parse_string_list_param(request.args.get("projects"))
        types = _parse_string_list_param(request.args.get("types"))

        allowed_types = set(_CONSUMPTION_PRODUCT_OBJECT_TYPES)
        types = [t for t in types if t in allowed_types]

        params: list[Any] = [days]
        filters = ["e.timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY", "e.object_key IS NOT NULL"]
        if types:
            filters.append(f"e.object_type IN ({','.join(['?'] * len(types))})")
            params.extend(types)
        else:
            filters.append(
                f"e.object_type IN ({_CONSUMPTION_PRODUCT_OBJECT_TYPES_SQL})"
            )
        if instances:
            filters.append(f"e.instance_name IN ({','.join(['?'] * len(instances))})")
            params.extend(instances)
        if projects:
            filters.append(f"e.project_key IN ({','.join(['?'] * len(projects))})")
            params.extend(projects)

        event_where_sql = " WHERE " + " AND ".join(filters)

        product_params: list[Any] = []
        product_filters = ["p.created_at IS NOT NULL"]
        if types:
            product_filters.append(f"p.product_type IN ({','.join(['?'] * len(types))})")
            product_params.extend(types)
        else:
            product_filters.append(
                f"p.product_type IN ({_CONSUMPTION_PRODUCT_OBJECT_TYPES_SQL})"
            )
        if instances:
            product_filters.append(f"p.instance_name IN ({','.join(['?'] * len(instances))})")
            product_params.extend(instances)
        if projects:
            product_filters.append(f"p.project_key IN ({','.join(['?'] * len(projects))})")
            product_params.extend(projects)

        product_where_sql = " WHERE " + " AND ".join(product_filters)
        sql = (
            "WITH product_events AS (\n"
            "  SELECT\n"
            "    e.instance_name,\n"
            "    e.project_key,\n"
            "    e.object_type AS product_type,\n"
            "    e.object_key AS product_key,\n"
            "    CAST(date_trunc('day', e.timestamp) AS DATE) AS event_day,\n"
            "    MIN(e.timestamp) AS first_event_at_day,\n"
            "    COUNT(*) AS daily_events,\n"
            "    COUNT(DISTINCT e.login) AS daily_users\n"
            "  FROM v_object_activity_events e\n"
            f"  {event_where_sql}\n"  # nosec B608 (event_where_sql is built from placeholders and allowlisted literals)
            "  GROUP BY 1,2,3,4,5\n"
            "),\n"
            "milestones AS (\n"
            "  SELECT\n"
            "    pe.*,\n"
            "    SUM(daily_events) OVER w AS cumulative_events\n"
            "  FROM product_events pe\n"
            "  WINDOW w AS (PARTITION BY instance_name, project_key, product_type, product_key ORDER BY event_day ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)\n"
            "),\n"
            "users_by_product_day AS (\n"
            "  SELECT\n"
            "    e.instance_name,\n"
            "    e.project_key,\n"
            "    e.object_type AS product_type,\n"
            "    e.object_key AS product_key,\n"
            "    CAST(date_trunc('day', e.timestamp) AS DATE) AS event_day,\n"
            "    COUNT(DISTINCT e.login) AS daily_users\n"
            "  FROM v_object_activity_events e\n"
            f"  {event_where_sql}\n"  # nosec B608 (event_where_sql is built from placeholders and allowlisted literals)
            "  GROUP BY 1,2,3,4,5\n"
            "),\n"
            "users_by_product AS (\n"
            "  SELECT\n"
            "    d.instance_name,\n"
            "    d.project_key,\n"
            "    d.product_type,\n"
            "    d.product_key,\n"
            "    d.event_day,\n"
            "    (\n"
            "      SELECT COUNT(DISTINCT e2.login)\n"
            "      FROM v_object_activity_events e2\n"
            "      WHERE e2.instance_name = d.instance_name\n"
            "        AND e2.project_key = d.project_key\n"
            "        AND e2.object_type = d.product_type\n"
            "        AND e2.object_key = d.product_key\n"
            "        AND CAST(date_trunc('day', e2.timestamp) AS DATE) <= d.event_day\n"
            "        AND e2.timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY\n"
            "    ) AS cumulative_users\n"
            "  FROM users_by_product_day d\n"
            "),\n"
            "firsts AS (\n"
            "  SELECT\n"
            "    p.instance_name,\n"
            "    p.project_key,\n"
            "    p.product_type,\n"
            "    p.product_key,\n"
            "    p.product_name,\n"
            "    p.created_at,\n"
            "    MIN(m.first_event_at_day) AS first_consumption_at,\n"
            "    MIN(CASE WHEN u.cumulative_users >= 2 THEN u.event_day END) AS multi_user_at,\n"
            "    MIN(CASE WHEN m.cumulative_events >= 5 THEN m.event_day END) AS repeat_use_at\n"
            "  FROM final_build_products_catalog p\n"
            "  LEFT JOIN milestones m\n"
            "    ON m.instance_name = p.instance_name\n"
            "   AND m.project_key = p.project_key\n"
            "   AND m.product_type = p.product_type\n"
            "   AND m.product_key = p.product_key\n"
            "  LEFT JOIN users_by_product u\n"
            "    ON u.instance_name = p.instance_name\n"
            "   AND u.project_key = p.project_key\n"
            "   AND u.product_type = p.product_type\n"
            "   AND u.product_key = p.product_key\n"
            "   AND u.event_day = m.event_day\n"
            f"  {product_where_sql}\n"
            "  GROUP BY 1,2,3,4,5,6\n"
            "),\n"
            "durations AS (\n"
            "  SELECT\n"
            "    *,\n"
            "    datediff('day', CAST(created_at AS DATE), CAST(first_consumption_at AS DATE)) AS days_to_first_consumption,\n"
            "    datediff('day', CAST(created_at AS DATE), CAST(multi_user_at AS DATE)) AS days_to_multi_user,\n"
            "    datediff('day', CAST(created_at AS DATE), CAST(repeat_use_at AS DATE)) AS days_to_repeat_use\n"
            "  FROM firsts\n"
            ")\n"
            "SELECT\n"
            "  COUNT(*) AS products_with_created_at,\n"
            "  COUNT(*) FILTER (WHERE first_consumption_at IS NOT NULL) AS products_with_first_consumption,\n"
            "  COUNT(*) FILTER (WHERE multi_user_at IS NOT NULL) AS products_with_multi_user,\n"
            "  COUNT(*) FILTER (WHERE repeat_use_at IS NOT NULL) AS products_with_repeat_use,\n"
            "  median(days_to_first_consumption) FILTER (WHERE days_to_first_consumption IS NOT NULL AND days_to_first_consumption >= 0) AS median_days_to_first_consumption,\n"
            "  avg(days_to_first_consumption) FILTER (WHERE days_to_first_consumption IS NOT NULL AND days_to_first_consumption >= 0) AS avg_days_to_first_consumption,\n"
            "  median(days_to_multi_user) FILTER (WHERE days_to_multi_user IS NOT NULL AND days_to_multi_user >= 0) AS median_days_to_multi_user,\n"
            "  avg(days_to_multi_user) FILTER (WHERE days_to_multi_user IS NOT NULL AND days_to_multi_user >= 0) AS avg_days_to_multi_user,\n"
            "  median(days_to_repeat_use) FILTER (WHERE days_to_repeat_use IS NOT NULL AND days_to_repeat_use >= 0) AS median_days_to_repeat_use,\n"
            "  avg(days_to_repeat_use) FILTER (WHERE days_to_repeat_use IS NOT NULL AND days_to_repeat_use >= 0) AS avg_days_to_repeat_use\n"
            "FROM durations;"
        )

        df = _query_df(sql, [*params, *params, days, *product_params])

        row = _df_records(df)[0] if len(df.index) else {}
        return _ok(
            {
                "days": days,
                "summary": {
                    "productsWithCreatedAt": int(row.get("products_with_created_at") or 0),
                    "productsWithFirstConsumption": int(row.get("products_with_first_consumption") or 0),
                    "productsWithMultiUser": int(row.get("products_with_multi_user") or 0),
                    "productsWithRepeatUse": int(row.get("products_with_repeat_use") or 0),
                    "medianDaysToFirstConsumption": float(row.get("median_days_to_first_consumption") or 0.0),
                    "avgDaysToFirstConsumption": float(row.get("avg_days_to_first_consumption") or 0.0),
                    "medianDaysToMultiUser": float(row.get("median_days_to_multi_user") or 0.0),
                    "avgDaysToMultiUser": float(row.get("avg_days_to_multi_user") or 0.0),
                    "medianDaysToRepeatUse": float(row.get("median_days_to_repeat_use") or 0.0),
                    "avgDaysToRepeatUse": float(row.get("avg_days_to_repeat_use") or 0.0),
                },
            }
        )

    except Exception as e:
        logger.exception("consumption lifecycle summary failed")
        return _err(str(e), status=500)


@bp.route("/api/consumption/process-usage")
def consumption_process_usage():
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        days = _parse_days_arg(default=30)
        activity_source_kind = _activity_source_kind(_query_df)

        summary_df = _query_df(
            _consumption_process_usage_summary_sql(activity_source_kind),
            [days],
        )
        summary = _df_records(summary_df)[0] if len(summary_df) else {}

        by_capability_df = _query_df(
            _consumption_process_usage_by_capability_sql(activity_source_kind),
            [days],
        )

        activity_daily_df = _query_df(
            _consumption_process_usage_daily_sql(activity_source_kind),
            [days],
        )

        top_by_users_df = _query_df(
            _consumption_process_usage_top_users_sql(activity_source_kind),
            [days],
        )

        top_by_projects_df = _query_df(
            _consumption_process_usage_top_projects_sql(activity_source_kind),
            [days],
        )

        top_by_instances_df = _query_df(
            _consumption_process_usage_top_instances_sql(activity_source_kind),
            [days],
        )

        total_active_users = int(summary.get("active_users") or 0)
        top_by_users_rows = []
        for row in _df_records(top_by_users_df):
            user_count = int(row.get("value") or 0)
            user_share = None
            if total_active_users > 0:
                user_share = round((user_count / total_active_users) * 100.0, 1)
            enriched = dict(row)
            enriched["userShare"] = user_share
            top_by_users_rows.append(enriched)

        return _ok(
            {
                "windowDays": days,
                "summary": {
                    **summary,
                    "active_users": total_active_users,
                },
                "activityDaily": _df_records(activity_daily_df),
                "byCapability": _df_records(by_capability_df),
                "topByUsers": top_by_users_rows,
                "topByProjects": _df_records(top_by_projects_df),
                "topByInstances": _df_records(top_by_instances_df),
            }
        )

    except Exception as e:
        logger.exception("consumption process usage failed")
        return _err(str(e), status=500)


@bp.route("/api/consumption/process-usage/capability/<capability>")
def consumption_process_usage_capability(capability: str):
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        capability = capability.strip()
        if not capability:
            return _err("Missing capability")

        days = _parse_days_arg(default=30)
        activity_source_kind = _activity_source_kind(_query_df)

        summary_df = _query_df(
            _consumption_capability_summary_sql(activity_source_kind),
            [capability, days],
        )
        summary = _df_records(summary_df)[0] if len(summary_df) else {}

        activity_daily_df = _query_df(
            _consumption_capability_daily_sql(activity_source_kind),
            [capability, days],
        )

        top_users_df = _query_df(
            _consumption_capability_top_users_sql(activity_source_kind),
            [capability, days],
        )

        top_projects_df = _query_df(
            _consumption_capability_top_projects_sql(activity_source_kind),
            [capability, days],
        )

        return _ok(
            {
                "summary": summary,
                "activityDaily": _df_records(activity_daily_df),
                "topUsers": _df_records(top_users_df),
                "topProjects": _df_records(top_projects_df),
            }
        )

    except Exception as e:
        logger.exception("consumption capability detail failed")
        return _err(str(e), status=500)


@bp.route("/api/build/development-activity")
def build_development_activity():
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        days = _parse_days_arg(default=30)
        activity_source_kind = _activity_source_kind(_query_df)

        activity_daily_df = _query_df(
            _development_activity_daily_sql(activity_source_kind),
            [days],
        )

        by_capability_df = _query_df(
            _development_activity_by_capability_sql(activity_source_kind),
            [days],
        )

        by_category_df = _query_df(
            _development_activity_by_category_sql(activity_source_kind),
            [days],
        )

        top_users_df = _query_df(
            _development_activity_top_users_sql(activity_source_kind),
            [days],
        )

        activity_daily_rows = []
        for row in _df_records(activity_daily_df):
            label = row.get("label") or row.get("day")
            if label is None:
                continue
            activity_daily_rows.append({"label": label, "value": row.get("value")})

        return _ok(
            {
                "activityDaily": activity_daily_rows,
                "byCapability": _df_records(by_capability_df),
                "byCategory": _df_records(by_category_df),
                "topUsers": _df_records(top_users_df),
            }
        )

    except Exception as e:
        logger.exception("development activity failed")
        return _err(str(e), status=500)


@bp.route("/api/build/development-activity/capability/<capability>")
def build_development_activity_capability(capability: str):
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        capability = capability.strip()
        if not capability:
            return _err("Missing capability")

        days = _parse_days_arg(default=30)
        activity_source_kind = _activity_source_kind(_query_df)

        summary_df = _query_df(
            _development_capability_summary_sql(activity_source_kind),
            [capability, days],
        )
        summary = _df_records(summary_df)[0] if len(summary_df) else None

        activity_daily_df = _query_df(
            _development_capability_daily_sql(activity_source_kind),
            [capability, days],
        )

        categories_df = _query_df(
            _development_capability_categories_sql(activity_source_kind),
            [capability, days],
        )

        tags_df = _query_df(
            _development_capability_tags_sql(activity_source_kind),
            [capability, days],
        )

        top_users_df = _query_df(
            _development_capability_top_users_sql(activity_source_kind),
            [capability, days],
        )

        activity_daily_rows = []
        for row in _df_records(activity_daily_df):
            label = row.get("label") or row.get("day")
            if label is None:
                continue
            activity_daily_rows.append({"label": label, "value": row.get("value")})

        return _ok(
            {
                "summary": summary,
                "activityDaily": activity_daily_rows,
                "categories": _df_records(categories_df),
                "tags": _df_records(tags_df),
                "topUsers": _df_records(top_users_df),
            }
        )

    except Exception as e:
        logger.exception("capability drilldown failed")
        return _err(str(e), status=500)


@bp.route("/api/build/development-activity/user/<login>")
def build_development_activity_user(login: str):
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        login = login.strip()
        if not login:
            return _err("Missing login")

        days = int(request.args.get("days") or 30)
        days = max(1, min(365, days))
        activity_source_kind = _activity_source_kind(_query_df)

        summary_df = _query_df(
            _development_user_summary_sql(activity_source_kind),
            [login, days],
        )
        summary = _df_records(summary_df)[0] if len(summary_df) else None

        activity_daily_df = _query_df(
            _user_drilldown_daily_sql(activity_source_kind),
            [login, days],
        )

        capabilities_df = _query_df(
            _user_drilldown_capabilities_sql(activity_source_kind),
            [login, days],
        )

        categories_df = _query_df(
            _user_drilldown_categories_sql(activity_source_kind),
            [login, days],
        )

        tags_df = _query_df(
            _user_drilldown_tags_sql(activity_source_kind),
            [login, days],
        )

        activity_daily_rows = []
        for row in _df_records(activity_daily_df):
            label = row.get("label") or row.get("day")
            if label is None:
                continue
            activity_daily_rows.append({"label": label, "value": row.get("value")})

        return _ok(
            {
                "summary": summary,
                "activityDaily": activity_daily_rows,
                "capabilities": _df_records(capabilities_df),
                "categories": _df_records(categories_df),
                "tags": _df_records(tags_df),
            }
        )

    except Exception as e:
        logger.exception("user drilldown failed")
        return _err(str(e), status=500)


# ----------------------------------------------------------------------------
# Users (UI-only activity from audit logs)
# ----------------------------------------------------------------------------


def _parse_instance_name(value: str | None) -> str | None:
    out = (value or "").strip()
    return out or None


def _duckdb_relation_exists(query_df, relation_name: str) -> bool:
    rows = query_df(
        """
        SELECT 1 AS present
        FROM information_schema.tables
        WHERE table_schema = 'main' AND table_name = ?
        UNION ALL
        SELECT 1 AS present
        FROM information_schema.views
        WHERE table_schema = 'main' AND table_name = ?
        LIMIT 1
        """.strip(),
        [relation_name, relation_name],
    )
    return rows is not None and not rows.empty

def _ensure_relation_ready(query_df, relation_name: str) -> tuple[bool, dict[str, Any] | None]:
    init_report = _ensure_ready_if_enabled()
    if _duckdb_relation_exists(query_df, relation_name):
        return True, init_report
    return False, init_report


def _parse_login_norm(value: str) -> str:
    return value.strip().lower()


def _sql_placeholders(n: int) -> str:
    return ",".join(["?"] * n)


def _sql_string_literals(values: list[str]) -> str:
    escaped = [str(value).replace("'", "''") for value in values]
    if not escaped:
        return "''"
    return ",".join(f"'{value}'" for value in escaped)


def _hub_instances_sql_list() -> str:
    """Return a SQL-safe list like `'hub1','hub2'`.

    Used only to render plugin-controlled config into VIEW templates.
    """

    hub_instances = []
    if pulse_settings is not None:
        hub_instances = _parse_csv_list(getattr(pulse_settings, "PULSE_HUB_INSTANCE_NAMES", ""))

    if not hub_instances:
        # Keep the SQL valid; no instance will match this.
        return "'__none__'"

    escaped = ["'" + s.replace("'", "''") + "'" for s in hub_instances]
    return ",".join(escaped)


@bp.route("/api/build/users/facets")
def build_users_facets():
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        instances = (
            _query_df(_users_facets_instances_sql())["instance_name"]
            .dropna()
            .astype(str)
            .tolist()
        )

        return _ok({"instances": instances})
    except Exception as e:
        logger.exception("users facets failed")
        return _err(str(e), status=500)


def _window_months_where_sql(*, months: int) -> str:
    # months=3 means: this month + previous 2.
    # We use month boundaries (calendar months) instead of rolling N days.
    months = max(1, min(24, int(months)))
    return f"day >= date_trunc('month', current_date) - INTERVAL {months - 1} MONTH"


@bp.route("/api/build/users/kpis")
def build_users_kpis():
    """User directory KPIs for the Users page.

    KPIs are computed from `base_users_instance_metadata_history` so they reflect
    per-instance license/profile state.

    Query parameters:
    - licenseFilter: entitlement/license-group filter for enabled-user counts
    - activityFilter: optional observed-activity cohort filter for activity-based counts
    - instance_name: optional filter
    """

    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        standard = _read_standard_project_variables()
        excluded_profiles = _read_user_profile_exclude_consumer(standard)

        raw_license_filter = request.args.get("licenseFilter")
        raw_activity_filter = request.args.get("activityFilter")
        license_filter = _parse_license_filter(raw_license_filter)
        activity_filter = _parse_activity_filter(raw_activity_filter) if raw_activity_filter else None
        instance_name = _parse_instance_name(request.args.get("instance_name"))

        license_filter_sql_template, license_filter_params = _resolve_license_filter_clause(license_filter)
        license_filter_sql = _format_license_filter_clause(license_filter_sql_template, profile_expr="users_userprofile")
        license_filter_sql_by_instance = _format_license_filter_clause(license_filter_sql_template, profile_expr="l.users_userprofile")
        license_filter_params_list: list[Any] = list(license_filter_params)
        license_group_case_sql_for_latest = _license_group_case_sql("l.users_userprofile")

        activity_filter_sql = ""
        activity_filter_sql_by_instance = ""
        activity_filter_params_list: list[Any] = []
        if activity_filter:
            activity_filter_sql_template, activity_filter_params = _resolve_license_filter_clause(activity_filter)
            activity_filter_sql = _format_license_filter_clause(activity_filter_sql_template, profile_expr="users_userprofile")
            activity_filter_sql_by_instance = _format_license_filter_clause(activity_filter_sql_template, profile_expr="l.users_userprofile")
            activity_filter_params_list = list(activity_filter_params)

        instance_sql = ""
        instance_params: list[Any] = []
        if instance_name:
            instance_sql = " AND instance_name = ?"
            instance_params = [instance_name]

        thirty_day_start_expr = "(current_date - INTERVAL 30 DAY)::DATE"
        ninety_day_start_expr = "(current_date - INTERVAL 90 DAY)::DATE"
        six_month_start_expr = "(current_date - INTERVAL 6 MONTH)::DATE"
        twelve_month_start_expr = "(current_date - INTERVAL 12 MONTH)::DATE"

        df = _query_df(
            (
                "WITH latest AS (\n"
                "  SELECT\n"
                "    instance_name,\n"
                "    lower(trim(users_login)) AS login_norm,\n"
                "    trim(users_login) AS login,\n"
                "    coalesce(trim(users_displayname), trim(users_login)) AS display_name,\n"
                "    users_enabled,\n"
                "    users_userprofile,\n"
                "    run_ts,\n"
                "    ROW_NUMBER() OVER (\n"
                "      PARTITION BY instance_name, lower(trim(users_login))\n"
                "      ORDER BY run_ts DESC\n"
                "    ) AS rn\n"
                "  FROM base_users_instance_metadata_history\n"
                "  WHERE users_login IS NOT NULL AND length(trim(users_login)) > 0\n"
                f"    {instance_sql}\n"  # nosec B608 (instance_sql uses placeholders)
                ")\n"
                ", activity AS (\n"
                "  SELECT\n"
                "    lower(trim(login_norm)) AS login_norm,\n"
                "    SUM(viewing_actions_count) AS total_viewing,\n"
                "    SUM(developing_actions_count) AS total_developing,\n"
                "    SUM(CASE WHEN day >= "
                f"{thirty_day_start_expr}"
                " THEN viewing_actions_count ELSE 0 END) AS viewing_30d,\n"
                "    SUM(CASE WHEN day >= "
                f"{thirty_day_start_expr}"
                " THEN developing_actions_count ELSE 0 END) AS developing_30d,\n"
                "    SUM(CASE WHEN day >= "
                f"{ninety_day_start_expr}"
                " THEN viewing_actions_count ELSE 0 END) AS viewing_90d,\n"
                "    SUM(CASE WHEN day >= "
                f"{ninety_day_start_expr}"
                " THEN developing_actions_count ELSE 0 END) AS developing_90d,\n"
                "    SUM(CASE WHEN day >= "
                f"{six_month_start_expr}"
                " THEN viewing_actions_count ELSE 0 END) AS viewing_6m,\n"
                "    SUM(CASE WHEN day >= "
                f"{six_month_start_expr}"
                " THEN developing_actions_count ELSE 0 END) AS developing_6m,\n"
                "    SUM(CASE WHEN day >= "
                f"{twelve_month_start_expr}"
                " THEN viewing_actions_count ELSE 0 END) AS viewing_12m,\n"
                "    SUM(CASE WHEN day >= "
                f"{twelve_month_start_expr}"
                " THEN developing_actions_count ELSE 0 END) AS developing_12m\n"
                "  FROM fact_user_activity_daily\n"
                "  GROUP BY 1\n"
                ")\n"
                "SELECT\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True') AS enabled_users,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True'"
                f"{license_filter_sql}) AS enabled_users_no_consumer,\n"  # nosec B608 (exclude_sql uses placeholders)
                f"  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND ({license_group_case_sql_for_latest}) = 'Creator Licenses') AS enabled_users_license_creator,\n"
                f"  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND ({license_group_case_sql_for_latest}) = 'Consumer Licenses') AS enabled_users_license_consumer,\n"
                f"  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND ({license_group_case_sql_for_latest}) = 'Admin Licenses') AS enabled_users_license_admin,\n"
                f"  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND ({license_group_case_sql_for_latest}) = 'Other Licenses') AS enabled_users_license_other,\n"
                "  COALESCE(SUM(a.total_viewing), 0) AS total_viewing_actions,\n"
                "  COALESCE(SUM(a.total_developing), 0) AS total_developing_actions,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.total_viewing, 0) > 0) AS viewing_users,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.total_developing, 0) > 0) AS developing_users,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND (coalesce(a.viewing_30d, 0) > 0 OR coalesce(a.developing_30d, 0) > 0)) AS active_users_30d,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.developing_30d, 0) > 0) AS developing_users_30d,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND (coalesce(a.viewing_90d, 0) > 0 OR coalesce(a.developing_90d, 0) > 0)) AS active_users_90d,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.developing_90d, 0) > 0) AS developing_users_90d,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.developing_6m, 0) > 0) AS developing_users_6m,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND (coalesce(a.viewing_6m, 0) > 0 OR coalesce(a.developing_6m, 0) > 0)) AS active_users_6m,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND (coalesce(a.viewing_12m, 0) > 0 OR coalesce(a.developing_12m, 0) > 0)) AS active_users_12m,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.developing_12m, 0) > 0) AS developing_users_12m,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.viewing_6m, 0) > 0 AND coalesce(a.developing_6m, 0) = 0) AS viewer_only_users_6m\n"
                "FROM latest l\n"
                "LEFT JOIN activity a ON a.login_norm = l.login_norm\n"
                "WHERE rn = 1;"
            ),
            [*instance_params, *license_filter_params_list],
        )

        row = _df_records(df)[0] if len(df) else {}
        enabled_users = int(row.get("enabled_users") or 0)
        active_users_30d = int(row.get("active_users_30d") or 0)
        active_users_90d = int(row.get("active_users_90d") or 0)
        active_users_6m = int(row.get("active_users_6m") or 0)
        active_users_12m = int(row.get("active_users_12m") or 0)
        developing_users_30d = int(row.get("developing_users_30d") or 0)
        developing_users_90d = int(row.get("developing_users_90d") or 0)
        developing_users_6m = int(row.get("developing_users_6m") or 0)
        developing_users_12m = int(row.get("developing_users_12m") or 0)
        row["inactive_users_6m"] = max(0, enabled_users - active_users_6m)
        row["inactive_users_30d"] = max(0, enabled_users - active_users_30d)
        row["inactive_users_90d"] = max(0, enabled_users - active_users_90d)
        row["inactive_users_12m"] = max(0, enabled_users - active_users_12m)
        total_viewing_actions = int(row.get("total_viewing_actions") or 0)
        total_developing_actions = int(row.get("total_developing_actions") or 0)
        row["active_rate_30d"] = (active_users_30d / enabled_users) if enabled_users else 0.0
        row["active_rate_90d"] = (active_users_90d / enabled_users) if enabled_users else 0.0
        row["active_rate_6m"] = (active_users_6m / enabled_users) if enabled_users else 0.0
        row["active_rate_12m"] = (active_users_12m / enabled_users) if enabled_users else 0.0
        row["contributor_rate_30d"] = (developing_users_30d / enabled_users) if enabled_users else 0.0
        row["contributor_rate_90d"] = (developing_users_90d / enabled_users) if enabled_users else 0.0
        row["contributor_rate_6m"] = (developing_users_6m / enabled_users) if enabled_users else 0.0
        row["contributor_rate_12m"] = (developing_users_12m / enabled_users) if enabled_users else 0.0
        row["developing_action_share"] = (
            total_developing_actions / (total_viewing_actions + total_developing_actions)
            if (total_viewing_actions + total_developing_actions)
            else 0.0
        )
        history_df = _query_df(
            (
                "SELECT\n"
                "  MIN(day) AS first_activity_day,\n"
                "  MAX(day) AS last_activity_day\n"
                "FROM fact_user_activity_daily\n"
                "WHERE 1 = 1"
                f"{instance_sql}\n"  # nosec B608 (instance_sql uses placeholders)
            ),
            instance_params,
        )
        history_row = _df_records(history_df)[0] if len(history_df) else {}
        row["activity_history_start"] = history_row.get("first_activity_day")
        row["activity_history_end"] = history_row.get("last_activity_day")
        row["active_window_days"] = [30, 90]
        row["inactive_window_months"] = 6
        row["active_window_months"] = [6, 12]

        license_status_df = _query_df(
            (
                "SELECT *\n"
                "FROM base_license_status_latest\n"
                "WHERE instance_name IS NOT NULL"
                f"{instance_sql}\n"  # nosec B608 (instance_sql uses placeholders)
                "ORDER BY instance_name;"
            ),
            instance_params,
        )
        license_status_rows = _df_records(license_status_df)

        def _most_common_license_value(field_name: str) -> dict[str, Any] | None:
            counts: dict[tuple[str, str], dict[str, Any]] = {}
            for status_row in license_status_rows:
                raw_value = status_row.get(field_name)
                display_value = _license_status_display_value(field_name, raw_value)
                if not display_value:
                    continue
                key = (display_value.lower(), display_value)
                bucket = counts.setdefault(
                    key,
                    {"value": display_value, "count": 0},
                )
                bucket["count"] += 1

            if not counts:
                return None

            return sorted(
                counts.values(),
                key=lambda item: (-int(item.get("count") or 0), str(item.get("value") or "")),
            )[0]

        license_status_summary: dict[str, Any] = {
            "instanceCount": len(license_status_rows),
            "mode": "single_instance" if instance_name else "most_common",
            "fields": {},
            "features": [],
        }

        for field_name in [
            "license_kind",
            "has_license",
            "valid",
            "expired",
            "community",
            "fallback_profile",
            "expires_on",
            "licensee_company",
            "licensee_name",
            "standard_offer",
            "emitted_by",
            "emitted_on",
        ]:
            field_summary = _most_common_license_value(field_name)
            if field_summary is not None:
                license_status_summary["fields"][field_name] = field_summary

        expires_field = cast(dict[str, Any] | None, license_status_summary["fields"].get("expires_on"))
        if expires_field and expires_field.get("value"):
            expires_on_value = str(expires_field.get("value") or "").strip()
            days_remaining_row = _query_df(
                (
                    "SELECT date_diff('day', current_date, try_cast(? AS DATE)) AS days_left;"
                ),
                [expires_on_value],
            )
            days_left = None
            if len(days_remaining_row):
                days_left = days_remaining_row.iloc[0].get("days_left")
            if days_left is not None:
                license_status_summary["fields"]["days_left"] = {
                    "value": int(days_left),
                    "count": int(expires_field.get("count") or 0),
                }

        excluded_feature_columns = {
            "instance_name",
            "instance_id",
            "license_id",
            "license_kind",
            "has_license",
            "valid",
            "expired",
            "community",
            "fallback_profile",
            "expires_on",
            "licensee_company",
            "licensee_name",
            "standard_offer",
            "emitted_by",
            "emitted_on",
            "run_ts",
        }
        feature_counts: dict[str, int] = {}
        for status_row in license_status_rows:
            for key, value in status_row.items():
                if key in excluded_feature_columns:
                    continue
                if _truthy_license_feature(value):
                    feature_counts[key] = feature_counts.get(key, 0) + 1

        curated_feature_fields = [
            ("community", "Community Edition"),
            ("standard_offer", "Standard Offer"),
            ("fallback_profile", "Fallback Profile"),
        ]
        curated_features: list[dict[str, Any]] = []
        for field_name, label in curated_feature_fields:
            field_summary = cast(dict[str, Any] | None, license_status_summary["fields"].get(field_name))
            if not field_summary:
                continue
            value = str(field_summary.get("value") or "").strip()
            if not value:
                continue
            if field_name == "community" and value.lower() != "true":
                continue
            curated_features.append(
                {
                    "key": field_name,
                    "label": label if field_name == "community" else f"{label}: {value}",
                    "count": int(field_summary.get("count") or 0),
                }
            )

        dynamic_features = [
            {
                "key": key,
                "label": key.replace("_", " ").strip().title(),
                "count": count,
            }
            for key, count in sorted(feature_counts.items(), key=lambda item: (-item[1], item[0]))
        ]
        license_status_summary["features"] = curated_features or dynamic_features

        addon_license_df = _query_df(
            (
                "SELECT addon_key, addon_enabled\n"
                "FROM base_license_addon_licenses_latest\n"
                "WHERE instance_name IS NOT NULL"
                f"{instance_sql}\n"  # nosec B608 (instance_sql uses placeholders)
                "ORDER BY addon_key;"
            ),
            instance_params,
        )
        addon_license_rows = _df_records(addon_license_df)
        addon_counts: dict[str, dict[str, Any]] = {}
        for addon_row in addon_license_rows:
            if not _truthy_license_feature(addon_row.get("addon_enabled")):
                continue
            addon_key = str(addon_row.get("addon_key") or "").strip()
            if not addon_key:
                continue
            entry = addon_counts.setdefault(
                addon_key,
                {
                    "key": addon_key,
                    "label": _addon_service_label(addon_key),
                    "count": 0,
                },
            )
            entry["count"] += 1

        license_status_summary["addonServices"] = [
            addon
            for addon in sorted(addon_counts.values(), key=lambda item: (-int(item.get("count") or 0), str(item.get("label") or "")))
        ]

        by_profile_df = _query_df(
            (
                "WITH latest AS (\n"
                "  SELECT\n"
                "    instance_name,\n"
                "    lower(trim(users_login)) AS login_norm,\n"
                "    coalesce(nullif(trim(users_userprofile), ''), 'UNKNOWN') AS user_profile,\n"
                "    users_enabled,\n"
                "    run_ts,\n"
                "    ROW_NUMBER() OVER (\n"
                "      PARTITION BY instance_name, lower(trim(users_login))\n"
                "      ORDER BY run_ts DESC\n"
                "    ) AS rn\n"
                "  FROM base_users_instance_metadata_history\n"
                "  WHERE users_login IS NOT NULL AND length(trim(users_login)) > 0\n"
                f"    {instance_sql}\n"  # nosec B608 (instance_sql uses placeholders)
                ")\n"
                "SELECT\n"
                "  user_profile AS profile,\n"
                "  COUNT(DISTINCT login_norm) FILTER (WHERE users_enabled = 'True') AS enabled_users\n"
                "FROM latest\n"
                "WHERE rn = 1\n"
                "GROUP BY 1\n"
                "ORDER BY enabled_users DESC, profile;"
            ),
            instance_params,
        )

        license_group_case_sql = _license_group_case_sql("user_profile")
        by_license_group_df = _query_df(
            (
                "WITH latest AS (\n"
                "  SELECT\n"
                "    instance_name,\n"
                "    lower(trim(users_login)) AS login_norm,\n"
                "    coalesce(nullif(trim(users_userprofile), ''), 'UNKNOWN') AS user_profile,\n"
                "    users_enabled,\n"
                "    run_ts,\n"
                "    ROW_NUMBER() OVER (\n"
                "      PARTITION BY instance_name, lower(trim(users_login))\n"
                "      ORDER BY run_ts DESC\n"
                "    ) AS rn\n"
                "  FROM base_users_instance_metadata_history\n"
                "  WHERE users_login IS NOT NULL AND length(trim(users_login)) > 0\n"
                f"    {instance_sql}\n"  # nosec B608 (instance_sql uses placeholders)
                ")\n"
                "SELECT\n"
                f"  {license_group_case_sql} AS license_group,\n"
                "  COUNT(DISTINCT login_norm) FILTER (WHERE users_enabled = 'True') AS enabled_users\n"
                "FROM latest\n"
                "WHERE rn = 1\n"
                "GROUP BY 1\n"
                "ORDER BY enabled_users DESC, license_group;"
            ),
            instance_params,
        )

        profile_normalized_expr = _license_profile_normalize_sql("user_profile")
        max_license_profile_df = _query_df(
            (
                "WITH instance_scope AS (\n"
                "  SELECT DISTINCT instance_name\n"
                "  FROM base_users_instance_metadata_history\n"
                "  WHERE users_login IS NOT NULL AND length(trim(users_login)) > 0\n"
                f"    {instance_sql}\n"  # nosec B608 (instance_sql uses placeholders)
                "),\n"
                "normalized_max AS (\n"
                "  SELECT\n"
                "    m.instance_name,\n"
                "    coalesce(nullif(trim(m.license_profile), ''), 'UNKNOWN') AS profile,\n"
                f"    {_license_profile_normalize_sql('m.license_profile')} AS profile_norm,\n"
                "    try_cast(m.max_licenses AS BIGINT) AS max_licenses\n"
                "  FROM base_license_max_licenses_latest m\n"
                "  INNER JOIN instance_scope s ON s.instance_name = m.instance_name\n"
                "  WHERE try_cast(m.max_licenses AS BIGINT) IS NOT NULL\n"
                "),\n"
                "ranked AS (\n"
                "  SELECT\n"
                "    profile_norm,\n"
                "    profile,\n"
                "    max_licenses,\n"
                "    COUNT(*) AS instance_count,\n"
                "    ROW_NUMBER() OVER (\n"
                "      PARTITION BY profile_norm\n"
                "      ORDER BY COUNT(*) DESC, max_licenses DESC, profile ASC\n"
                "    ) AS rn\n"
                "  FROM normalized_max\n"
                "  GROUP BY 1, 2, 3\n"
                ")\n"
                "SELECT\n"
                "  profile_norm,\n"
                "  profile AS profile_from_max,\n"
                "  max_licenses,\n"
                "  instance_count\n"
                "FROM ranked\n"
                "WHERE rn = 1;"
            ),
            instance_params,
        )

        by_license_profile_group_df = _query_df(
            (
                "WITH latest AS (\n"
                "  SELECT\n"
                "    instance_name,\n"
                "    lower(trim(users_login)) AS login_norm,\n"
                "    coalesce(nullif(trim(users_userprofile), ''), 'UNKNOWN') AS user_profile,\n"
                "    users_enabled,\n"
                "    run_ts,\n"
                "    ROW_NUMBER() OVER (\n"
                "      PARTITION BY instance_name, lower(trim(users_login))\n"
                "      ORDER BY run_ts DESC\n"
                "    ) AS rn\n"
                "  FROM base_users_instance_metadata_history\n"
                "  WHERE users_login IS NOT NULL AND length(trim(users_login)) > 0\n"
                f"    {instance_sql}\n"  # nosec B608 (instance_sql uses placeholders)
                ")\n"
                "SELECT\n"
                f"  {license_group_case_sql} AS license_group,\n"
                "  user_profile AS profile,\n"
                f"  {profile_normalized_expr} AS profile_norm,\n"
                "  COUNT(DISTINCT login_norm) FILTER (WHERE users_enabled = 'True') AS enabled_users\n"
                "FROM latest\n"
                "WHERE rn = 1\n"
                "GROUP BY 1, 2, 3\n"
                "HAVING COUNT(DISTINCT login_norm) FILTER (WHERE users_enabled = 'True') > 0\n"
                "ORDER BY license_group, enabled_users DESC, profile;"
            ),
            instance_params,
        )

        by_instance_df = _query_df(
            (
                "WITH latest AS (\n"
                "  SELECT\n"
                "    instance_name,\n"
                "    lower(trim(users_login)) AS login_norm,\n"
                "    users_enabled,\n"
                "    users_userprofile,\n"
                "    run_ts,\n"
                "    ROW_NUMBER() OVER (\n"
                "      PARTITION BY instance_name, lower(trim(users_login))\n"
                "      ORDER BY run_ts DESC\n"
                "    ) AS rn\n"
                "  FROM base_users_instance_metadata_history\n"
                "  WHERE users_login IS NOT NULL AND length(trim(users_login)) > 0\n"
                f"    {instance_sql}\n"  # nosec B608 (instance_sql uses placeholders)
                "),\n"
                "activity AS (\n"
                "  SELECT\n"
                "    instance_name,\n"
                "    lower(trim(login_norm)) AS login_norm,\n"
                "    SUM(viewing_actions_count) AS total_viewing,\n"
                "    SUM(developing_actions_count) AS total_developing,\n"
                "    SUM(CASE WHEN day >= "
                f"{six_month_start_expr}"
                " THEN viewing_actions_count ELSE 0 END) AS viewing_6m,\n"
                "    SUM(CASE WHEN day >= "
                f"{six_month_start_expr}"
                " THEN developing_actions_count ELSE 0 END) AS developing_6m\n"
                "  FROM fact_user_activity_daily\n"
                "  GROUP BY 1, 2\n"
                ")\n"
                "SELECT\n"
                "  l.instance_name AS instanceName,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True') AS enabled_users,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True'"
                f"{license_filter_sql_by_instance}) AS enabled_users_no_consumer,\n"  # nosec B608 (exclude_sql_by_instance uses placeholders)
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.total_viewing, 0) > 0) AS viewing_users,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.total_developing, 0) > 0) AS developing_users,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.developing_6m, 0) > 0) AS developing_users_6m,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND (coalesce(a.viewing_6m, 0) > 0 OR coalesce(a.developing_6m, 0) > 0)) AS active_users_6m,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.viewing_6m, 0) > 0 AND coalesce(a.developing_6m, 0) = 0) AS viewer_only_users_6m\n"
                "FROM latest l\n"
                "LEFT JOIN activity a ON a.instance_name = l.instance_name AND a.login_norm = l.login_norm\n"
                "WHERE l.rn = 1\n"
                "GROUP BY 1\n"
                "ORDER BY enabled_users DESC, instanceName;"
            ),
            [*instance_params, *license_filter_params_list],
        )

        by_instance_rows = _df_records(by_instance_df)
        for item in by_instance_rows:
            enabled_users_instance = int(item.get("enabled_users") or 0)
            active_users_6m_instance = int(item.get("active_users_6m") or 0)
            developing_users_instance = int(item.get("developing_users") or 0)
            developing_users_6m_instance = int(item.get("developing_users_6m") or 0)
            item["inactive_users_6m"] = max(0, enabled_users_instance - active_users_6m_instance)
            item["active_rate_6m"] = (active_users_6m_instance / enabled_users_instance) if enabled_users_instance else 0.0
            item["contributor_rate_6m"] = (developing_users_6m_instance / enabled_users_instance) if enabled_users_instance else 0.0
            item["inactive_window_months"] = 6

        license_group_definitions = {
            "Creator Licenses": "Entitlements supporting creation and development workflows",
            "Consumer Licenses": "Entitlements supporting consumption and viewing workflows",
            "Admin Licenses": "Entitlements supporting administration and platform oversight",
            "Other Licenses": "Other entitlement categories identified in the license profile data",
        }

        max_license_profile_rows = _df_records(max_license_profile_df)
        max_license_by_profile_norm = {
            str(item.get("profile_norm") or ""): {
                "profile_from_max": item.get("profile_from_max"),
                "max_licenses": int(item.get("max_licenses") or 0),
                "instance_count": int(item.get("instance_count") or 0),
            }
            for item in max_license_profile_rows
            if str(item.get("profile_norm") or "")
        }

        by_license_profile_group_rows = _df_records(by_license_profile_group_df)
        grouped_license_profiles: dict[str, dict[str, Any]] = {}
        for item in by_license_profile_group_rows:
            group_name = str(item.get("license_group") or "Other Licenses")
            profile_name = str(item.get("profile") or "UNKNOWN")
            profile_norm = str(item.get("profile_norm") or "")
            enabled_users = int(item.get("enabled_users") or 0)
            max_license_entry = max_license_by_profile_norm.get(profile_norm, {})
            if group_name not in grouped_license_profiles:
                grouped_license_profiles[group_name] = {
                    "license_group": group_name,
                    "definition": license_group_definitions.get(group_name, "License for other actions"),
                    "enabled_users": 0,
                    "profiles": [],
                }
            grouped_license_profiles[group_name]["enabled_users"] += enabled_users
            grouped_license_profiles[group_name]["profiles"].append(
                {
                    "profile": profile_name,
                    "enabled_users": enabled_users,
                    "max_licenses": max_license_entry.get("max_licenses"),
                    "max_licenses_profile": max_license_entry.get("profile_from_max"),
                    "max_licenses_instance_count": max_license_entry.get("instance_count"),
                }
            )

        grouped_license_profiles_rows = sorted(
            grouped_license_profiles.values(),
            key=lambda item: (-int(item.get("enabled_users") or 0), str(item.get("license_group") or "")),
        )

        return _ok(
            {
                "instanceName": instance_name,
                "licenseFilter": license_filter,
                "activityFilter": activity_filter,
                "meta": {
                    "excludedProfiles": excluded_profiles,
                    "excludedProfilesSource": "pulse_dashboard.configs.terminology_yaml.license_groups.license_consumer",
                },
                "kpis": row,
                "licenseStatusSummary": license_status_summary,
                "byProfile": _df_records(by_profile_df),
                "byLicenseGroup": _df_records(by_license_group_df),
                "byLicenseGroupProfiles": grouped_license_profiles_rows,
                "byInstance": by_instance_rows,
            }
        )

    except Exception as e:
        logger.exception("users kpis failed")
        return _err(str(e), status=500)

@bp.route("/api/build/users/active-monthly")
def build_users_active_monthly():
    """Monthly active users (calendar months) with license filter.

    Definition of "active": any UI activity (viewing or developing actions)
    recorded in `fact_user_activity_daily`.

    Activity filter is applied using the per-instance snapshot table
    `base_users_instance_metadata_history`, scoped to configured Creator vs
    Consumer license-group membership.

    Query parameters:
    - window: this_month|last_3_months|last_12_months (default: last_3_months)
    - months: integer (optional override; 1..24)
    - activityFilter: creator|consumer
    - instance_name: optional filter
    """

    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        standard = _read_standard_project_variables()
        excluded_profiles = _read_user_profile_exclude_consumer(standard)

        months = _parse_window_months(request.args.get("window"))
        if months is None:
            months = int(request.args.get("months") or 3)
        months = max(1, min(24, months))

        activity_filter = _parse_activity_filter(request.args.get("activityFilter"))
        instance_name = _parse_instance_name(request.args.get("instance_name"))

        # Month range (calendar months, including current partial month).
        # We keep these as SQL expressions (not parameters) to avoid interval-typing issues.
        start_month_expr = f"(date_trunc('month', current_date) - INTERVAL {months - 1} MONTH)::DATE"
        end_month_expr = "date_trunc('month', current_date)::DATE"
        next_month_expr = "(date_trunc('month', current_date) + INTERVAL 1 MONTH)::DATE"

        # Build profile exclusion SQL.
        filter_sql_template, filter_params = _resolve_license_filter_clause(activity_filter)
        exclude_sql = _format_license_filter_clause(filter_sql_template, profile_expr="u.users_userprofile")
        exclude_params: list[Any] = list(filter_params)

        instance_sql = ""
        instance_params_aggregate: list[Any] = []
        instance_params_by_instance: list[Any] = []
        instances_filter_sql = ""
        if instance_name:
            instance_sql = " AND a.instance_name = ?"
            instance_params_aggregate = [instance_name]
            # Used twice in the by-instance query:
            # - once in the activity CTE (performance)
            # - once to restrict the generated instance list
            instance_params_by_instance = [instance_name, instance_name]
            instances_filter_sql = " AND instance_name = ?"

        latest_users_cte = (
            "latest_users AS (\n"
            "  SELECT\n"
            "    instance_name,\n"
            "    lower(trim(users_login)) AS login_norm,\n"
            "    users_enabled,\n"
            "    users_userprofile,\n"
            "    ROW_NUMBER() OVER (\n"
            "      PARTITION BY instance_name, lower(trim(users_login))\n"
            "      ORDER BY run_ts DESC\n"
            "    ) AS rn\n"
            "  FROM base_users_instance_metadata_history\n"
            "  WHERE users_login IS NOT NULL\n"
            "    AND length(trim(users_login)) > 0\n"
            "),\n"
        )

        # By-instance series.
        by_instance_df = _query_df(
            (
                "WITH months AS (\n"  # nosec B608 (SQL fragments are static)
                f"  SELECT * FROM generate_series({start_month_expr}, ({next_month_expr} - INTERVAL 1 DAY)::DATE, INTERVAL 1 MONTH) AS t(month_start)\n"  # nosec B608 (month expr is internal)
                "),\n"
                f"{latest_users_cte}"
                "activity AS (\n"
                "  SELECT\n"
                "    date_trunc('month', a.day) AS month_start,\n"
                "    a.instance_name,\n"
                "    a.login_norm\n"
                "  FROM fact_user_activity_daily a\n"
                "  JOIN latest_users u\n"
                "    ON u.instance_name = a.instance_name\n"
                "   AND u.login_norm = a.login_norm\n"
                "  WHERE "
                f"    a.day >= {start_month_expr}\n"  # nosec B608 (month expr is internal)
                f"    AND a.day < {next_month_expr}\n"  # nosec B608 (month expr is internal)
                "    AND u.rn = 1\n"
                "    AND u.users_enabled = 'True'\n"
                f"    {exclude_sql}\n"  # nosec B608 (exclude_sql uses placeholders)
                f"    {instance_sql}\n"  # nosec B608 (instance_sql uses placeholders)
                "),\n"
                "agg AS (\n"
                "  SELECT\n"
                "    month_start,\n"
                "    instance_name,\n"
                "    COUNT(DISTINCT login_norm) AS active_users\n"
                "  FROM activity\n"
                "  GROUP BY 1, 2\n"
                ")\n"
                "SELECT\n"
                "  CAST(m.month_start AS VARCHAR) AS month,\n"
                "  i.instance_name,\n"
                "  COALESCE(a.active_users, 0) AS active_users\n"
                "FROM months m\n"
                "CROSS JOIN (\n"
                "  SELECT DISTINCT instance_name\n"
                "  FROM base_users_instance_metadata_history\n"
                "  WHERE instance_name IS NOT NULL\n"
                f"  {instances_filter_sql}\n"  # nosec B608 (instances_filter_sql uses placeholders)
                ") i\n"
                "LEFT JOIN agg a\n"
                "  ON a.month_start = m.month_start AND a.instance_name = i.instance_name\n"
                "ORDER BY m.month_start, i.instance_name;"
            ),
            [*exclude_params, *instance_params_by_instance],
        )

        # Aggregate series (distinct across instances).
        aggregate_df = _query_df(
            (
                "WITH months AS (\n"  # nosec B608 (SQL fragments are static)
                f"  SELECT * FROM generate_series({start_month_expr}, ({next_month_expr} - INTERVAL 1 DAY)::DATE, INTERVAL 1 MONTH) AS t(month_start)\n"  # nosec B608 (month expr is internal)
                "),\n"
                f"{latest_users_cte}"
                "activity AS (\n"
                "  SELECT\n"
                "    date_trunc('month', a.day) AS month_start,\n"
                "    a.instance_name,\n"
                "    a.login_norm\n"
                "  FROM fact_user_activity_daily a\n"
                "  JOIN latest_users u\n"
                "    ON u.instance_name = a.instance_name\n"
                "   AND u.login_norm = a.login_norm\n"
                "  WHERE "
                f"    a.day >= {start_month_expr}\n"  # nosec B608 (month expr is internal)
                f"    AND a.day < {next_month_expr}\n"  # nosec B608 (month expr is internal)
                "    AND u.rn = 1\n"
                "    AND u.users_enabled = 'True'\n"
                f"    {exclude_sql}\n"  # nosec B608 (exclude_sql uses placeholders)
                f"    {instance_sql}\n"  # nosec B608 (instance_sql uses placeholders)
                "),\n"
                "agg AS (\n"
                "  SELECT\n"
                "    month_start,\n"
                "    COUNT(DISTINCT concat(instance_name, '::', login_norm)) AS active_users\n"
                "  FROM activity\n"
                "  GROUP BY 1\n"
                ")\n"
                "SELECT\n"
                "  CAST(m.month_start AS VARCHAR) AS month,\n"
                "  COALESCE(a.active_users, 0) AS active_users\n"
                "FROM months m\n"
                "LEFT JOIN agg a\n"
                "  ON a.month_start = m.month_start\n"
                "ORDER BY m.month_start;"
            ),
            [*exclude_params, *instance_params_aggregate],
        )

        return _ok(
            {
                "window": request.args.get("window") or None,
                "months": months,
                "activityFilter": activity_filter,
                "instanceName": instance_name,
                "meta": {
                    "excludedProfiles": excluded_profiles if activity_filter == "license_consumer" else [],
                    "excludedProfilesSource": "pulse_dashboard.configs.terminology_yaml.license_groups.license_consumer",
                },
                "byInstance": _df_records(by_instance_df),
                "aggregate": _df_records(aggregate_df),
            }
        )

    except Exception as e:
        logger.exception("users active monthly failed")
        return _err(str(e), status=500)


@bp.route("/api/build/users/formal-mau-monthly")
def build_users_formal_mau_monthly():
    """Formal monthly active users (calendar months).

    Definition of "active": at least one qualifying `application-open` event
    recorded in `fact_formal_mau_daily`, with enabled/non-trial filtering
    applied by `final_build_formal_mau_daily`.

    Query parameters:
    - window: this_month|last_3_months|last_12_months (default: last_3_months)
    - months: integer (optional override; 1..24)
    - instance_name: optional filter
    """

    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        months = _parse_window_months(request.args.get("window"))
        if months is None:
            months = int(request.args.get("months") or 3)
        months = max(1, min(24, months))

        instance_name = _parse_instance_name(request.args.get("instance_name"))

        if not _duckdb_relation_exists(_query_df, "final_build_formal_mau_daily"):
            return _ok(
                {
                    "window": request.args.get("window") or None,
                    "months": months,
                    "instanceName": instance_name,
                    "latestMonth": None,
                    "byInstance": [],
                    "aggregate": [],
                    "available": False,
                    "reason": "formal_mau_view_missing",
                }
            )

        start_month_expr = f"(date_trunc('month', current_date) - INTERVAL {months - 1} MONTH)::DATE"
        next_month_expr = "(date_trunc('month', current_date) + INTERVAL 1 MONTH)::DATE"

        instance_sql = ""
        instance_params_aggregate: list[Any] = []
        instance_params_by_instance: list[Any] = []
        instances_filter_sql = ""
        if instance_name:
            instance_sql = " AND f.instance_name = ?"
            instance_params_aggregate = [instance_name]
            instance_params_by_instance = [instance_name, instance_name]
            instances_filter_sql = " AND instance_name = ?"

        by_instance_df = _query_df(
            f"""
            WITH months AS (
              SELECT *
              FROM generate_series({start_month_expr}, ({next_month_expr} - INTERVAL 1 DAY)::DATE, INTERVAL 1 MONTH) AS t(month_start)
            ),
            activity AS (
              SELECT
                date_trunc('month', f.day) AS month_start,
                f.instance_name,
                f.login_norm
              FROM final_build_formal_mau_daily f
              WHERE f.day >= {start_month_expr}
                AND f.day < {next_month_expr}
                {instance_sql}
            ),
            agg AS (
              SELECT
                month_start,
                instance_name,
                COUNT(DISTINCT login_norm) AS active_users
              FROM activity
              GROUP BY 1, 2
            )
            SELECT
              CAST(m.month_start AS VARCHAR) AS month,
              i.instance_name,
              COALESCE(a.active_users, 0) AS active_users
            FROM months m
            CROSS JOIN (
              SELECT DISTINCT instance_name
              FROM base_users_instance_metadata_history
              WHERE instance_name IS NOT NULL
              {instances_filter_sql}
            ) i
            LEFT JOIN agg a
              ON a.month_start = m.month_start AND a.instance_name = i.instance_name
            ORDER BY m.month_start, i.instance_name;
            """,  # nosec B608 (month expr and instance_sql are internal/generated)
            instance_params_by_instance,
        )

        aggregate_df = _query_df(
            f"""
            WITH months AS (
              SELECT *
              FROM generate_series({start_month_expr}, ({next_month_expr} - INTERVAL 1 DAY)::DATE, INTERVAL 1 MONTH) AS t(month_start)
            ),
            activity AS (
              SELECT
                date_trunc('month', f.day) AS month_start,
                f.instance_name,
                f.login_norm
              FROM final_build_formal_mau_daily f
              WHERE f.day >= {start_month_expr}
                AND f.day < {next_month_expr}
                {instance_sql}
            ),
            agg AS (
              SELECT
                month_start,
                COUNT(DISTINCT concat(instance_name, '::', login_norm)) AS active_users
              FROM activity
              GROUP BY 1
            )
            SELECT
              CAST(m.month_start AS VARCHAR) AS month,
              COALESCE(a.active_users, 0) AS active_users
            FROM months m
            LEFT JOIN agg a
              ON a.month_start = m.month_start
            ORDER BY m.month_start;
            """,  # nosec B608 (month expr and instance_sql are internal/generated)
            instance_params_aggregate,
        )

        license_group_case_sql = _license_group_case_sql("user_profile")
        latest_month_profiles_df = _query_df(
            f"""
            WITH latest_users AS (
              SELECT
                instance_name,
                lower(trim(users_login)) AS login_norm,
                coalesce(nullif(trim(users_userprofile), ''), 'UNKNOWN') AS user_profile,
                ROW_NUMBER() OVER (
                  PARTITION BY instance_name, lower(trim(users_login))
                  ORDER BY run_ts DESC
                ) AS rn
              FROM base_users_instance_metadata_history
              WHERE users_login IS NOT NULL
                AND length(trim(users_login)) > 0
            ),
            latest_activity AS (
              SELECT
                f.instance_name,
                f.login_norm,
                u.user_profile
              FROM final_build_formal_mau_daily f
              JOIN latest_users u
                ON u.instance_name = f.instance_name
               AND u.login_norm = f.login_norm
               AND u.rn = 1
              WHERE f.day >= date_trunc('month', current_date)::DATE
                AND f.day < {next_month_expr}
                {instance_sql}
            )
            SELECT
              {license_group_case_sql} AS license_group,
              user_profile AS userProfile,
              COUNT(DISTINCT concat(instance_name, '::', login_norm)) AS active_users
            FROM latest_activity
            GROUP BY 1, 2
            ORDER BY active_users DESC, license_group, userProfile;
            """,  # nosec B608 (month expr, case sql, and instance_sql are internal/generated)
            instance_params_aggregate,
        )

        latest_month_instance_profiles_df = _query_df(
            f"""
            WITH latest_users AS (
              SELECT
                instance_name,
                lower(trim(users_login)) AS login_norm,
                coalesce(nullif(trim(users_userprofile), ''), 'UNKNOWN') AS user_profile,
                ROW_NUMBER() OVER (
                  PARTITION BY instance_name, lower(trim(users_login))
                  ORDER BY run_ts DESC
                ) AS rn
              FROM base_users_instance_metadata_history
              WHERE users_login IS NOT NULL
                AND length(trim(users_login)) > 0
            ),
            latest_activity AS (
              SELECT
                f.instance_name,
                f.login_norm,
                u.user_profile
              FROM final_build_formal_mau_daily f
              JOIN latest_users u
                ON u.instance_name = f.instance_name
               AND u.login_norm = f.login_norm
               AND u.rn = 1
              WHERE f.day >= date_trunc('month', current_date)::DATE
                AND f.day < {next_month_expr}
                {instance_sql}
            )
            SELECT
              instance_name,
              {license_group_case_sql} AS license_group,
              user_profile AS userProfile,
              COUNT(DISTINCT login_norm) AS active_users
            FROM latest_activity
            GROUP BY 1, 2, 3
            ORDER BY instance_name, active_users DESC, license_group, userProfile;
            """,  # nosec B608 (month expr, case sql, and instance_sql are internal/generated)
            instance_params_aggregate,
        )

        aggregate_rows = _df_records(aggregate_df)
        latest_month = aggregate_rows[-1] if aggregate_rows else None

        return _ok(
            {
                "window": request.args.get("window") or None,
                "months": months,
                "instanceName": instance_name,
                "latestMonth": latest_month,
                "byInstance": _df_records(by_instance_df),
                "byProfile": _df_records(latest_month_profiles_df),
                "byInstanceProfile": _df_records(latest_month_instance_profiles_df),
                "aggregate": aggregate_rows,
                "available": True,
            }
        )

    except Exception as e:
        logger.exception("users formal mau monthly failed")
        return _err(str(e), status=500)


@bp.route("/api/build/users/leaderboard")
def build_users_leaderboard():
    """Return leaderboards for consuming and creating activity."""

    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        # Prefer calendar-month windows if provided.
        months = _parse_window_months(request.args.get("window"))
        if months is None:
            months = int(request.args.get("months") or 0) or None

        if months is not None:
            months = max(1, min(24, months))
            where = [_window_months_where_sql(months=months)]
            params: list[Any] = []
        else:
            days = int(request.args.get("days") or 30)
            days = max(1, min(365, days))
            where = ["day >= current_date - ?::INTEGER"]
            params = [days]

        instance_name = _parse_instance_name(request.args.get("instance_name"))
        activity_filter = _parse_activity_filter(request.args.get("activityFilter"))

        standard = _read_standard_project_variables()
        excluded_profiles = _read_user_profile_exclude_consumer(standard)

        activity_filter_sql = ""
        activity_filter_params: list[Any] = []
        if activity_filter == "license_consumer" and excluded_profiles:
            activity_filter_sql = (
                f" AND coalesce(upper(trim(users_userprofile)), '') NOT IN ({_sql_placeholders(len(excluded_profiles))})"
            )
            activity_filter_params = list(excluded_profiles)

        if instance_name:
            where.append("instance_name = ?")
            params.append(instance_name)

        where_sql = " WHERE " + " AND ".join(where)

        directory_cte = _users_directory_cte_sql()

        base_sql = (
            "WITH agg AS (\n"
            "  SELECT\n"
            "    login_norm,\n"
            "    SUM(viewing_actions_count) AS viewing,\n"
            "    SUM(developing_actions_count) AS developing,\n"
            "    MAX(last_activity_at) AS last_activity_at,\n"
            "    COUNT(DISTINCT instance_name) AS instances\n"
            "  FROM fact_user_activity_daily\n"
            f"  {where_sql}\n"  # nosec B608 (where_sql uses placeholders)
            "  GROUP BY 1\n"
            "),\n"
            + directory_cte
        )

        activity_predicates = []
        if activity_filter == "license_creator":
            activity_predicates.append("coalesce(a.developing, 0) > 0")
        elif activity_filter == "license_consumer":
            activity_predicates.append("coalesce(a.viewing, 0) > 0")
            if activity_filter_sql:
                activity_predicates.append(f"1 = 1{activity_filter_sql}")

        activity_filter_clause = ""
        if activity_predicates:
            activity_filter_clause = "WHERE " + " AND ".join(activity_predicates) + "\n"

        viewing_df = _query_df(
            (
                base_sql
                + "SELECT\n"  # nosec B608 (SQL fragments are static)
                + "  a.login_norm AS login,\n"
                + "  u.display_name AS displayName,\n"
                + "  u.email AS email,\n"
                + "  u.user_profile AS userProfile,\n"
                + "  u.enabled AS enabled,\n"
                + "  a.viewing AS value,\n"
                + "  a.developing AS developing,\n"
                + "  a.instances AS instances,\n"
                + "  a.last_activity_at AS lastActivityAt\n"
                + "FROM agg a\n"
                + "LEFT JOIN directory u ON u.login_norm = a.login_norm\n"
                + activity_filter_clause
                + "ORDER BY value DESC NULLS LAST\n"
                + "LIMIT 50;"
            ),
            [*params, *activity_filter_params],
        )

        developing_df = _query_df(
            (
                base_sql
                + "SELECT\n"  # nosec B608 (SQL fragments are static)
                + "  a.login_norm AS login,\n"
                + "  u.display_name AS displayName,\n"
                + "  u.email AS email,\n"
                + "  u.user_profile AS userProfile,\n"
                + "  u.enabled AS enabled,\n"
                + "  a.developing AS value,\n"
                + "  a.viewing AS viewing,\n"
                + "  a.instances AS instances,\n"
                + "  a.last_activity_at AS lastActivityAt\n"
                + "FROM agg a\n"
                + "LEFT JOIN directory u ON u.login_norm = a.login_norm\n"
                + activity_filter_clause
                + "ORDER BY value DESC NULLS LAST\n"
                + "LIMIT 50;"
            ),
            [*params, *activity_filter_params],
        )

        payload: dict[str, Any] = {
            "instanceName": instance_name,
            "activityFilter": activity_filter,
            "viewing": _df_records(viewing_df),
            "developing": _df_records(developing_df),
        }
        if months is not None:
            payload["months"] = months
        else:
            payload["days"] = int(days or 30)
        return _ok(payload)

    except Exception as e:
        logger.exception("users leaderboard failed")
        return _err(str(e), status=500)


@bp.route("/api/build/users/creator-risk")
def build_users_creator_risk():
    """Return creator-license risk lists based on Pulse trailing 6-month guidance."""

    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        instance_name = _parse_instance_name(request.args.get("instance_name"))
        delinquent_page = max(1, int(request.args.get("delinquentPage") or 1))
        underutilized_page = max(1, int(request.args.get("underutilizedPage") or 1))
        page_size = 10
        delinquent_offset = (delinquent_page - 1) * page_size
        underutilized_offset = (underutilized_page - 1) * page_size

        latest_instance_sql = ""
        activity_instance_sql = ""
        instance_params: list[Any] = []
        if instance_name:
            latest_instance_sql = " AND instance_name = ?"
            activity_instance_sql = " AND instance_name = ?"
            instance_params = [instance_name]

        license_group_case_sql = _license_group_case_sql("l.user_profile")
        six_month_start_expr = "(current_date - INTERVAL 6 MONTH)::DATE"

        common_cte = (
            "WITH latest AS (\n"  # nosec B608 (SQL fragments use validated internal expressions and placeholders)
            "  SELECT\n"
            "    instance_name,\n"
            "    lower(trim(users_login)) AS login_norm,\n"
            "    trim(users_login) AS login,\n"
            "    coalesce(trim(users_displayname), trim(users_login)) AS display_name,\n"
            "    coalesce(nullif(trim(users_userprofile), ''), 'UNKNOWN') AS user_profile,\n"
            "    users_enabled,\n"
            "    run_ts,\n"
            "    ROW_NUMBER() OVER (\n"
            "      PARTITION BY instance_name, lower(trim(users_login))\n"
            "      ORDER BY run_ts DESC\n"
            "    ) AS rn\n"
            "  FROM base_users_instance_metadata_history\n"
            "  WHERE users_login IS NOT NULL AND length(trim(users_login)) > 0\n"
            f"    {latest_instance_sql}\n"  # nosec B608 (instance filter uses placeholders)
            "),\n"
            "activity_6m AS (\n"
            "  SELECT\n"
            "    instance_name,\n"
            "    lower(trim(login_norm)) AS login_norm,\n"
            "    SUM(CASE WHEN day >= "
            f"{six_month_start_expr}"  # nosec B608 (internal fixed date expression)
            " THEN viewing_actions_count ELSE 0 END) AS viewing_6m,\n"
            "    SUM(CASE WHEN day >= "
            f"{six_month_start_expr}"  # nosec B608 (internal fixed date expression)
            " THEN developing_actions_count ELSE 0 END) AS developing_6m,\n"
            "    MAX(last_activity_at) AS last_activity_at\n"
            "  FROM fact_user_activity_daily\n"
            "  WHERE 1 = 1"
            f"{activity_instance_sql}\n"  # nosec B608 (instance filter uses placeholders)
            "  GROUP BY 1, 2\n"
            "),\n"
            "creator_latest AS (\n"
            "  SELECT\n"
            "    l.instance_name,\n"
            "    l.login_norm,\n"
            "    l.login,\n"
            "    l.display_name,\n"
            "    l.user_profile,\n"
            "    l.users_enabled,\n"
            "    coalesce(a.viewing_6m, 0) AS viewing_6m,\n"
            "    coalesce(a.developing_6m, 0) AS developing_6m,\n"
            "    a.last_activity_at AS last_activity_at\n"
            "  FROM latest l\n"
            "  LEFT JOIN activity_6m a ON a.instance_name = l.instance_name AND a.login_norm = l.login_norm\n"
            "  WHERE l.rn = 1\n"
            "    AND l.users_enabled = 'True'\n"
            f"    AND ({license_group_case_sql}) = 'Creator Licenses'\n"
            ")\n"
        )

        delinquent_total_df = _query_df(
            (
                common_cte
                + "SELECT COUNT(*) AS total_rows FROM creator_latest WHERE viewing_6m = 0 AND developing_6m = 0;"  # nosec B608
            ),
            [*instance_params, *instance_params],
        )
        delinquent_rows_df = _query_df(
            (
                common_cte
                + "SELECT\n"  # nosec B608
                + "  instance_name AS instanceName,\n"
                + "  login,\n"
                + "  login_norm AS loginNorm,\n"
                + "  display_name AS displayName,\n"
                + "  user_profile AS userProfile,\n"
                + "  viewing_6m AS viewing6m,\n"
                + "  developing_6m AS developing6m,\n"
                + "  CAST(last_activity_at AS VARCHAR) AS lastActivityAt\n"
                + "FROM creator_latest\n"
                + "WHERE viewing_6m = 0 AND developing_6m = 0\n"
                + "ORDER BY coalesce(last_activity_at, TIMESTAMP '1900-01-01') DESC, display_name, login\n"
                + "LIMIT ? OFFSET ?;"
            ),
            [*instance_params, *instance_params, page_size, delinquent_offset],
        )

        under_total_df = _query_df(
            (
                common_cte
                + "SELECT COUNT(*) AS total_rows FROM creator_latest WHERE viewing_6m > 0 AND (developing_6m::DOUBLE / viewing_6m::DOUBLE) < 0.05;"  # nosec B608
            ),
            [*instance_params, *instance_params],
        )
        under_rows_df = _query_df(
            (
                common_cte
                + "SELECT\n"  # nosec B608
                + "  instance_name AS instanceName,\n"
                + "  login,\n"
                + "  login_norm AS loginNorm,\n"
                + "  display_name AS displayName,\n"
                + "  user_profile AS userProfile,\n"
                + "  viewing_6m AS viewing6m,\n"
                + "  developing_6m AS developing6m,\n"
                + "  (developing_6m::DOUBLE / viewing_6m::DOUBLE) AS developingToViewingRatio,\n"
                + "  CAST(last_activity_at AS VARCHAR) AS lastActivityAt\n"
                + "FROM creator_latest\n"
                + "WHERE viewing_6m > 0 AND (developing_6m::DOUBLE / viewing_6m::DOUBLE) < 0.05\n"
                + "ORDER BY developingToViewingRatio ASC, viewing_6m DESC, display_name, login\n"
                + "LIMIT ? OFFSET ?;"
            ),
            [*instance_params, *instance_params, page_size, underutilized_offset],
        )

        delinquent_total = int((_df_records(delinquent_total_df)[0] if len(delinquent_total_df) else {}).get("total_rows") or 0)
        under_total = int((_df_records(under_total_df)[0] if len(under_total_df) else {}).get("total_rows") or 0)

        return _ok(
            {
                "instanceName": instance_name,
                "meta": {
                    "windowMonths": 6,
                    "ratioThreshold": 0.05,
                    "guidanceLabel": "Pulse guidance uses a fixed trailing 6-month review window for these risk signals.",
                },
                "delinquentCreators": {
                    "page": delinquent_page,
                    "pageSize": page_size,
                    "totalRows": delinquent_total,
                    "totalPages": max(1, math.ceil(delinquent_total / page_size)) if delinquent_total else 1,
                    "rows": _df_records(delinquent_rows_df),
                },
                "underutilizedCreators": {
                    "page": underutilized_page,
                    "pageSize": page_size,
                    "totalRows": under_total,
                    "totalPages": max(1, math.ceil(under_total / page_size)) if under_total else 1,
                    "rows": _df_records(under_rows_df),
                },
            }
        )

    except Exception as e:
        logger.exception("users creator risk failed")
        return _err(str(e), status=500)

@bp.route("/api/build/users/segments")
def build_users_segments():
    """Return user behavior segments for the selected window.

    Segments are based on activity in `fact_user_activity_daily`:
    - viewer_only: viewing > 0 and developing = 0
    - mixed: viewing > 0 and developing > 0
    - inactive: enabled users with neither in the selected window
    """

    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        standard = _read_standard_project_variables()
        excluded_profiles = _read_user_profile_exclude_consumer(standard)

        months, days = _resolve_window_params()
        activity_filter = _parse_activity_filter(request.args.get("activityFilter"))
        instance_name = _parse_instance_name(request.args.get("instance_name"))

        exclude_sql = ""
        exclude_params: list[Any] = []
        if activity_filter == "license_consumer" and excluded_profiles:
            exclude_sql = (
                f" AND coalesce(upper(trim(l.users_userprofile)), '') NOT IN ({_sql_placeholders(len(excluded_profiles))})"
            )
            exclude_params = list(excluded_profiles)

        latest_instance_sql = ""
        activity_instance_sql = ""
        instance_params: list[Any] = []
        if instance_name:
            latest_instance_sql = " AND instance_name = ?"
            activity_instance_sql = " AND instance_name = ?"
            instance_params = [instance_name]

        if months is not None:
            activity_window_sql = _window_months_where_sql(months=months)
            activity_params: list[Any] = []
        else:
            activity_window_sql = "day >= current_date - ?::INTEGER"
            activity_params = [int(days or 30)]

        df = _query_df(
            (
                "WITH latest AS (\n"
                "  SELECT\n"
                "    instance_name,\n"
                "    lower(trim(users_login)) AS login_norm,\n"
                "    users_enabled,\n"
                "    users_userprofile,\n"
                "    ROW_NUMBER() OVER (\n"
                "      PARTITION BY instance_name, lower(trim(users_login))\n"
                "      ORDER BY run_ts DESC\n"
                "    ) AS rn\n"
                "  FROM base_users_instance_metadata_history\n"
                "  WHERE users_login IS NOT NULL AND length(trim(users_login)) > 0\n"
                f"    {latest_instance_sql}\n"  # nosec B608
                "),\n"
                "activity AS (\n"
                "  SELECT\n"
                "    lower(trim(login_norm)) AS login_norm,\n"
                "    SUM(viewing_actions_count) AS viewing,\n"
                "    SUM(developing_actions_count) AS developing\n"
                "  FROM fact_user_activity_daily\n"
                f"  WHERE {activity_window_sql}\n"  # nosec B608
                f"    {activity_instance_sql}\n"  # nosec B608
                "  GROUP BY 1\n"
                ")\n"
                "SELECT\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True'" + f"{exclude_sql}) AS enabled_users,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.viewing, 0) > 0 AND coalesce(a.developing, 0) = 0" + f"{exclude_sql}) AS viewer_only_users,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.developing, 0) > 0 AND coalesce(a.viewing, 0) = 0" + f"{exclude_sql}) AS developer_only_users,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.viewing, 0) > 0 AND coalesce(a.developing, 0) > 0" + f"{exclude_sql}) AS mixed_users,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.viewing, 0) > coalesce(a.developing, 0) AND coalesce(a.developing, 0) > 0" + f"{exclude_sql}) AS viewer_dominant_users,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.developing, 0) > coalesce(a.viewing, 0) AND coalesce(a.viewing, 0) > 0" + f"{exclude_sql}) AS developer_dominant_users,\n"
                "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.viewing, 0) = coalesce(a.developing, 0) AND coalesce(a.viewing, 0) > 0" + f"{exclude_sql}) AS balanced_mixed_users\n"
                "FROM latest l\n"
                "LEFT JOIN activity a ON a.login_norm = l.login_norm\n"
                "WHERE l.rn = 1;"
            ),
            [*instance_params, *activity_params, *instance_params, *exclude_params],
        )

        row = _df_records(df)[0] if len(df.index) else {}
        enabled_users = int(row.get("enabled_users") or 0)
        viewer_only_users = int(row.get("viewer_only_users") or 0)
        developer_only_users = int(row.get("developer_only_users") or 0)
        mixed_users = int(row.get("mixed_users") or 0)
        viewer_dominant_users = int(row.get("viewer_dominant_users") or 0)
        developer_dominant_users = int(row.get("developer_dominant_users") or 0)
        balanced_mixed_users = int(row.get("balanced_mixed_users") or 0)
        inactive_users = max(0, enabled_users - viewer_only_users - developer_only_users - mixed_users)

        segments = [
            {"label": "Viewer only", "value": viewer_only_users},
            {"label": "Mixed", "value": mixed_users},
            {"label": "Inactive", "value": inactive_users},
        ]
        dominance_segments = [
            {"label": "Viewer dominant", "value": viewer_dominant_users},
            {"label": "Developer dominant", "value": developer_dominant_users},
            {"label": "Balanced mixed", "value": balanced_mixed_users},
        ]

        payload: dict[str, Any] = {
            "instanceName": instance_name,
            "activityFilter": activity_filter,
            "segments": segments,
            "dominanceSegments": dominance_segments,
            "totals": {
                "enabledUsers": enabled_users,
                "viewerOnlyUsers": viewer_only_users,
                "developerOnlyUsers": developer_only_users,
                "mixedUsers": mixed_users,
                "inactiveUsers": inactive_users,
                "viewerDominantUsers": viewer_dominant_users,
                "developerDominantUsers": developer_dominant_users,
                "balancedMixedUsers": balanced_mixed_users,
            },
        }
        if months is not None:
            payload["months"] = months
        else:
            payload["days"] = int(days or 30)
        return _ok(payload)

    except Exception as e:
        logger.exception("users segments failed")
        return _err(str(e), status=500)


@bp.route("/api/build/users/stickiness")
def build_users_stickiness():
    """Return monthly stickiness and reactivation metrics.

    Stickiness here is the monthly active rate among currently enabled users.
    Reactivated users are active this month after being inactive in the prior month.
    """

    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        standard = _read_standard_project_variables()
        excluded_profiles = _read_user_profile_exclude_consumer(standard)

        months = _parse_window_months(request.args.get("window"))
        if months is None:
            months = int(request.args.get("months") or 6)
        months = max(2, min(24, months))

        activity_filter = _parse_activity_filter(request.args.get("activityFilter"))
        instance_name = _parse_instance_name(request.args.get("instance_name"))

        start_month_expr = f"(date_trunc('month', current_date) - INTERVAL {months - 1} MONTH)::DATE"
        next_month_expr = "(date_trunc('month', current_date) + INTERVAL 1 MONTH)::DATE"

        filter_sql_template, filter_params = _resolve_license_filter_clause(activity_filter)
        exclude_sql = _format_license_filter_clause(filter_sql_template, profile_expr="u.users_userprofile")
        exclude_params: list[Any] = list(filter_params)

        instance_sql_users = ""
        instance_sql_activity = ""
        instance_params_users: list[Any] = []
        instance_params_activity: list[Any] = []
        if instance_name:
            instance_sql_users = " AND u.instance_name = ?"
            instance_sql_activity = " AND a.instance_name = ?"
            instance_params_users = [instance_name]
            instance_params_activity = [instance_name]

        df = _query_df(
            (
                "WITH months AS (\n"
                f"  SELECT * FROM generate_series({start_month_expr}, ({next_month_expr} - INTERVAL 1 DAY)::DATE, INTERVAL 1 MONTH) AS t(month_start)\n"
                "),\n"
                "eligible AS (\n"
                "  SELECT\n"
                "    m.month_start,\n"
                "    COUNT(DISTINCT lower(trim(u.users_login))) AS enabled_users\n"
                "  FROM months m\n"
                "  JOIN base_users_instance_metadata_history u ON TRUE\n"
                "  WHERE u.users_login IS NOT NULL\n"
                "    AND length(trim(u.users_login)) > 0\n"
                "    AND u.users_enabled = 'True'\n"
                f"    {exclude_sql}\n"  # nosec B608
                f"    {instance_sql_users}\n"  # nosec B608
                "  GROUP BY 1\n"
                "),\n"
                "activity AS (\n"
                "  SELECT\n"
                "    date_trunc('month', a.day) AS month_start,\n"
                "    a.login_norm\n"
                "  FROM fact_user_activity_daily a\n"
                "  JOIN base_users_instance_metadata_history u\n"
                "    ON u.instance_name = a.instance_name\n"
                "   AND lower(trim(u.users_login)) = a.login_norm\n"
                "  WHERE a.day >= " + start_month_expr + "\n"
                "    AND a.day < " + next_month_expr + "\n"
                "    AND u.users_enabled = 'True'\n"
                f"    {exclude_sql}\n"  # nosec B608
                f"    {instance_sql_activity}\n"  # nosec B608
                "  GROUP BY 1, 2\n"
                "),\n"
                "activity_with_prev AS (\n"
                "  SELECT\n"
                "    month_start,\n"
                "    login_norm,\n"
                "    LAG(month_start) OVER (PARTITION BY login_norm ORDER BY month_start) AS prev_month_start\n"
                "  FROM activity\n"
                "),\n"
                "monthly AS (\n"
                "  SELECT\n"
                "    m.month_start,\n"
                "    COUNT(DISTINCT a.login_norm) AS active_users,\n"
                "    COUNT(DISTINCT CASE WHEN a.prev_month_start = m.month_start - INTERVAL 1 MONTH THEN a.login_norm END) AS retained_users,\n"
                "    COUNT(DISTINCT CASE WHEN a.prev_month_start IS NOT NULL AND a.prev_month_start < m.month_start - INTERVAL 1 MONTH THEN a.login_norm END) AS reactivated_users,\n"
                "    COUNT(DISTINCT CASE WHEN a.prev_month_start IS NULL THEN a.login_norm END) AS new_active_users\n"
                "  FROM months m\n"
                "  LEFT JOIN activity_with_prev a ON a.month_start = m.month_start\n"
                "  GROUP BY 1\n"
                ")\n"
                "SELECT\n"
                "  CAST(m.month_start AS VARCHAR) AS month,\n"
                "  COALESCE(m.active_users, 0) AS activeUsers,\n"
                "  COALESCE(e.enabled_users, 0) AS enabledUsers,\n"
                "  COALESCE(m.retained_users, 0) AS retainedUsers,\n"
                "  COALESCE(m.reactivated_users, 0) AS reactivatedUsers,\n"
                "  COALESCE(m.new_active_users, 0) AS newActiveUsers,\n"
                "  CASE WHEN COALESCE(e.enabled_users, 0) > 0 THEN COALESCE(m.active_users, 0) * 1.0 / e.enabled_users ELSE 0 END AS activeRate\n"
                "FROM monthly m\n"
                "LEFT JOIN eligible e ON e.month_start = m.month_start\n"
                "ORDER BY m.month_start;"
            ),
            [*exclude_params, *instance_params_users, *exclude_params, *instance_params_activity],
        )

        rows = _df_records(df)
        latest = rows[-1] if rows else {}
        return _ok(
            {
                "months": months,
                "instanceName": instance_name,
                "activityFilter": activity_filter,
                "series": rows,
                "latest": latest,
            }
        )

    except Exception as e:
        logger.exception("users stickiness failed")
        return _err(str(e), status=500)


@bp.route("/api/build/users/<login>")
def build_user_detail(login: str):
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        login_norm = _parse_login_norm(login)
        if not login_norm:
            return _err("Missing login")

        months, days = _resolve_window_params()

        if months is not None:
            where = [_window_months_where_sql(months=months), "login_norm = ?"]
            params: list[Any] = [login_norm]
        else:
            where = ["day >= current_date - ?::INTEGER", "login_norm = ?"]
            params = [int(days or 30), login_norm]

        instance_name = _parse_instance_name(request.args.get("instance_name"))

        if instance_name:
            where.append("instance_name = ?")
            params.append(instance_name)

        where_sql = " WHERE " + " AND ".join(where)

        summary_df = _query_df(
            (
                "SELECT\n"
                "  SUM(viewing_actions_count) AS viewing,\n"
                "  SUM(developing_actions_count) AS developing,\n"
                "  MAX(last_activity_at) AS last_activity_at,\n"
                "  COUNT(DISTINCT instance_name) AS instances,\n"
                "  COUNT(DISTINCT project_key) AS projects\n"
                "FROM fact_user_activity_project_daily\n"
                f"{where_sql};"  # nosec B608 (where_sql uses placeholders)
            ),
            params,
        )
        summary = _df_records(summary_df)[0] if len(summary_df) else None
        if summary is not None:
            viewing = int(summary.get("viewing") or 0)
            developing = int(summary.get("developing") or 0)
            total_actions = viewing + developing
            summary["viewing"] = viewing
            summary["developing"] = developing
            summary["total_actions"] = total_actions
            summary["activity_mode"] = (
                "developer"
                if developing > viewing
                else "viewer"
                if viewing > developing
                else "balanced"
                if total_actions > 0
                else "inactive"
            )
            summary["developing_share"] = (developing / total_actions) if total_actions else 0.0
            summary["viewing_share"] = (viewing / total_actions) if total_actions else 0.0
            if months is not None:
                summary["months"] = months
            else:
                summary["days"] = int(days or 30)

        directory_params: list[Any] = [login_norm]
        if instance_name:
            directory_params.append(instance_name)

        instances_df = _query_df(
            _user_detail_instances_sql(include_instance_filter=bool(instance_name)),
            directory_params,
        )
        directory_records = [_normalize_user_directory_record(row) for row in _df_records(instances_df)]
        user = max(directory_records, key=_user_directory_record_rank_key) if directory_records else None

        # Daily activity trend (UI only)
        daily_df = _query_df(
            (
                "SELECT\n"
                "  CAST(day AS VARCHAR) AS label,\n"
                "  SUM(viewing_actions_count) AS viewing,\n"
                "  SUM(developing_actions_count) AS developing\n"
                "FROM fact_user_activity_daily\n"
                f"{where_sql}\n"  # nosec B608 (where_sql uses placeholders)
                "GROUP BY 1\n"
                "ORDER BY 1;"
            ),
            params,
        )

        monthly_df = _query_df(
            (
                "SELECT\n"
                "  CAST(date_trunc('month', day) AS VARCHAR) AS month,\n"
                "  SUM(viewing_actions_count) AS viewing,\n"
                "  SUM(developing_actions_count) AS developing\n"
                "FROM fact_user_activity_daily\n"
                f"{where_sql}\n"  # nosec B608
                "GROUP BY 1\n"
                "ORDER BY 1;"
            ),
            params,
        )

        return _ok(
            {
                "user": user,
                "instances": directory_records,
                "directoryRecords": directory_records,
                "summary": summary,
                "activityDaily": _df_records(daily_df),
                "activityMonthly": _df_records(monthly_df),
            }
        )

    except Exception as e:
        logger.exception("user detail failed")
        return _err(str(e), status=500)


@bp.route("/api/build/users/<login>/top-projects")
def build_user_top_projects(login: str):
    try:
        _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
        _ensure_ready_if_enabled()

        login_norm = _parse_login_norm(login)
        if not login_norm:
            return _err("Missing login")

        months, days = _resolve_window_params()

        if months is not None:
            where = [_window_months_where_sql(months=months), "login_norm = ?"]
            params: list[Any] = [login_norm]
        else:
            where = ["day >= current_date - ?::INTEGER", "login_norm = ?"]
            params = [int(days or 30), login_norm]

        limit = int(_parse_int_arg("limit", default=10, minimum=1, maximum=100) or 10)

        instance_name = _parse_instance_name(request.args.get("instance_name"))

        if instance_name:
            where.append("instance_name = ?")
            params.append(instance_name)

        where_sql = " WHERE " + " AND ".join(where)

        df = _query_df(
            (
                "SELECT\n"
                "  instance_name AS instanceName,\n"
                "  project_key AS projectKey,\n"
                "  SUM(viewing_actions_count) AS viewing,\n"
                "  SUM(developing_actions_count) AS developing,\n"
                "  MAX(last_activity_at) AS lastActivityAt\n"
                "FROM fact_user_activity_project_daily\n"
                f"{where_sql}\n"  # nosec B608 (where_sql uses placeholders)
                "GROUP BY 1, 2\n"
                "ORDER BY developing DESC NULLS LAST, viewing DESC NULLS LAST\n"
                "LIMIT ?;"
            ),
            [*params, limit],
        )

        payload: dict[str, Any] = {"rows": _df_records(df)}
        if months is not None:
            payload["months"] = months
        else:
            payload["days"] = int(days or 30)
        return _ok(payload)

    except Exception as e:
        logger.exception("user top projects failed")
        return _err(str(e), status=500)


@bp.route("/api/export/users-report.pdf")
def export_users_report_pdf():
    try:
        _require_organization_access()
        instance_name = _parse_instance_name(request.args.get("instance_name"))
        license_filter = request.args.get("licenseFilter") or None
        activity_filter = request.args.get("activityFilter") or None
        months = _parse_window_months(request.args.get("window"))
        if months is None:
            months = int(request.args.get("months") or 3)
        months = max(1, min(24, months))
        sections = {
            "includeSummary": str(request.args.get("includeSummary") or "true").lower() == "true",
            "includeMonthly": str(request.args.get("includeMonthly") or "true").lower() == "true",
            "includeSegments": str(request.args.get("includeSegments") or "true").lower() == "true",
            "includeLeaderboard": str(request.args.get("includeLeaderboard") or "true").lower() == "true",
            "includeLicenseSummary": str(request.args.get("includeLicenseSummary") or "true").lower() == "true",
        }

        pdf_bytes = build_users_report_pdf_bytes(
            instance_name=instance_name,
            license_filter=license_filter,
            activity_filter=activity_filter,
            months=months,
            sections=sections,
        )

        filename = "pulse-users-report.pdf"
        if instance_name:
            safe_instance = re.sub(r"[^a-zA-Z0-9._-]+", "-", instance_name).strip("-") or "instance"
            filename = f"pulse-users-report-{safe_instance}.pdf"

        return send_file(
            BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename,
        )
    except Exception as e:
        logger.exception("users report export failed")
        return _err(str(e), status=500)



def register_routes(app: Flask, *, is_local_dev: bool = False) -> None:
    global _IS_LOCAL_DEV
    _IS_LOCAL_DEV = is_local_dev
    app.register_blueprint(bp)
    logger.info("Pulse backend routes registered from %s; url_map=%s", __file__, sorted(str(rule) for rule in app.url_map.iter_rules()))
    # In local Flask dev, there is no DSS body.html warmup flow, so kick off a
    # best-effort background init. In DSS/visual-webapp mode,  owns
    # the blocking startup init via ; avoid racing it with
    # a parallel background init thread that can hit the shared lock timeout.
    if _IS_LOCAL_DEV:
        if pulse_settings is not None:
            logger.info(
                "Pulse local backend startup: auto_init=%s duckdb_path=%s metadata_path=%s lock_path=%s",
                getattr(pulse_settings, "PULSE_AUTO_INIT_DUCKDB", False),
                getattr(pulse_settings, "DUCKDB_PATH", None),
                getattr(pulse_settings, "DUCKDB_METADATA_PATH", None),
                getattr(pulse_settings, "PULSE_DUCKDB_INIT_LOCK_PATH", None),
            )
        _maybe_schedule_startup_duckdb_init()
