from __future__ import annotations

import logging
from typing import Any

from flask import Blueprint, request

from pulse_dashboard.webapp_backend.services.users import _parse_days_arg
from pulse_dashboard.webapp_backend.support import (
    _df_records,
    _duckdb_relation_exists,
    _ensure_ready_if_enabled,
    _err,
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


def _activity_source_kind(query_df) -> str:
    if _duckdb_relation_exists(query_df, "final_build_development_activity_events"):
        try:
            df = query_df("SELECT COUNT(*) AS n FROM final_build_development_activity_events;")
            if len(df.index) and int(df.iloc[0]["n"] or 0) > 0:
                return "final_build"
        except Exception:
            logger.exception("Unable to inspect final_build_development_activity_events row count")
    return "fallback_join"


def _activity_source_from_sql(source_kind: str) -> str:
    if source_kind == "final_build":
        return "FROM final_build_development_activity_events\n"
    return (
        "FROM fact_dev_activity_events e\n"
        "LEFT JOIN dim_category_to_capability m\n"
        "  ON lower(trim(m.dataiku_category)) = lower(trim(e.dataiku_category))\n"
        "LEFT JOIN dim_dev_activity_event_classification c\n"
        "  ON lower(trim(c.msgtype)) = lower(trim(e.msgtype))\n"
    )


def _activity_select_prefix(source_kind: str) -> str:
    if source_kind == "final_build":
        return ""
    return (
        "SELECT\n"
        "  e.timestamp,\n"
        "  e.instance_name,\n"
        "  e.login,\n"
        "  e.project_key,\n"
        "  e.msgtype,\n"
        "  e.msgtypebase AS base_tag,\n"
        "  COALESCE(m.category_display_name, e.dataiku_category) AS dataiku_category,\n"
        "  COALESCE(m.capability_display_name, m.capability, 'Uncategorized') AS capability,\n"
        "  COALESCE(c.activity_class, 'unclassified') AS activity_class,\n"
        "  COALESCE(c.is_meaningful_activity, FALSE) AS is_meaningful_activity,\n"
        "  CASE WHEN e.login IS NOT NULL AND length(trim(e.login)) > 0 THEN TRUE ELSE FALSE END AS is_user_attributed\n"
    )


def _wrap_activity_query(source_kind: str, sql: str) -> str:
    if source_kind == "final_build":
        return sql.format(from_sql=_activity_source_from_sql(source_kind))
    return (
        "WITH dev_events AS (\n"
        + _activity_select_prefix(source_kind)
        + _activity_source_from_sql(source_kind)
        + ")\n"
        + sql.format(from_sql="FROM dev_events\n")
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
        "  COUNT(DISTINCT login) AS active_users,\n"
        "  COUNT(DISTINCT project_key) AS active_projects,\n"
        "  COUNT(DISTINCT instance_name) AS active_instances\n"
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
        "ORDER BY value DESC, label\n"
        "LIMIT 50;",
    )


def _consumption_capability_top_projects_sql(source_kind: str) -> str:
    return _wrap_activity_query(
        source_kind,
        "SELECT\n"
        "  concat_ws(' :: ', instance_name, project_key) AS label,\n"
        "  COUNT(*) AS value\n"
        "{from_sql}"
        "WHERE capability = ? AND timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY\n"
        "GROUP BY 1\n"
        "ORDER BY value DESC, label\n"
        "LIMIT 50;",
    )


def register_routes(bp: Blueprint) -> None:
    @bp.route("/api/consumption/process-usage")
    def consumption_process_usage():
        try:
            query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
            _ensure_ready_if_enabled()

            days = _parse_days_arg(default=30)
            activity_source_kind = _activity_source_kind(query_df)

            summary_df = query_df(
                _consumption_process_usage_summary_sql(activity_source_kind),
                [days],
            )
            summary = _df_records(summary_df)[0] if len(summary_df) else {}

            by_capability_df = query_df(
                _consumption_process_usage_by_capability_sql(activity_source_kind),
                [days],
            )

            activity_daily_df = query_df(
                _consumption_process_usage_daily_sql(activity_source_kind),
                [days],
            )

            top_by_users_df = query_df(
                _consumption_process_usage_top_users_sql(activity_source_kind),
                [days],
            )

            top_by_projects_df = query_df(
                _consumption_process_usage_top_projects_sql(activity_source_kind),
                [days],
            )

            top_by_instances_df = query_df(
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

        except Exception as exc:
            logger.exception("consumption process usage failed")
            return _err(str(exc), status=500)

    @bp.route("/api/consumption/process-usage/capability/<capability>")
    def consumption_process_usage_capability(capability: str):
        try:
            query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
            _ensure_ready_if_enabled()

            capability = capability.strip()
            if not capability:
                return _err("Missing capability")

            days = _parse_days_arg(default=30)
            activity_source_kind = _activity_source_kind(query_df)

            summary_df = query_df(
                _consumption_capability_summary_sql(activity_source_kind),
                [capability, days],
            )
            summary = _df_records(summary_df)[0] if len(summary_df) else {}

            activity_daily_df = query_df(
                _consumption_capability_daily_sql(activity_source_kind),
                [capability, days],
            )

            top_users_df = query_df(
                _consumption_capability_top_users_sql(activity_source_kind),
                [capability, days],
            )

            top_projects_df = query_df(
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

        except Exception as exc:
            logger.exception("consumption capability detail failed")
            return _err(str(exc), status=500)

    @bp.route("/api/build/development-activity")
    def build_development_activity():
        try:
            query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
            _ensure_ready_if_enabled()

            days = _parse_days_arg(default=30)
            activity_source_kind = _activity_source_kind(query_df)

            activity_daily_df = query_df(
                _development_activity_daily_sql(activity_source_kind),
                [days],
            )

            by_capability_df = query_df(
                _development_activity_by_capability_sql(activity_source_kind),
                [days],
            )

            by_category_df = query_df(
                _development_activity_by_category_sql(activity_source_kind),
                [days],
            )

            top_users_df = query_df(
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

        except Exception as exc:
            logger.exception("development activity failed")
            return _err(str(exc), status=500)

    @bp.route("/api/build/development-activity/capability/<capability>")
    def build_development_activity_capability(capability: str):
        try:
            query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
            _ensure_ready_if_enabled()

            capability = capability.strip()
            if not capability:
                return _err("Missing capability")

            days = _parse_days_arg(default=30)
            activity_source_kind = _activity_source_kind(query_df)

            summary_df = query_df(
                _development_capability_summary_sql(activity_source_kind),
                [capability, days],
            )
            summary = _df_records(summary_df)[0] if len(summary_df) else None

            activity_daily_df = query_df(
                _development_capability_daily_sql(activity_source_kind),
                [capability, days],
            )

            categories_df = query_df(
                _development_capability_categories_sql(activity_source_kind),
                [capability, days],
            )

            tags_df = query_df(
                _development_capability_tags_sql(activity_source_kind),
                [capability, days],
            )

            top_users_df = query_df(
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

        except Exception as exc:
            logger.exception("capability drilldown failed")
            return _err(str(exc), status=500)

    @bp.route("/api/build/development-activity/user/<login>")
    def build_development_activity_user(login: str):
        try:
            query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
            _ensure_ready_if_enabled()

            login = login.strip()
            if not login:
                return _err("Missing login")

            days = int(request.args.get("days") or 30)
            days = max(1, min(365, days))
            activity_source_kind = _activity_source_kind(query_df)

            summary_df = query_df(
                _development_user_summary_sql(activity_source_kind),
                [login, days],
            )
            summary = _df_records(summary_df)[0] if len(summary_df) else None

            activity_daily_df = query_df(
                _user_drilldown_daily_sql(activity_source_kind),
                [login, days],
            )

            capabilities_df = query_df(
                _user_drilldown_capabilities_sql(activity_source_kind),
                [login, days],
            )

            categories_df = query_df(
                _user_drilldown_categories_sql(activity_source_kind),
                [login, days],
            )

            tags_df = query_df(
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

        except Exception as exc:
            logger.exception("user drilldown failed")
            return _err(str(exc), status=500)
