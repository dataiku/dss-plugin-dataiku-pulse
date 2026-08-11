from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify

from pulse_dashboard.webapp_backend.services.users import (
    _normalize_optional_bool,
    _read_license_groups,
    _read_standard_project_variables,
    _read_user_profile_exclude_consumer,
)
from pulse_dashboard.webapp_backend.startup import (
    _maybe_schedule_startup_duckdb_init,
    _refresh_startup_status_metadata,
    _startup_check_completed,
    _startup_init_status,
    create_connection,
    is_initialization_in_progress,
    pulse_settings,
)
from pulse_dashboard.webapp_backend.support import _df_records, _ok, _require_duckdb_engine

logger = logging.getLogger(__name__)

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


def _duckdb_init_in_progress() -> bool:
    if is_initialization_in_progress is None:
        return False
    try:
        return bool(is_initialization_in_progress())
    except Exception:
        return False


def register_routes(bp: Blueprint) -> None:
    @bp.route("/api/startup/init-status")
    def startup_init_status():
        if not bool(_startup_init_status.get("startupCheckPerformed")) and not _startup_check_completed:
            _maybe_schedule_startup_duckdb_init()
        _refresh_startup_status_metadata()
        return jsonify({"ok": True, "init": dict(_startup_init_status)})

    @bp.route("/api/startup/flags")
    def startup_flags():
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
