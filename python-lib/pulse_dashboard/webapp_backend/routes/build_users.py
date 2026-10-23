from __future__ import annotations

import logging
import math
from typing import Any, cast

from flask import Blueprint, request

from pulse_dashboard.webapp_backend.services.users import (
    RequestValidationError,
    _addon_service_label,
    _license_profile_normalize_sql,
    _license_status_display_value,
    _normalize_user_directory_record,
    _parse_license_filter,
    _parse_activity_filter,
    _parse_int_arg,
    _parse_instance_name,
    _parse_login_norm,
    _parse_window_months,
    _read_standard_project_variables,
    _read_user_profile_exclude_consumer,
    _resolve_license_filter_clause,
    _format_license_filter_clause,
    _truthy_license_feature,
    _license_group_case_sql,
    _resolve_window_params,
    _sql_placeholders,
    _user_detail_instances_sql,
    _user_directory_record_rank_key,
    _users_directory_cte_sql,
    _window_months_where_sql,
)
from pulse_dashboard.webapp_backend.support import (
    _df_records,
    _duckdb_relation_exists,
    _ensure_ready_if_enabled,
    _err,
    _ok,
    _require_organization_access,
    _require_duckdb_engine,
)

logger = logging.getLogger(__name__)
if not logger.handlers:
    gunicorn_error_logger = logging.getLogger("gunicorn.error")
    if gunicorn_error_logger.handlers:
        logger.handlers = gunicorn_error_logger.handlers
        logger.setLevel(gunicorn_error_logger.level)
        logger.propagate = False

_USERS_FACETS_INSTANCES_SQL = (
    "SELECT DISTINCT instance_name\n"
    "FROM base_users_instance_metadata\n"
    "WHERE instance_name IS NOT NULL AND length(trim(instance_name)) > 0\n"
    "ORDER BY instance_name;"
)

_USER_DIRECTORY_SORT_SQL = {
    "display_name_asc": "display_name_sort ASC, login_norm ASC",
    "display_name_desc": "display_name_sort DESC, login_norm ASC",
    "login_asc": "login_norm ASC",
    "login_desc": "login_norm DESC",
    "activity_desc": "latest_activity_at DESC NULLS LAST, display_name_sort ASC, login_norm ASC",
    "activity_asc": "latest_activity_at ASC NULLS LAST, display_name_sort ASC, login_norm ASC",
}

_ADOPTION_PERIODS: dict[str, dict[str, Any]] = {
    "weekly": {
        "count": 12,
        "unit": "week",
        "step": "1 WEEK",
        "lookback": "11 WEEK",
        "label_sql": "strftime(period_start, '%Y-%m-%d') || ' – ' || strftime(period_start + INTERVAL 6 DAY, '%Y-%m-%d')",
    },
    "monthly": {
        "count": 3,
        "unit": "month",
        "step": "1 MONTH",
        "lookback": "2 MONTH",
        "label_sql": "strftime(period_start, '%b %Y')",
    },
    "quarterly": {
        "count": 4,
        "unit": "quarter",
        "step": "3 MONTH",
        "lookback": "9 MONTH",
        "label_sql": "'Q' || CAST(quarter(period_start) AS VARCHAR) || ' ' || CAST(year(period_start) AS VARCHAR)",
    },
}
_ADOPTION_REPORTING_DATE_SQL = "current_date"


def _users_facets_instances_sql() -> str:
    return _USERS_FACETS_INSTANCES_SQL


def _parse_adoption_period(value: str | None) -> str:
    period = str(value or "monthly").strip().lower()
    if period not in _ADOPTION_PERIODS:
        allowed = ", ".join(sorted(_ADOPTION_PERIODS))
        raise RequestValidationError(f"Invalid adoption period={period!r}; expected one of: {allowed}")
    return period


def _adoption_period_config(period: str) -> dict[str, Any]:
    return _ADOPTION_PERIODS[period]


def _adoption_periods_cte_sql(period: str, reporting_date_sql: str = _ADOPTION_REPORTING_DATE_SQL) -> str:
    config = _adoption_period_config(period)
    unit = config["unit"]
    step = config["step"]
    lookback = config["lookback"]
    label_sql = config["label_sql"]
    return (
        "periods AS (\n"
        "  SELECT\n"
        "    period_start::DATE AS period_start,\n"
        f"    (period_start + INTERVAL {step})::DATE AS period_end,\n"
        f"    {label_sql} AS period_label,\n"
        "    period_start <= " + reporting_date_sql + " AND " + reporting_date_sql + " < (period_start + INTERVAL " + step + ") AS is_current_period,\n"
        "    period_start <= " + reporting_date_sql + " AND " + reporting_date_sql + " < (period_start + INTERVAL " + step + ") AS is_partial_period\n"
        "  FROM generate_series(\n"
        f"    (date_trunc('{unit}', {reporting_date_sql}) - INTERVAL {lookback})::DATE,\n"
        f"    date_trunc('{unit}', {reporting_date_sql})::DATE,\n"
        f"    INTERVAL {step}\n"
        "  ) AS t(period_start)\n"
        ")"
    )


def _parse_user_directory_sort(value: str | None) -> str:
    sort = str(value or "display_name_asc").strip().lower()
    if sort not in _USER_DIRECTORY_SORT_SQL:
        allowed = ", ".join(sorted(_USER_DIRECTORY_SORT_SQL))
        raise RequestValidationError(f"Invalid sort={sort!r}; expected one of: {allowed}")
    return sort


def _parse_user_status_filter(value: str | None) -> str:
    status = str(value or "all").strip().lower()
    if status not in {"all", "enabled", "disabled"}:
        raise RequestValidationError("Invalid account_status; expected one of: all, enabled, disabled")
    return status


def _parse_directory_text_filter(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _user_directory_base_ctes_sql() -> str:
    license_group_sql = _license_group_case_sql("user_profile")
    return (
        "WITH latest_records AS (\n"
        "  SELECT\n"
        "    instance_name,\n"
        "    trim(users_login) AS login,\n"
        "    lower(trim(users_login)) AS login_norm,\n"
        "    nullif(trim(users_displayname), '') AS display_name,\n"
        "    nullif(trim(users_email), '') AS email,\n"
        "    users_enabled = 'True' AS enabled,\n"
        "    coalesce(nullif(trim(users_resultinguserprofile), ''), nullif(trim(users_userprofile), '')) AS user_profile,\n"
        "    run_ts,\n"
        "    partition_date,\n"
        "    users_lastsessionactivity,\n"
        "    ROW_NUMBER() OVER (\n"
        "      PARTITION BY instance_name, lower(trim(users_login))\n"
        "      ORDER BY run_ts DESC NULLS LAST, partition_date DESC NULLS LAST, users_lastsessionactivity DESC NULLS LAST, trim(users_login) ASC\n"
        "    ) AS rn\n"
        "  FROM base_users_instance_metadata\n"
        "  WHERE users_login IS NOT NULL AND length(trim(users_login)) > 0\n"
        "),\n"
        "latest AS (\n"
        "  SELECT\n"
        "    instance_name,\n"
        "    login,\n"
        "    login_norm,\n"
        "    display_name,\n"
        "    email,\n"
        "    enabled,\n"
        "    user_profile,\n"
        f"    {license_group_sql} AS license_group,\n"
        "    run_ts\n"
        "  FROM latest_records\n"
        "  WHERE rn = 1\n"
        "),\n"
        "activity AS (\n"
        "  SELECT\n"
        "    lower(trim(login_norm)) AS login_norm,\n"
        "    MAX(last_activity_at) AS latest_activity_at\n"
        "  FROM fact_user_activity_daily\n"
        "  WHERE login_norm IS NOT NULL AND length(trim(login_norm)) > 0\n"
        "    AND (? IS NULL OR instance_name = ?)\n"
        "  GROUP BY 1\n"
        ")\n"
    )


def _directory_filter_sql(*, include_search: bool, account_status: str, has_license_group: bool, has_profile: bool) -> str:
    clauses = ["(? IS NULL OR instance_name = ?)"]
    if include_search:
        clauses.append(
            "(lower(coalesce(display_name, '')) LIKE ? OR lower(login_norm) LIKE ? OR lower(coalesce(email, '')) LIKE ?)"
        )
    if account_status == "enabled":
        clauses.append("enabled IS TRUE")
    elif account_status == "disabled":
        clauses.append("enabled IS FALSE")
    if has_license_group:
        clauses.append("license_group = ?")
    if has_profile:
        clauses.append("coalesce(user_profile, 'UNKNOWN') = ?")
    return " AND ".join(clauses)


def _directory_filter_params(
    *,
    instance_name: str | None,
    search: str | None,
    license_group: str | None,
    license_profile: str | None,
) -> list[Any]:
    params: list[Any] = [instance_name, instance_name]
    if search:
        pattern = f"%{search.lower()}%"
        params.extend([pattern, pattern, pattern])
    if license_group:
        params.append(license_group)
    if license_profile:
        params.append(license_profile)
    return params


def register_routes(bp: Blueprint) -> None:
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

    @bp.route("/api/build/users/leaderboard")
    def build_users_leaderboard():
        """Return leaderboards for consuming and creating activity."""

        try:
            _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
            _ensure_ready_if_enabled()

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
                where = ["last_activity_at >= current_date - ?::INTEGER"]
                params = [days]

            instance_name = _parse_instance_name(request.args.get("instance_name"))
            activity_filter = _parse_activity_filter(request.args.get("activityFilter"))

            standard = _read_standard_project_variables()
            excluded_profiles = _read_user_profile_exclude_consumer(standard)

            activity_filter_sql = ""
            activity_filter_params: list[Any] = []
            if activity_filter == "license_consumer" and excluded_profiles:
                activity_filter_sql = (
                    f" AND coalesce(upper(trim(u.user_profile)), '') NOT IN ({_sql_placeholders(len(excluded_profiles))})"
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
                f"  {where_sql}\n"
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
                    + "SELECT\n"
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
                    + "SELECT\n"
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


    @bp.route("/api/build/users/kpis")
    def build_users_kpis():
        """User directory KPIs for the Users page.

        KPIs are computed from `base_users_instance_metadata` so they reflect
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
                    "  FROM base_users_instance_metadata\n"
                    "  WHERE users_login IS NOT NULL AND length(trim(users_login)) > 0\n"
                    f"    {instance_sql}\n"
                    ")\n"
                    ", activity AS (\n"
                    "  SELECT\n"
                    "    lower(trim(login_norm)) AS login_norm,\n"
                    "    SUM(viewing_actions_count) AS total_viewing,\n"
                    "    SUM(developing_actions_count) AS total_developing,\n"
                    "    SUM(CASE WHEN last_activity_at >= "
                    f"{thirty_day_start_expr}"
                    " THEN viewing_actions_count ELSE 0 END) AS viewing_30d,\n"
                    "    SUM(CASE WHEN last_activity_at >= "
                    f"{thirty_day_start_expr}"
                    " THEN developing_actions_count ELSE 0 END) AS developing_30d,\n"
                    "    SUM(CASE WHEN last_activity_at >= "
                    f"{ninety_day_start_expr}"
                    " THEN viewing_actions_count ELSE 0 END) AS viewing_90d,\n"
                    "    SUM(CASE WHEN last_activity_at >= "
                    f"{ninety_day_start_expr}"
                    " THEN developing_actions_count ELSE 0 END) AS developing_90d,\n"
                    "    SUM(CASE WHEN last_activity_at >= "
                    f"{six_month_start_expr}"
                    " THEN viewing_actions_count ELSE 0 END) AS viewing_6m,\n"
                    "    SUM(CASE WHEN last_activity_at >= "
                    f"{six_month_start_expr}"
                    " THEN developing_actions_count ELSE 0 END) AS developing_6m,\n"
                    "    SUM(CASE WHEN last_activity_at >= "
                    f"{twelve_month_start_expr}"
                    " THEN viewing_actions_count ELSE 0 END) AS viewing_12m,\n"
                    "    SUM(CASE WHEN last_activity_at >= "
                    f"{twelve_month_start_expr}"
                    " THEN developing_actions_count ELSE 0 END) AS developing_12m\n"
                    "  FROM fact_user_activity_daily\n"
                    "  GROUP BY 1\n"
                    ")\n"
                    "SELECT\n"
                    "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True') AS enabled_users,\n"
                    "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True'"
                    f"{license_filter_sql}) AS enabled_users_no_consumer,\n"
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
                    f"{instance_sql}\n"
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
                    f"{instance_sql}\n"
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
                "run_ts",
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
                "rn",
            }
            feature_counts: dict[str, int] = {}
            for status_row in license_status_rows:
                for field_name, raw_value in status_row.items():
                    if field_name in excluded_feature_columns or not field_name.startswith("feature_"):
                        continue
                    if not _truthy_license_feature(raw_value):
                        continue
                    feature_counts[field_name] = feature_counts.get(field_name, 0) + 1

            feature_rows = [
                {
                    "feature": field_name,
                    "label": field_name.replace("feature_", "").replace("_", " ").title(),
                    "count": count,
                }
                for field_name, count in sorted(
                    feature_counts.items(),
                    key=lambda item: (-int(item[1]), str(item[0])),
                )
            ]
            license_status_summary["features"] = feature_rows

            addon_services = []
            for field_name, count in feature_counts.items():
                if field_name not in {"feature_advanced_govern", "feature_advanced_llm_mesh", "feature_stories"}:
                    continue
                addon_key = field_name.replace("feature_", "")
                addon_services.append(
                    {
                        "key": addon_key,
                        "label": _addon_service_label(addon_key),
                        "count": count,
                    }
                )
            license_status_summary["addonServices"] = sorted(
                addon_services,
                key=lambda item: (-int(item.get("count") or 0), str(item.get("label") or "")),
            )

            by_profile_df = _query_df(
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
                    "  FROM base_users_instance_metadata\n"
                    "  WHERE users_login IS NOT NULL AND length(trim(users_login)) > 0\n"
                    f"    {instance_sql}\n"
                    "),\n"
                    "activity AS (\n"
                    "  SELECT\n"
                    "    instance_name,\n"
                    "    lower(trim(login_norm)) AS login_norm,\n"
                    "    SUM(viewing_actions_count) AS total_viewing,\n"
                    "    SUM(developing_actions_count) AS total_developing,\n"
                    "    SUM(CASE WHEN last_activity_at >= "
                    f"{thirty_day_start_expr}"
                    " THEN viewing_actions_count ELSE 0 END) AS viewing_30d,\n"
                    "    SUM(CASE WHEN last_activity_at >= "
                    f"{thirty_day_start_expr}"
                    " THEN developing_actions_count ELSE 0 END) AS developing_30d,\n"
                    "    SUM(CASE WHEN last_activity_at >= "
                    f"{ninety_day_start_expr}"
                    " THEN viewing_actions_count ELSE 0 END) AS viewing_90d,\n"
                    "    SUM(CASE WHEN last_activity_at >= "
                    f"{ninety_day_start_expr}"
                    " THEN developing_actions_count ELSE 0 END) AS developing_90d,\n"
                    "    SUM(CASE WHEN last_activity_at >= "
                    f"{six_month_start_expr}"
                    " THEN viewing_actions_count ELSE 0 END) AS viewing_6m,\n"
                    "    SUM(CASE WHEN last_activity_at >= "
                    f"{six_month_start_expr}"
                    " THEN developing_actions_count ELSE 0 END) AS developing_6m,\n"
                    "    MAX(last_activity_at) AS last_activity_at\n"
                    "  FROM fact_user_activity_daily\n"
                    "  WHERE 1 = 1\n"
                    f"    {instance_sql}\n"
                    "  GROUP BY 1, 2\n"
                    ")\n"
                    "SELECT\n"
                    "  coalesce(trim(l.users_userprofile), 'UNKNOWN') AS profile,\n"
                    "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True') AS enabled_users,\n"
                    "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND (coalesce(a.viewing_30d, 0) > 0 OR coalesce(a.developing_30d, 0) > 0)) AS active_users_30d,\n"
                    "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.developing_30d, 0) > 0) AS developing_users_30d,\n"
                    "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND (coalesce(a.viewing_90d, 0) > 0 OR coalesce(a.developing_90d, 0) > 0)) AS active_users_90d,\n"
                    "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.developing_90d, 0) > 0) AS developing_users_90d,\n"
                    "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND (coalesce(a.viewing_6m, 0) > 0 OR coalesce(a.developing_6m, 0) > 0)) AS active_users_6m,\n"
                    "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.developing_6m, 0) > 0) AS developing_users_6m\n"
                    "FROM latest l\n"
                    "LEFT JOIN activity a ON a.instance_name = l.instance_name AND a.login_norm = l.login_norm\n"
                    "WHERE l.rn = 1"
                    f"{license_filter_sql_by_instance}\n"
                    "GROUP BY 1\n"
                    "ORDER BY enabled_users DESC, profile;"
                ),
                [*instance_params, *instance_params, *license_filter_params_list],
            )

            by_license_group_df = _query_df(
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
                    "  FROM base_users_instance_metadata\n"
                    "  WHERE users_login IS NOT NULL AND length(trim(users_login)) > 0\n"
                    f"    {instance_sql}\n"
                    "),\n"
                    "activity AS (\n"
                    "  SELECT\n"
                    "    instance_name,\n"
                    "    lower(trim(login_norm)) AS login_norm,\n"
                    "    SUM(viewing_actions_count) AS total_viewing,\n"
                    "    SUM(developing_actions_count) AS total_developing,\n"
                    "    SUM(CASE WHEN last_activity_at >= "
                    f"{thirty_day_start_expr}"
                    " THEN viewing_actions_count ELSE 0 END) AS viewing_30d,\n"
                    "    SUM(CASE WHEN last_activity_at >= "
                    f"{thirty_day_start_expr}"
                    " THEN developing_actions_count ELSE 0 END) AS developing_30d,\n"
                    "    SUM(CASE WHEN last_activity_at >= "
                    f"{ninety_day_start_expr}"
                    " THEN viewing_actions_count ELSE 0 END) AS viewing_90d,\n"
                    "    SUM(CASE WHEN last_activity_at >= "
                    f"{ninety_day_start_expr}"
                    " THEN developing_actions_count ELSE 0 END) AS developing_90d,\n"
                    "    SUM(CASE WHEN last_activity_at >= "
                    f"{six_month_start_expr}"
                    " THEN viewing_actions_count ELSE 0 END) AS viewing_6m,\n"
                    "    SUM(CASE WHEN last_activity_at >= "
                    f"{six_month_start_expr}"
                    " THEN developing_actions_count ELSE 0 END) AS developing_6m\n"
                    "  FROM fact_user_activity_daily\n"
                    "  WHERE 1 = 1\n"
                    f"    {instance_sql}\n"
                    "  GROUP BY 1, 2\n"
                    ")\n"
                    "SELECT\n"
                    f"  {license_group_case_sql_for_latest} AS license_group,\n"
                    "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True') AS enabled_users,\n"
                    "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND (coalesce(a.viewing_30d, 0) > 0 OR coalesce(a.developing_30d, 0) > 0)) AS active_users_30d,\n"
                    "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.developing_30d, 0) > 0) AS developing_users_30d,\n"
                    "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND (coalesce(a.viewing_90d, 0) > 0 OR coalesce(a.developing_90d, 0) > 0)) AS active_users_90d,\n"
                    "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.developing_90d, 0) > 0) AS developing_users_90d,\n"
                    "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND (coalesce(a.viewing_6m, 0) > 0 OR coalesce(a.developing_6m, 0) > 0)) AS active_users_6m,\n"
                    "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.developing_6m, 0) > 0) AS developing_users_6m\n"
                    "FROM latest l\n"
                    "LEFT JOIN activity a ON a.instance_name = l.instance_name AND a.login_norm = l.login_norm\n"
                    "WHERE l.rn = 1"
                    f"{license_filter_sql_by_instance}\n"
                    "GROUP BY 1\n"
                    "ORDER BY enabled_users DESC, license_group;"
                ),
                [*instance_params, *instance_params, *license_filter_params_list],
            )

            profile_normalized_expr = _license_profile_normalize_sql("user_profile")
            max_license_profile_df = _query_df(
                (
                    "WITH instance_scope AS (\n"
                    "  SELECT DISTINCT instance_name\n"
                    "  FROM base_users_instance_metadata\n"
                    "  WHERE users_login IS NOT NULL AND length(trim(users_login)) > 0\n"
                    f"    {instance_sql}\n"
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

            by_instance_df = _query_df(
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
                    "  FROM base_users_instance_metadata\n"
                    "  WHERE users_login IS NOT NULL AND length(trim(users_login)) > 0\n"
                    f"    {instance_sql}\n"
                    "),\n"
                    "activity AS (\n"
                    "  SELECT\n"
                    "    instance_name,\n"
                    "    lower(trim(login_norm)) AS login_norm,\n"
                    "    SUM(viewing_actions_count) AS total_viewing,\n"
                    "    SUM(developing_actions_count) AS total_developing,\n"
                    "    SUM(CASE WHEN last_activity_at >= "
                    f"{six_month_start_expr}"
                    " THEN viewing_actions_count ELSE 0 END) AS viewing_6m,\n"
                    "    SUM(CASE WHEN last_activity_at >= "
                    f"{six_month_start_expr}"
                    " THEN developing_actions_count ELSE 0 END) AS developing_6m\n"
                    "  FROM fact_user_activity_daily\n"
                    "  WHERE 1 = 1\n"
                    f"    {instance_sql}\n"
                    "  GROUP BY 1, 2\n"
                    ")\n"
                    "SELECT\n"
                    "  l.instance_name AS instanceName,\n"
                    "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True') AS enabled_users,\n"
                    "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.viewing_6m, 0) > 0) AS viewing_users,\n"
                    "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.developing_6m, 0) > 0) AS developing_users,\n"
                    "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND (coalesce(a.viewing_6m, 0) > 0 OR coalesce(a.developing_6m, 0) > 0)) AS active_users_6m,\n"
                    "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.viewing_6m, 0) > 0 AND coalesce(a.developing_6m, 0) = 0) AS viewer_only_users_6m\n"
                    "FROM latest l\n"
                    "LEFT JOIN activity a ON a.instance_name = l.instance_name AND a.login_norm = l.login_norm\n"
                    "WHERE l.rn = 1"
                    f"{license_filter_sql_by_instance}\n"
                    "GROUP BY 1\n"
                    "ORDER BY enabled_users DESC, instanceName;"
                ),
                [*instance_params, *instance_params, *license_filter_params_list],
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
                    "  FROM base_users_instance_metadata\n"
                    "  WHERE users_login IS NOT NULL AND length(trim(users_login)) > 0\n"
                    f"    {instance_sql}\n"
                    ")\n"
                    "SELECT\n"
                    f"  {_license_group_case_sql('user_profile')} AS license_group,\n"
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

    @bp.route("/api/build/users/license-utilization")
    def build_users_license_utilization():
        """Fact-backed license utilization reporting for the License Performance page."""

        try:
            _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
            _ensure_ready_if_enabled()

            instance_name = _parse_instance_name(request.args.get("instance_name"))
            license_filter = _parse_license_filter(request.args.get("licenseFilter"))
            license_profile = (request.args.get("license_profile") or "").strip() or None
            history_days = int(_parse_int_arg("days", default=180, minimum=1, maximum=3660) or 180)

            if not _duckdb_relation_exists(_query_df, "fact_license_utilization_daily"):
                return _ok(
                    {
                        "available": False,
                        "unavailableReason": "fact_license_utilization_daily is unavailable. Rebuild GOLD tables with the license utilization fact before using License Performance capacity metrics.",
                        "instanceName": instance_name,
                        "licenseFilter": license_filter,
                        "licenseProfile": license_profile,
                        "latestRows": [],
                        "historyRows": [],
                        "meta": {
                            "source": "fact_license_utilization_daily",
                            "grain": ["snapshot_date", "instance_name", "license_profile"],
                            "historyDays": history_days,
                            "preservesSeparateInstances": not bool(instance_name),
                            "scopeLabel": instance_name or "separate DSS instances",
                            "latestSnapshotDates": [],
                            "historyStartDate": None,
                            "historyEndDate": None,
                            "rowCount": 0,
                            "seriesCount": 0,
                        },
                    }
                )

            instance_sql = ""
            params: list[Any] = []
            if instance_name:
                instance_sql = " AND instance_name = ?"
                params.append(instance_name)

            profile_sql = ""
            if license_profile:
                profile_sql = " AND license_profile = ?"
                params.append(license_profile)

            license_filter_sql_template, license_filter_params = _resolve_license_filter_clause(license_filter)
            license_filter_sql = _format_license_filter_clause(license_filter_sql_template, profile_expr="license_profile")
            params.extend(license_filter_params)

            base_where_sql = f"WHERE 1=1{instance_sql}{profile_sql}{license_filter_sql}"
            license_group_case_sql = _license_group_case_sql("license_profile")
            fact_license_group_case_sql = _license_group_case_sql("f.license_profile")
            fact_where_parts = ["b.max_snapshot_date IS NOT NULL"]
            fact_where_parts.append("f.snapshot_date >= b.max_snapshot_date - (?::INTEGER) * INTERVAL 1 DAY")
            history_filter_params: list[Any] = [history_days]
            fact_history_where_sql = "\n  AND ".join(fact_where_parts)

            latest_df = _query_df(
                (
                    "WITH ranked AS (\n"
                    "  SELECT\n"
                    "    snapshot_date,\n"
                    "    instance_name,\n"
                    "    license_profile,\n"
                    f"    {license_group_case_sql} AS license_group,\n"
                    "    entitled_count,\n"
                    "    assigned_count,\n"
                    "    available_count,\n"
                    "    utilization_pct,\n"
                    "    run_ts,\n"
                    "    ROW_NUMBER() OVER (\n"
                    "      PARTITION BY instance_name, license_profile\n"
                    "      ORDER BY snapshot_date DESC, run_ts DESC\n"
                    "    ) AS rn\n"
                    "  FROM fact_license_utilization_daily\n"
                    f"  {base_where_sql}\n"
                    ")\n"
                    "SELECT\n"
                    "  snapshot_date,\n"
                    "  instance_name,\n"
                    "  license_group,\n"
                    "  license_profile,\n"
                    "  entitled_count,\n"
                    "  assigned_count,\n"
                    "  available_count,\n"
                    "  utilization_pct,\n"
                    "  run_ts\n"
                    "FROM ranked\n"
                    "WHERE rn = 1\n"
                    "ORDER BY instance_name, license_group, license_profile;"
                ),
                params,
            )

            history_df = _query_df(
                (
                    "WITH bounds AS (\n"
                    "  SELECT\n"
                    "    instance_name,\n"
                    "    license_profile,\n"
                    "    max(snapshot_date) AS max_snapshot_date\n"
                    "  FROM fact_license_utilization_daily\n"
                    f"  {base_where_sql}\n"
                    "  GROUP BY instance_name, license_profile\n"
                    ")\n"
                    "SELECT\n"
                    "  f.snapshot_date,\n"
                    "  f.instance_name,\n"
                    f"  {fact_license_group_case_sql} AS license_group,\n"
                    "  f.license_profile,\n"
                    "  f.entitled_count,\n"
                    "  f.assigned_count,\n"
                    "  f.available_count,\n"
                    "  f.utilization_pct,\n"
                    "  f.run_ts\n"
                    "FROM fact_license_utilization_daily f\n"
                    "INNER JOIN bounds b\n"
                    "  ON f.instance_name IS NOT DISTINCT FROM b.instance_name\n"
                    " AND f.license_profile IS NOT DISTINCT FROM b.license_profile\n"
                    f"WHERE {fact_history_where_sql}\n"
                    "ORDER BY f.instance_name, f.license_profile, f.snapshot_date;"
                ),
                [*params, *history_filter_params],
            )

            coverage_df = _query_df(
                (
                    "WITH bounds AS (\n"
                    "  SELECT\n"
                    "    instance_name,\n"
                    "    license_profile,\n"
                    "    max(snapshot_date) AS max_snapshot_date\n"
                    "  FROM fact_license_utilization_daily\n"
                    f"  {base_where_sql}\n"
                    "  GROUP BY instance_name, license_profile\n"
                    ")\n"
                    "SELECT\n"
                    "  min(f.snapshot_date) AS history_start_date,\n"
                    "  max(f.snapshot_date) AS history_end_date,\n"
                    "  count(*) AS row_count,\n"
                    "  count(DISTINCT f.instance_name || '|' || coalesce(f.license_profile, 'UNKNOWN')) AS series_count\n"
                    "FROM fact_license_utilization_daily f\n"
                    "INNER JOIN bounds b\n"
                    "  ON f.instance_name IS NOT DISTINCT FROM b.instance_name\n"
                    " AND f.license_profile IS NOT DISTINCT FROM b.license_profile\n"
                    f"WHERE {fact_history_where_sql};"
                ),
                [*params, *history_filter_params],
            )
            coverage = _df_records(coverage_df)[0] if not coverage_df.empty else {}
            latest_rows = _df_records(latest_df)
            latest_snapshot_dates = sorted(
                {
                    str(row.get("snapshot_date") or "")[:10]
                    for row in latest_rows
                    if row.get("snapshot_date")
                }
            )

            return _ok(
                {
                    "available": True,
                    "instanceName": instance_name,
                    "licenseFilter": license_filter,
                    "licenseProfile": license_profile,
                    "latestRows": latest_rows,
                    "historyRows": _df_records(history_df),
                    "meta": {
                        "source": "fact_license_utilization_daily",
                        "grain": ["snapshot_date", "instance_name", "license_profile"],
                        "historyDays": history_days,
                        "preservesSeparateInstances": not bool(instance_name),
                        "scopeLabel": instance_name or "separate DSS instances",
                        "latestSnapshotDates": latest_snapshot_dates,
                        "historyStartDate": coverage.get("history_start_date"),
                        "historyEndDate": coverage.get("history_end_date"),
                        "rowCount": int(coverage.get("row_count") or 0),
                        "seriesCount": int(coverage.get("series_count") or 0),
                    },
                }
            )

        except RequestValidationError as exc:
            return _err(str(exc), status=400)
        except Exception as e:
            logger.exception("users license utilization failed")
            return _err(str(e), status=500)

    @bp.route("/api/build/users/current-license-utilization")
    def build_users_current_license_utilization():
        """Current base-table license utilization reporting for the License Performance page."""

        try:
            _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
            _ensure_ready_if_enabled()

            instance_name = _parse_instance_name(request.args.get("instance_name"))
            license_filter = _parse_license_filter(request.args.get("licenseFilter"))

            instance_sql = ""
            instance_params: list[Any] = []
            if instance_name:
                instance_sql = " AND instance_name = ?"
                instance_params = [instance_name]

            license_filter_sql_template, license_filter_params = _resolve_license_filter_clause(license_filter)
            user_license_filter_sql = _format_license_filter_clause(
                license_filter_sql_template,
                profile_expr="user_profile",
            )
            max_license_filter_sql = _format_license_filter_clause(
                license_filter_sql_template,
                profile_expr="profile",
            )
            profile_normalized_expr = _license_profile_normalize_sql("user_profile")
            license_group_case_sql = _license_group_case_sql("user_profile")
            max_license_group_case_sql = _license_group_case_sql("m.profile")

            common_ctes = (
                "WITH instance_scope AS (\n"
                "  SELECT DISTINCT instance_name\n"
                "  FROM base_users_instance_metadata\n"
                "  WHERE users_login IS NOT NULL AND length(trim(users_login)) > 0\n"
                f"    {instance_sql}\n"
                "),\n"
                "latest_users AS (\n"
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
                "  FROM base_users_instance_metadata\n"
                "  WHERE users_login IS NOT NULL AND length(trim(users_login)) > 0\n"
                f"    {instance_sql}\n"
                "),\n"
                "current_users AS (\n"
                "  SELECT\n"
                "    instance_name,\n"
                "    login_norm,\n"
                "    user_profile,\n"
                f"    {profile_normalized_expr} AS profile_norm,\n"
                "    run_ts\n"
                "  FROM latest_users\n"
                "  WHERE rn = 1 AND users_enabled = 'True'\n"
                f"    {user_license_filter_sql}\n"
                "),\n"
                "normalized_max AS (\n"
                "  SELECT\n"
                "    m.instance_name,\n"
                "    coalesce(nullif(trim(m.license_profile), ''), 'UNKNOWN') AS profile,\n"
                f"    {_license_profile_normalize_sql('m.license_profile')} AS profile_norm,\n"
                "    try_cast(m.max_licenses AS BIGINT) AS max_licenses,\n"
                "    m.run_ts\n"
                "  FROM base_license_max_licenses_latest m\n"
                "  INNER JOIN instance_scope s ON s.instance_name = m.instance_name\n"
                "  WHERE try_cast(m.max_licenses AS BIGINT) IS NOT NULL\n"
                "),\n"
                "ranked_max AS (\n"
                "  SELECT\n"
                "    instance_name,\n"
                "    profile,\n"
                "    profile_norm,\n"
                "    max_licenses,\n"
                "    run_ts,\n"
                "    ROW_NUMBER() OVER (\n"
                "      PARTITION BY instance_name, profile_norm\n"
                "      ORDER BY max_licenses DESC NULLS LAST, profile ASC, run_ts DESC\n"
                "    ) AS rn\n"
                "  FROM normalized_max\n"
                "),\n"
                "current_max AS (\n"
                "  SELECT instance_name, profile, profile_norm, max_licenses, run_ts\n"
                "  FROM ranked_max\n"
                "  WHERE rn = 1\n"
                f"    {max_license_filter_sql}\n"
                ")\n"
            )

            instance_rows_df = _query_df(
                (
                    common_ctes +
                    ", user_counts AS (\n"
                    "  SELECT\n"
                    "    instance_name,\n"
                    "    user_profile AS profile,\n"
                    "    profile_norm,\n"
                    f"    {license_group_case_sql} AS license_group,\n"
                    "    COUNT(DISTINCT login_norm) AS assigned_count,\n"
                    "    max(run_ts) AS user_snapshot_ts\n"
                    "  FROM current_users\n"
                    "  GROUP BY 1, 2, 3, 4\n"
                    "),\n"
                    "row_keys AS (\n"
                    "  SELECT\n"
                    "    coalesce(u.instance_name, m.instance_name) AS instance_name,\n"
                    "    coalesce(u.profile, m.profile) AS profile,\n"
                    "    coalesce(u.profile_norm, m.profile_norm) AS profile_norm,\n"
                    "    coalesce(u.license_group, "
                    f"{max_license_group_case_sql}) AS license_group\n"
                    "  FROM user_counts u\n"
                    "  FULL OUTER JOIN current_max m\n"
                    "    ON m.instance_name IS NOT DISTINCT FROM u.instance_name\n"
                    "   AND m.profile_norm IS NOT DISTINCT FROM u.profile_norm\n"
                    ")\n"
                    "SELECT\n"
                    "  k.instance_name,\n"
                    "  k.license_group,\n"
                    "  k.profile AS license_profile,\n"
                    "  k.profile_norm,\n"
                    "  coalesce(u.assigned_count, 0) AS assigned_count,\n"
                    "  m.max_licenses AS entitled_count,\n"
                    "  CASE WHEN m.max_licenses IS NULL THEN NULL ELSE m.max_licenses - coalesce(u.assigned_count, 0) END AS available_count,\n"
                    "  CASE WHEN m.max_licenses > 0 THEN (coalesce(u.assigned_count, 0)::DOUBLE / m.max_licenses::DOUBLE) * 100 ELSE NULL END AS utilization_pct,\n"
                    "  u.user_snapshot_ts,\n"
                    "  m.run_ts AS license_snapshot_ts\n"
                    "FROM row_keys k\n"
                    "LEFT JOIN user_counts u\n"
                    "  ON u.instance_name IS NOT DISTINCT FROM k.instance_name\n"
                    " AND u.profile_norm IS NOT DISTINCT FROM k.profile_norm\n"
                    "LEFT JOIN current_max m\n"
                    "  ON m.instance_name IS NOT DISTINCT FROM k.instance_name\n"
                    " AND m.profile_norm IS NOT DISTINCT FROM k.profile_norm\n"
                    "ORDER BY k.instance_name, k.license_group, k.profile;"
                ),
                [*instance_params, *instance_params, *license_filter_params, *license_filter_params],
            )

            profile_rows_df = _query_df(
                (
                    common_ctes +
                    ", profile_user_counts AS (\n"
                    "  SELECT\n"
                    "    user_profile AS profile,\n"
                    "    profile_norm,\n"
                    f"    {license_group_case_sql} AS license_group,\n"
                    "    COUNT(DISTINCT login_norm) AS assigned_count,\n"
                    "    COUNT(DISTINCT instance_name) AS contributing_instance_count,\n"
                    "    max(run_ts) AS user_snapshot_ts\n"
                    "  FROM current_users\n"
                    "  GROUP BY 1, 2, 3\n"
                    "),\n"
                    "profile_max AS (\n"
                    "  SELECT\n"
                    "    profile_norm,\n"
                    "    profile,\n"
                    "    max_licenses,\n"
                    "    COUNT(*) AS max_licenses_instance_count,\n"
                    "    max(run_ts) AS license_snapshot_ts,\n"
                    "    ROW_NUMBER() OVER (\n"
                    "      PARTITION BY profile_norm\n"
                    "      ORDER BY COUNT(*) DESC, max_licenses DESC, profile ASC\n"
                    "    ) AS rn\n"
                    "  FROM current_max\n"
                    "  GROUP BY 1, 2, 3\n"
                    "),\n"
                    "representative_max AS (\n"
                    "  SELECT profile_norm, profile, max_licenses, max_licenses_instance_count, license_snapshot_ts\n"
                    "  FROM profile_max\n"
                    "  WHERE rn = 1\n"
                    "),\n"
                    "row_keys AS (\n"
                    "  SELECT\n"
                    "    coalesce(u.profile, m.profile) AS profile,\n"
                    "    coalesce(u.profile_norm, m.profile_norm) AS profile_norm,\n"
                    "    coalesce(u.license_group, "
                    f"{max_license_group_case_sql}) AS license_group\n"
                    "  FROM profile_user_counts u\n"
                    "  FULL OUTER JOIN representative_max m\n"
                    "    ON m.profile_norm IS NOT DISTINCT FROM u.profile_norm\n"
                    ")\n"
                    "SELECT\n"
                    "  k.license_group,\n"
                    "  k.profile AS license_profile,\n"
                    "  k.profile_norm,\n"
                    "  coalesce(u.assigned_count, 0) AS assigned_count,\n"
                    "  coalesce(u.contributing_instance_count, 0) AS contributing_instance_count,\n"
                    "  m.max_licenses AS entitled_count,\n"
                    "  CASE WHEN m.max_licenses IS NULL THEN NULL ELSE m.max_licenses - coalesce(u.assigned_count, 0) END AS available_count,\n"
                    "  CASE WHEN m.max_licenses > 0 THEN (coalesce(u.assigned_count, 0)::DOUBLE / m.max_licenses::DOUBLE) * 100 ELSE NULL END AS utilization_pct,\n"
                    "  m.profile AS entitlement_profile,\n"
                    "  m.max_licenses_instance_count,\n"
                    "  u.user_snapshot_ts,\n"
                    "  m.license_snapshot_ts\n"
                    "FROM row_keys k\n"
                    "LEFT JOIN profile_user_counts u\n"
                    "  ON u.profile_norm IS NOT DISTINCT FROM k.profile_norm\n"
                    "LEFT JOIN representative_max m\n"
                    "  ON m.profile_norm IS NOT DISTINCT FROM k.profile_norm\n"
                    "ORDER BY assigned_count DESC, k.license_group, k.profile;"
                ),
                [*instance_params, *instance_params, *license_filter_params, *license_filter_params],
            )

            instance_rows = _df_records(instance_rows_df)
            instances_by_profile_norm: dict[str, list[dict[str, Any]]] = {}
            for row in instance_rows:
                profile_norm = str(row.get("profile_norm") or "")
                instances_by_profile_norm.setdefault(profile_norm, []).append(
                    {
                        "instance_name": row.get("instance_name"),
                        "assigned_count": row.get("assigned_count"),
                        "entitled_count": row.get("entitled_count"),
                        "available_count": row.get("available_count"),
                        "utilization_pct": row.get("utilization_pct"),
                    }
                )

            profile_rows = _df_records(profile_rows_df)
            for row in profile_rows:
                profile_norm = str(row.get("profile_norm") or "")
                row["instances"] = instances_by_profile_norm.get(profile_norm, [])

            return _ok(
                {
                    "instanceName": instance_name,
                    "licenseFilter": license_filter,
                    "profileRows": profile_rows,
                    "instanceRows": instance_rows,
                    "meta": {
                        "source": "base_users_instance_metadata + base_license_max_licenses_latest",
                        "profileField": "users_userprofile",
                        "assignmentCount": "enabled distinct normalized logins within the selected scope",
                        "capacityAggregation": "representative max_licenses by normalized profile; capacity is not summed across instances",
                        "profileRowCount": len(profile_rows),
                        "instanceRowCount": len(instance_rows),
                    },
                }
            )

        except RequestValidationError as exc:
            return _err(str(exc), status=400)
        except Exception as e:
            logger.exception("users current license utilization failed")
            return _err(str(e), status=500)

    @bp.route("/api/build/users/directory/facets")
    def build_users_directory_facets():
        try:
            _require_organization_access()
            _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
            _ensure_ready_if_enabled()

            instance_name = _parse_instance_name(request.args.get("instance_name"))
            facets_df = _query_df(
                (
                    _user_directory_base_ctes_sql()
                    + "SELECT\n"
                    + "  'licenseGroup' AS facet_type,\n"
                    + "  license_group AS value,\n"
                    + "  license_group AS label\n"
                    + "FROM latest\n"
                    + "WHERE (? IS NULL OR instance_name = ?)\n"
                    + "GROUP BY 1, 2, 3\n"
                    + "UNION ALL\n"
                    + "SELECT\n"
                    + "  'licenseProfile' AS facet_type,\n"
                    + "  coalesce(user_profile, 'UNKNOWN') AS value,\n"
                    + "  coalesce(user_profile, 'UNKNOWN') AS label\n"
                    + "FROM latest\n"
                    + "WHERE (? IS NULL OR instance_name = ?)\n"
                    + "GROUP BY 1, 2, 3\n"
                    + "ORDER BY facet_type, label;"
                ),
                [instance_name, instance_name, instance_name, instance_name, instance_name, instance_name],
            )
            facet_rows = _df_records(facets_df)
            return _ok(
                {
                    "instanceName": instance_name,
                    "licenseGroups": [row for row in facet_rows if row.get("facet_type") == "licenseGroup"],
                    "licenseProfiles": [row for row in facet_rows if row.get("facet_type") == "licenseProfile"],
                }
            )
        except PermissionError as exc:
            return _err(str(exc), status=403)
        except Exception as e:
            logger.exception("users directory facets failed")
            return _err(str(e), status=500)

    @bp.route("/api/build/users/directory")
    def build_users_directory():
        try:
            _require_organization_access()
            _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
            _ensure_ready_if_enabled()

            instance_name = _parse_instance_name(request.args.get("instance_name"))
            search = _parse_directory_text_filter(request.args.get("q"))
            account_status = _parse_user_status_filter(request.args.get("account_status"))
            license_group = _parse_directory_text_filter(request.args.get("license_group"))
            license_profile = _parse_directory_text_filter(request.args.get("license_profile"))
            sort = _parse_user_directory_sort(request.args.get("sort"))
            page_size = int(_parse_int_arg("limit", default=25, minimum=1, maximum=100) or 25)
            offset = int(_parse_int_arg("offset", default=0, minimum=0, maximum=100_000) or 0)

            filter_sql = _directory_filter_sql(
                include_search=bool(search),
                account_status=account_status,
                has_license_group=bool(license_group),
                has_profile=bool(license_profile),
            )
            filter_params = _directory_filter_params(
                instance_name=instance_name,
                search=search,
                license_group=license_group,
                license_profile=license_profile,
            )
            common_sql = (
                _user_directory_base_ctes_sql()
                + ",\nmatching_logins AS (\n"
                + "  SELECT DISTINCT login_norm\n"
                + "  FROM latest\n"
                + f"  WHERE {filter_sql}\n"
                + "),\n"
                + "display_records AS (\n"
                + "  SELECT latest.*\n"
                + "  FROM latest\n"
                + "  JOIN matching_logins USING (login_norm)\n"
                + "  WHERE (? IS NULL OR instance_name = ?)\n"
                + "),\n"
                + "grouped AS (\n"
                + "  SELECT\n"
                + "    d.login_norm,\n"
                + "    coalesce(max(d.display_name), max(d.login), d.login_norm) AS display_name,\n"
                + "    max(d.login) AS login,\n"
                + "    max(d.email) AS email,\n"
                + "    count(DISTINCT d.instance_name) AS instance_count,\n"
                + "    string_agg(DISTINCT d.instance_name, ', ' ORDER BY d.instance_name) AS instances,\n"
                + "    string_agg(DISTINCT coalesce(d.user_profile, 'UNKNOWN'), ', ' ORDER BY coalesce(d.user_profile, 'UNKNOWN')) AS license_profiles,\n"
                + "    string_agg(DISTINCT d.license_group, ', ' ORDER BY d.license_group) AS license_groups,\n"
                + "    sum(CASE WHEN d.enabled IS TRUE THEN 1 ELSE 0 END) AS enabled_count,\n"
                + "    sum(CASE WHEN d.enabled IS FALSE THEN 1 ELSE 0 END) AS disabled_count,\n"
                + "    sum(CASE WHEN d.enabled IS NULL THEN 1 ELSE 0 END) AS unknown_status_count,\n"
                + "    max(a.latest_activity_at) AS latest_activity_at\n"
                + "  FROM display_records d\n"
                + "  LEFT JOIN activity a ON a.login_norm = d.login_norm\n"
                + "  GROUP BY d.login_norm\n"
                + ")\n"
            )
            params = [instance_name, instance_name, *filter_params, instance_name, instance_name]
            total_df = _query_df(common_sql + "SELECT COUNT(*) AS total_rows FROM grouped;", params)
            rows_df = _query_df(
                (
                    common_sql
                    + "SELECT\n"
                    + "  login_norm AS login,\n"
                    + "  login AS representativeLogin,\n"
                    + "  display_name AS displayName,\n"
                    + "  email,\n"
                    + "  instance_count AS instanceCount,\n"
                    + "  instances,\n"
                    + "  license_profiles AS licenseProfiles,\n"
                    + "  license_groups AS licenseGroups,\n"
                    + "  CASE\n"
                    + "    WHEN enabled_count > 0 AND disabled_count = 0 AND unknown_status_count = 0 THEN 'Enabled'\n"
                    + "    WHEN disabled_count > 0 AND enabled_count = 0 AND unknown_status_count = 0 THEN 'Disabled'\n"
                    + "    WHEN enabled_count = 0 AND disabled_count = 0 AND unknown_status_count > 0 THEN 'Unknown'\n"
                    + "    ELSE 'Mixed'\n"
                    + "  END AS accountStatus,\n"
                    + "  CAST(latest_activity_at AS VARCHAR) AS latestActivityAt,\n"
                    + "  lower(coalesce(display_name, login_norm)) AS display_name_sort\n"
                    + "FROM grouped\n"
                    + f"ORDER BY {_USER_DIRECTORY_SORT_SQL[sort]}\n"
                    + "LIMIT ? OFFSET ?;"
                ),
                [*params, page_size, offset],
            )
            total = int((_df_records(total_df)[0] if len(total_df) else {}).get("total_rows") or 0)
            return _ok(
                {
                    "rows": _df_records(rows_df),
                    "total": total,
                    "limit": page_size,
                    "offset": offset,
                    "sort": sort,
                    "instanceName": instance_name,
                    "filters": {
                        "q": search or "",
                        "accountStatus": account_status,
                        "licenseGroup": license_group or "",
                        "licenseProfile": license_profile or "",
                    },
                }
            )
        except RequestValidationError as exc:
            return _err(str(exc), status=400)
        except PermissionError as exc:
            return _err(str(exc), status=403)
        except Exception as e:
            logger.exception("users directory failed")
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
                where = ["last_activity_at >= current_date - ?::INTEGER", "login_norm = ?"]
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
                    f"{where_sql};"
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

            daily_df = _query_df(
                (
                    "SELECT\n"
                    "  CAST(day AS VARCHAR) AS label,\n"
                    "  SUM(viewing_actions_count) AS viewing,\n"
                    "  SUM(developing_actions_count) AS developing\n"
                    "FROM fact_user_activity_daily\n"
                    f"{where_sql}\n"
                    "GROUP BY 1\n"
                    "ORDER BY 1;"
                ),
                params,
            )

            monthly_df = _query_df(
                (
                    "SELECT\n"
                    "  CAST(date_trunc('month', last_activity_at) AS VARCHAR) AS month,\n"
                    "  SUM(viewing_actions_count) AS viewing,\n"
                    "  SUM(developing_actions_count) AS developing\n"
                    "FROM fact_user_activity_daily\n"
                    f"{where_sql}\n"
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
                where = ["last_activity_at >= current_date - ?::INTEGER", "login_norm = ?"]
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
                    f"{where_sql}\n"
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

    @bp.route("/api/build/users/active-monthly")
    def build_users_active_monthly():
        """Monthly active users (calendar months) with license filter.

        Definition of "active": any UI activity (viewing or developing actions)
        recorded in `fact_user_activity_daily`.

        Activity filter is applied using the per-instance snapshot table
        `base_users_instance_metadata`, scoped to configured Creator vs
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

            start_month_expr = f"(date_trunc('month', current_date) - INTERVAL {months - 1} MONTH)::DATE"
            end_month_expr = "date_trunc('month', current_date)::DATE"
            next_month_expr = "(date_trunc('month', current_date) + INTERVAL 1 MONTH)::DATE"

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
                "  FROM base_users_instance_metadata\n"
                "  WHERE users_login IS NOT NULL\n"
                "    AND length(trim(users_login)) > 0\n"
                "),\n"
            )

            by_instance_df = _query_df(
                (
                    "WITH months AS (\n"
                    f"  SELECT * FROM generate_series({start_month_expr}, ({next_month_expr} - INTERVAL 1 DAY)::DATE, INTERVAL 1 MONTH) AS t(month_start)\n"
                    "),\n"
                    f"{latest_users_cte}"
                    "activity AS (\n"
                    "  SELECT\n"
                    "    date_trunc('month', a.last_activity_at) AS month_start,\n"
                    "    a.instance_name,\n"
                    "    a.login_norm\n"
                    "  FROM fact_user_activity_daily a\n"
                    "  JOIN latest_users u\n"
                    "    ON u.instance_name = a.instance_name\n"
                    "   AND u.login_norm = a.login_norm\n"
                    "  WHERE "
                    f"    a.last_activity_at >= {start_month_expr}\n"
                    f"    AND a.last_activity_at < {next_month_expr}\n"
                    "    AND u.rn = 1\n"
                    "    AND u.users_enabled = 'True'\n"
                    f"    {exclude_sql}\n"
                    f"    {instance_sql}\n"
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
                    "  FROM base_users_instance_metadata\n"
                    "  WHERE instance_name IS NOT NULL\n"
                    f"  {instances_filter_sql}\n"
                    ") i\n"
                    "LEFT JOIN agg a\n"
                    "  ON a.month_start = m.month_start AND a.instance_name = i.instance_name\n"
                    "ORDER BY m.month_start, i.instance_name;"
                ),
                [*exclude_params, *instance_params_by_instance],
            )

            aggregate_df = _query_df(
                (
                    "WITH months AS (\n"
                    f"  SELECT * FROM generate_series({start_month_expr}, ({next_month_expr} - INTERVAL 1 DAY)::DATE, INTERVAL 1 MONTH) AS t(month_start)\n"
                    "),\n"
                    f"{latest_users_cte}"
                    "activity AS (\n"
                    "  SELECT\n"
                    "    date_trunc('month', a.last_activity_at) AS month_start,\n"
                    "    a.instance_name,\n"
                    "    a.login_norm\n"
                    "  FROM fact_user_activity_daily a\n"
                    "  JOIN latest_users u\n"
                    "    ON u.instance_name = a.instance_name\n"
                    "   AND u.login_norm = a.login_norm\n"
                    "  WHERE "
                    f"    a.last_activity_at >= {start_month_expr}\n"
                    f"    AND a.last_activity_at < {next_month_expr}\n"
                    "    AND u.rn = 1\n"
                    "    AND u.users_enabled = 'True'\n"
                    f"    {exclude_sql}\n"
                    f"    {instance_sql}\n"
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

    @bp.route("/api/build/users/adoption-trends")
    def build_users_adoption_trends():
        """Period-aware adoption trends for the User Insights UI container.

        This route intentionally does not replace the legacy monthly endpoints used
        by report export. Supported periods are fixed calendar buckets:
        weekly, monthly, and quarterly.
        """

        try:
            _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
            _ensure_ready_if_enabled()

            period = _parse_adoption_period(request.args.get("period"))
            period_config = _adoption_period_config(period)
            reporting_date_sql = _ADOPTION_REPORTING_DATE_SQL
            reporting_cutoff_sql = f"({reporting_date_sql} + INTERVAL 1 DAY)::DATE"
            periods_cte = _adoption_periods_cte_sql(period, reporting_date_sql=reporting_date_sql)
            activity_filter = _parse_activity_filter(request.args.get("activityFilter"))
            instance_name = _parse_instance_name(request.args.get("instance_name"))

            standard = _read_standard_project_variables()
            excluded_profiles = _read_user_profile_exclude_consumer(standard)
            filter_sql_template, filter_params = _resolve_license_filter_clause(activity_filter)
            exclude_sql = _format_license_filter_clause(filter_sql_template, profile_expr="u.users_userprofile")
            exclude_params: list[Any] = list(filter_params)

            instance_sql_observed = ""
            instance_sql_formal = ""
            instances_filter_sql = ""
            instance_params_one: list[Any] = []
            instance_params_two: list[Any] = []
            if instance_name:
                instance_sql_observed = " AND a.instance_name = ?"
                instance_sql_formal = " AND f.instance_name = ?"
                instances_filter_sql = " AND instance_name = ?"
                instance_params_one = [instance_name]
                instance_params_two = [instance_name, instance_name]

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
                "  FROM base_users_instance_metadata\n"
                "  WHERE users_login IS NOT NULL\n"
                "    AND length(trim(users_login)) > 0\n"
                ")"
            )

            periods_df = _query_df(
                (
                    f"WITH {periods_cte}\n"
                    "SELECT\n"
                    "  CAST(period_start AS VARCHAR) AS periodStart,\n"
                    "  CAST(period_end AS VARCHAR) AS periodEnd,\n"
                    "  period_label AS label,\n"
                    "  is_current_period AS isCurrentPeriod,\n"
                    "  is_partial_period AS isPartialPeriod\n"
                    "FROM periods\n"
                    "ORDER BY period_start;"
                )
            )

            observed_common_cte = (
                f"WITH {periods_cte},\n"
                f"{latest_users_cte},\n"
                "activity AS (\n"
                "  SELECT\n"
                "    p.period_start,\n"
                "    a.instance_name,\n"
                "    a.login_norm\n"
                "  FROM periods p\n"
                "  JOIN fact_user_activity_daily a\n"
                "    ON a.last_activity_at >= p.period_start\n"
                f"   AND a.last_activity_at < least(p.period_end, {reporting_cutoff_sql})\n"
                "  JOIN latest_users u\n"
                "    ON u.instance_name = a.instance_name\n"
                "   AND u.login_norm = a.login_norm\n"
                "  WHERE u.rn = 1\n"
                "    AND u.users_enabled = 'True'\n"
                f"    {exclude_sql}\n"
                f"    {instance_sql_observed}\n"
                ")"
            )

            observed_aggregate_df = _query_df(
                (
                    f"{observed_common_cte},\n"
                    "agg AS (\n"
                    "  SELECT\n"
                    "    period_start,\n"
                    "    COUNT(DISTINCT concat(instance_name, '::', login_norm)) AS active_users\n"
                    "  FROM activity\n"
                    "  GROUP BY 1\n"
                    ")\n"
                    "SELECT\n"
                    "  CAST(p.period_start AS VARCHAR) AS periodStart,\n"
                    "  CAST(p.period_end AS VARCHAR) AS periodEnd,\n"
                    "  p.period_label AS label,\n"
                    "  p.is_current_period AS isCurrentPeriod,\n"
                    "  p.is_partial_period AS isPartialPeriod,\n"
                    "  COALESCE(a.active_users, 0) AS active_users\n"
                    "FROM periods p\n"
                    "LEFT JOIN agg a ON a.period_start = p.period_start\n"
                    "ORDER BY p.period_start;"
                ),
                [*exclude_params, *instance_params_one],
            )

            observed_by_instance_df = _query_df(
                (
                    f"{observed_common_cte},\n"
                    "agg AS (\n"
                    "  SELECT\n"
                    "    period_start,\n"
                    "    instance_name,\n"
                    "    COUNT(DISTINCT login_norm) AS active_users\n"
                    "  FROM activity\n"
                    "  GROUP BY 1, 2\n"
                    ")\n"
                    "SELECT\n"
                    "  CAST(p.period_start AS VARCHAR) AS periodStart,\n"
                    "  CAST(p.period_end AS VARCHAR) AS periodEnd,\n"
                    "  p.period_label AS label,\n"
                    "  p.is_current_period AS isCurrentPeriod,\n"
                    "  p.is_partial_period AS isPartialPeriod,\n"
                    "  i.instance_name,\n"
                    "  COALESCE(a.active_users, 0) AS active_users\n"
                    "FROM periods p\n"
                    "CROSS JOIN (\n"
                    "  SELECT DISTINCT instance_name\n"
                    "  FROM base_users_instance_metadata\n"
                    "  WHERE instance_name IS NOT NULL\n"
                    f"  {instances_filter_sql}\n"
                    ") i\n"
                    "LEFT JOIN agg a ON a.period_start = p.period_start AND a.instance_name = i.instance_name\n"
                    "ORDER BY p.period_start, i.instance_name;"
                ),
                [*exclude_params, *instance_params_one, *instance_params_one],
            )

            observed_extent_df = _query_df(
                (
                    "SELECT\n"
                    "  CAST(MIN(last_activity_at) AS VARCHAR) AS firstObservation,\n"
                    "  CAST(MAX(last_activity_at) AS VARCHAR) AS lastObservation\n"
                    "FROM fact_user_activity_daily\n"
                    f"WHERE last_activity_at < {reporting_cutoff_sql}"
                    f"{(' AND instance_name = ?') if instance_name else ''};"
                ),
                instance_params_one,
            )

            formal_available = _duckdb_relation_exists(_query_df, "final_build_formal_mau_daily")
            formal_payload: dict[str, Any]
            if not formal_available:
                formal_payload = {
                    "available": False,
                    "reason": "formal_mau_view_missing",
                    "aggregate": [],
                    "byInstance": [],
                    "byProfile": [],
                    "byInstanceProfile": [],
                    "currentPeriod": None,
                }
            else:
                formal_common_cte = (
                    f"WITH {periods_cte},\n"
                    "activity AS (\n"
                    "  SELECT\n"
                    "    p.period_start,\n"
                    "    f.instance_name,\n"
                    "    f.login_norm\n"
                    "  FROM periods p\n"
                    "  JOIN final_build_formal_mau_daily f\n"
                    "    ON f.last_application_open_at >= p.period_start\n"
                    f"   AND f.last_application_open_at < least(p.period_end, {reporting_cutoff_sql})\n"
                    f"  WHERE 1 = 1{instance_sql_formal}\n"
                    ")"
                )
                formal_aggregate_df = _query_df(
                    (
                        f"{formal_common_cte},\n"
                        "agg AS (\n"
                        "  SELECT\n"
                        "    period_start,\n"
                        "    COUNT(DISTINCT concat(instance_name, '::', login_norm)) AS active_users\n"
                        "  FROM activity\n"
                        "  GROUP BY 1\n"
                        ")\n"
                        "SELECT\n"
                        "  CAST(p.period_start AS VARCHAR) AS periodStart,\n"
                        "  CAST(p.period_end AS VARCHAR) AS periodEnd,\n"
                        "  p.period_label AS label,\n"
                        "  p.is_current_period AS isCurrentPeriod,\n"
                        "  p.is_partial_period AS isPartialPeriod,\n"
                        "  COALESCE(a.active_users, 0) AS active_users\n"
                        "FROM periods p\n"
                        "LEFT JOIN agg a ON a.period_start = p.period_start\n"
                        "ORDER BY p.period_start;"
                    ),
                    instance_params_one,
                )
                formal_by_instance_df = _query_df(
                    (
                        f"{formal_common_cte},\n"
                        "agg AS (\n"
                        "  SELECT\n"
                        "    period_start,\n"
                        "    instance_name,\n"
                        "    COUNT(DISTINCT login_norm) AS active_users\n"
                        "  FROM activity\n"
                        "  GROUP BY 1, 2\n"
                        ")\n"
                        "SELECT\n"
                        "  CAST(p.period_start AS VARCHAR) AS periodStart,\n"
                        "  CAST(p.period_end AS VARCHAR) AS periodEnd,\n"
                        "  p.period_label AS label,\n"
                        "  p.is_current_period AS isCurrentPeriod,\n"
                        "  p.is_partial_period AS isPartialPeriod,\n"
                        "  i.instance_name,\n"
                        "  COALESCE(a.active_users, 0) AS active_users\n"
                        "FROM periods p\n"
                        "CROSS JOIN (\n"
                        "  SELECT DISTINCT instance_name\n"
                        "  FROM base_users_instance_metadata\n"
                        "  WHERE instance_name IS NOT NULL\n"
                        f"  {instances_filter_sql}\n"
                        ") i\n"
                        "LEFT JOIN agg a ON a.period_start = p.period_start AND a.instance_name = i.instance_name\n"
                        "ORDER BY p.period_start, i.instance_name;"
                    ),
                    [*instance_params_one, *instance_params_one],
                )

                license_group_case_sql = _license_group_case_sql("user_profile")
                current_profile_cte = (
                    f"WITH {periods_cte},\n"
                    "current_period AS (\n"
                    "  SELECT period_start, period_end\n"
                    "  FROM periods\n"
                    "  WHERE is_current_period\n"
                    "),\n"
                    "latest_users AS (\n"
                    "  SELECT\n"
                    "    instance_name,\n"
                    "    lower(trim(users_login)) AS login_norm,\n"
                    "    coalesce(nullif(trim(users_userprofile), ''), 'UNKNOWN') AS user_profile,\n"
                    "    ROW_NUMBER() OVER (\n"
                    "      PARTITION BY instance_name, lower(trim(users_login))\n"
                    "      ORDER BY run_ts DESC\n"
                    "    ) AS rn\n"
                    "  FROM base_users_instance_metadata\n"
                    "  WHERE users_login IS NOT NULL\n"
                    "    AND length(trim(users_login)) > 0\n"
                    "),\n"
                    "latest_activity AS (\n"
                    "  SELECT\n"
                    "    f.instance_name,\n"
                    "    f.login_norm,\n"
                    "    u.user_profile\n"
                    "  FROM current_period p\n"
                    "  JOIN final_build_formal_mau_daily f\n"
                    "    ON f.last_application_open_at >= p.period_start\n"
                    f"   AND f.last_application_open_at < least(p.period_end, {reporting_cutoff_sql})\n"
                    "  JOIN latest_users u\n"
                    "    ON u.instance_name = f.instance_name\n"
                    "   AND u.login_norm = f.login_norm\n"
                    "   AND u.rn = 1\n"
                    f"  WHERE 1 = 1{instance_sql_formal}\n"
                    ")"
                )
                formal_by_profile_df = _query_df(  # nosec B608
                    (
                        f"{current_profile_cte}\n"
                        "SELECT\n"
                        f"  {license_group_case_sql} AS license_group,\n"
                        "  user_profile AS userProfile,\n"
                        "  COUNT(DISTINCT concat(instance_name, '::', login_norm)) AS active_users\n"
                        "FROM latest_activity\n"
                        "GROUP BY 1, 2\n"
                        "ORDER BY active_users DESC, license_group, userProfile;"
                    ),
                    instance_params_one,
                )
                formal_by_instance_profile_df = _query_df(  # nosec B608
                    (
                        f"{current_profile_cte}\n"
                        "SELECT\n"
                        "  instance_name,\n"
                        f"  {license_group_case_sql} AS license_group,\n"
                        "  user_profile AS userProfile,\n"
                        "  COUNT(DISTINCT login_norm) AS active_users\n"
                        "FROM latest_activity\n"
                        "GROUP BY 1, 2, 3\n"
                        "ORDER BY instance_name, active_users DESC, license_group, userProfile;"
                    ),
                    instance_params_one,
                )
                formal_aggregate_rows = _df_records(formal_aggregate_df)
                formal_payload = {
                    "available": True,
                    "aggregate": formal_aggregate_rows,
                    "byInstance": _df_records(formal_by_instance_df),
                    "byProfile": _df_records(formal_by_profile_df),
                    "byInstanceProfile": _df_records(formal_by_instance_profile_df),
                    "currentPeriod": next((row for row in formal_aggregate_rows if row.get("isCurrentPeriod")), formal_aggregate_rows[-1] if formal_aggregate_rows else None),
                }

            period_rows = _df_records(periods_df)
            observed_aggregate_rows = _df_records(observed_aggregate_df)
            return _ok(
                {
                    "period": period,
                    "periodCount": period_config["count"],
                    "instanceName": instance_name,
                    "activityFilter": activity_filter,
                    "reportingDateConvention": "DuckDB current_date calendar buckets; current periods include observations before tomorrow's current_date boundary.",
                    "periods": period_rows,
                    "observed": {
                        "aggregate": observed_aggregate_rows,
                        "byInstance": _df_records(observed_by_instance_df),
                        "currentPeriod": next((row for row in observed_aggregate_rows if row.get("isCurrentPeriod")), observed_aggregate_rows[-1] if observed_aggregate_rows else None),
                        "history": (_df_records(observed_extent_df)[0] if not observed_extent_df.empty else {}),
                        "meta": {
                            "excludedProfiles": excluded_profiles if activity_filter == "license_consumer" else [],
                            "excludedProfilesSource": "pulse_dashboard.configs.terminology_yaml.license_groups.license_consumer",
                        },
                    },
                    "formal": formal_payload,
                }
            )

        except RequestValidationError as e:
            return _err(str(e), status=400)
        except Exception as e:
            logger.exception("users adoption trends failed")
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
            segment_exclude_params = exclude_params * 7

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
                activity_window_sql = "last_activity_at >= current_date - ?::INTEGER"
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
                    "  FROM base_users_instance_metadata\n"
                    "  WHERE users_login IS NOT NULL AND length(trim(users_login)) > 0\n"
                    f"    {latest_instance_sql}\n"
                    "),\n"
                    "activity AS (\n"
                    "  SELECT\n"
                    "    lower(trim(login_norm)) AS login_norm,\n"
                    "    SUM(viewing_actions_count) AS viewing,\n"
                    "    SUM(developing_actions_count) AS developing\n"
                    "  FROM fact_user_activity_daily\n"
                    f"  WHERE {activity_window_sql}\n"
                    f"    {activity_instance_sql}\n"
                    "  GROUP BY 1\n"
                    ")\n"
                    "SELECT\n"
                    "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True'"
                    + f"{exclude_sql}) AS enabled_users,\n"
                    "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.viewing, 0) > 0 AND coalesce(a.developing, 0) = 0"
                    + f"{exclude_sql}) AS viewer_only_users,\n"
                    "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.developing, 0) > 0 AND coalesce(a.viewing, 0) = 0"
                    + f"{exclude_sql}) AS developer_only_users,\n"
                    "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.viewing, 0) > 0 AND coalesce(a.developing, 0) > 0"
                    + f"{exclude_sql}) AS mixed_users,\n"
                    "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.viewing, 0) > coalesce(a.developing, 0) AND coalesce(a.developing, 0) > 0"
                    + f"{exclude_sql}) AS viewer_dominant_users,\n"
                    "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.developing, 0) > coalesce(a.viewing, 0) AND coalesce(a.viewing, 0) > 0"
                    + f"{exclude_sql}) AS developer_dominant_users,\n"
                    "  COUNT(DISTINCT l.login_norm) FILTER (WHERE l.users_enabled = 'True' AND coalesce(a.viewing, 0) = coalesce(a.developing, 0) AND coalesce(a.viewing, 0) > 0"
                    + f"{exclude_sql}) AS balanced_mixed_users\n"
                    "FROM latest l\n"
                    "LEFT JOIN activity a ON a.login_norm = l.login_norm\n"
                    "WHERE l.rn = 1;"
                ),
                [*instance_params, *activity_params, *instance_params, *segment_exclude_params],
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
                    "  JOIN base_users_instance_metadata u ON TRUE\n"
                    "  WHERE u.users_login IS NOT NULL\n"
                    "    AND length(trim(u.users_login)) > 0\n"
                    "    AND u.users_enabled = 'True'\n"
                    f"    {exclude_sql}\n"
                    f"    {instance_sql_users}\n"
                    "  GROUP BY 1\n"
                    "),\n"
                    "activity AS (\n"
                    "  SELECT\n"
                    "    date_trunc('month', a.last_activity_at) AS month_start,\n"
                    "    a.login_norm\n"
                    "  FROM fact_user_activity_daily a\n"
                    "  JOIN base_users_instance_metadata u\n"
                    "    ON u.instance_name = a.instance_name\n"
                    "   AND lower(trim(u.users_login)) = a.login_norm\n"
                    "  WHERE a.last_activity_at >= " + start_month_expr + "\n"
                    "    AND a.last_activity_at < " + next_month_expr + "\n"
                    "    AND u.users_enabled = 'True'\n"
                    f"    {exclude_sql}\n"
                    f"    {instance_sql_activity}\n"
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
                date_trunc('month', f.last_application_open_at) AS month_start,
                f.instance_name,
                f.login_norm
              FROM final_build_formal_mau_daily f
              WHERE f.last_application_open_at >= {start_month_expr}
                AND f.last_application_open_at < {next_month_expr}
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
              FROM base_users_instance_metadata
              WHERE instance_name IS NOT NULL
              {instances_filter_sql}
            ) i
            LEFT JOIN agg a
              ON a.month_start = m.month_start AND a.instance_name = i.instance_name
            ORDER BY m.month_start, i.instance_name;
            """,
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
                date_trunc('month', f.last_application_open_at) AS month_start,
                f.instance_name,
                f.login_norm
              FROM final_build_formal_mau_daily f
              WHERE f.last_application_open_at >= {start_month_expr}
                AND f.last_application_open_at < {next_month_expr}
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
            """,
                instance_params_aggregate,
            )

            license_group_case_sql = _license_group_case_sql("user_profile")
            latest_month_profiles_df = _query_df(  # nosec B608
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
              FROM base_users_instance_metadata
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
              WHERE f.last_application_open_at >= date_trunc('month', current_date)::DATE
                AND f.last_application_open_at < {next_month_expr}
                {instance_sql}
            )
            SELECT
              {license_group_case_sql} AS license_group,
              user_profile AS userProfile,
              COUNT(DISTINCT concat(instance_name, '::', login_norm)) AS active_users
            FROM latest_activity
            GROUP BY 1, 2
            ORDER BY active_users DESC, license_group, userProfile;
            """,
                instance_params_aggregate,
            )

            latest_month_instance_profiles_df = _query_df(  # nosec B608
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
              FROM base_users_instance_metadata
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
              WHERE f.last_application_open_at >= date_trunc('month', current_date)::DATE
                AND f.last_application_open_at < {next_month_expr}
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
            """,
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
                "WITH latest AS (\n"  # nosec B608
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
                "  FROM base_users_instance_metadata\n"
                "  WHERE users_login IS NOT NULL AND length(trim(users_login)) > 0\n"
                f"    {latest_instance_sql}\n"
                "),\n"
                "activity_6m AS (\n"
                "  SELECT\n"
                "    instance_name,\n"
                "    lower(trim(login_norm)) AS login_norm,\n"
                "    SUM(CASE WHEN last_activity_at >= "
                f"{six_month_start_expr}"
                " THEN viewing_actions_count ELSE 0 END) AS viewing_6m,\n"
                "    SUM(CASE WHEN last_activity_at >= "
                f"{six_month_start_expr}"
                " THEN developing_actions_count ELSE 0 END) AS developing_6m,\n"
                "    MAX(last_activity_at) AS last_activity_at\n"
                "  FROM fact_user_activity_daily\n"
                "  WHERE 1 = 1"
                f"{activity_instance_sql}\n"
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
