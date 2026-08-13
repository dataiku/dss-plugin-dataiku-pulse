from __future__ import annotations

import json
import logging
import re
from typing import Any

from flask import Blueprint, request

from pulse_dashboard.webapp_backend.services.users import (
    RequestValidationError,
    _parse_csv_list,
    _parse_days_arg,
    _parse_int_arg,
)
from pulse_dashboard.webapp_backend.support import (
    _current_user_auth_info,
    _df_records,
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

_MD5_RE = re.compile(r"^[a-fA-F0-9]{32}$")

_OBJECT_EXTRAS_SOURCES: dict[str, dict[str, str | bool]] = {
    "api_service": {"table": "base_api_services_project_metadata", "key_col": "api_services_id", "project_scoped": True},
    "agent_tool": {"table": "base_agent_tools_project_metadata", "key_col": "agent_tools_id", "project_scoped": True},
    "insight": {"table": "base_insights_project_metadata", "key_col": "insights_id", "project_scoped": True},
    "web_application": {"table": "base_webapps_project_metadata", "key_col": "webapps_id", "project_scoped": True},
    "dataiku_application": {"table": "base_apps_instance_metadata", "key_col": "apps_appid", "project_scoped": False},
}

_PRODUCT_TO_EVENT_OBJECT_TYPE = {
    "api_service": "api_service",
    "insight": "insight",
    "agent_tool": "agent_tool",
}


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
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        for key, value in payload.items():
            if "description" in str(key).lower() and isinstance(value, str) and value.strip():
                return value.strip()

    return None


def _metadata_status_from_score(score: float) -> str:
    if score >= 99.9:
        return "complete"
    if score >= 50.0:
        return "partial"
    return "sparse"


def _product_inventory_metadata_score(row: dict[str, Any]) -> float:
    checks = [
        bool(str(row.get("productName") or "").strip()),
        bool(str(row.get("productKey") or "").strip()),
        bool(str(row.get("productType") or "").strip()),
        bool(str(row.get("ownerLogin") or "").strip()),
        row.get("createdAt") is not None,
        row.get("updatedAt") is not None,
    ]
    return 100.0 * (sum(1 for ok in checks if ok) / len(checks)) if checks else 0.0


def _non_empty_sql(column_sql: str) -> str:
    return f"CASE WHEN {column_sql} IS NOT NULL AND length(trim(CAST({column_sql} AS VARCHAR))) > 0 THEN 1 ELSE 0 END"


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


def _parse_pagination(*, default_limit: int = 25, max_limit: int = 5000) -> tuple[int, int]:
    limit = _parse_int_arg("limit", default=default_limit, minimum=1, maximum=max_limit)
    offset = _parse_int_arg("offset", default=0, minimum=0)
    return int(limit or default_limit), int(offset or 0)


def _fetch_usage_and_related_assets(*, query_df, project_key: str | None, object_type: str, object_key: str) -> tuple[int, list[dict[str, Any]]]:
    params: list[object] = [object_type, object_key]
    where = "object_type = ? AND object_key = ?"
    if project_key is not None:
        where += " AND project_key = ?"
        params.append(project_key)

    usage_sql = "\n".join(["SELECT COUNT(*) AS n FROM v_object_activity_events WHERE", where + ";"])
    usage_df = query_df(usage_sql, params)
    usage = int(usage_df.iloc[0]["n"]) if len(usage_df.index) else 0

    related_sql = "\n".join(
        [
            "SELECT",
            "  instance_name AS instanceName,",
            "  project_key AS projectKey,",
            "  COUNT(*) AS eventCount",
            "FROM v_object_activity_events",
            "WHERE " + where,
            "GROUP BY 1, 2",
            "ORDER BY eventCount DESC, instanceName, projectKey;",
        ]
    )
    related_df = query_df(related_sql, params)

    return usage, _df_records(related_df)


def _fetch_description(*, query_df, instance_name: str, project_key: str | None, object_type: str, object_key: str) -> str | None:
    spec = _OBJECT_EXTRAS_SOURCES.get(object_type)
    if not spec:
        return None

    table = str(spec["table"])
    key_col = str(spec["key_col"])
    project_scoped = bool(spec.get("project_scoped", False))

    where = ["instance_name = ?", f"{key_col} = ?"]
    params: list[object] = [instance_name, object_key]

    if project_scoped:
        if not project_key:
            return None
        where.append("project_key = ?")
        params.append(project_key)

    description_sql = "\n".join(
        [
            "SELECT extras FROM",
            table,
            "WHERE " + " AND ".join(where),
            "LIMIT 1;",
        ]
    )
    df = query_df(description_sql, params)
    if not len(df.index):
        return None

    extras = df.iloc[0].get("extras")
    return _extract_description_from_extras(extras if isinstance(extras, str) else None)


def _product_facets_payload() -> dict[str, list[str]]:
    return _product_facets_payload_for_owner(owner_login=None)


def _self_scope_requested() -> bool:
    return (request.args.get("scope") or "").strip().lower() == "self"


def _current_authenticated_login() -> str | None:
    auth_info = _current_user_auth_info() or {}
    login = str(auth_info.get("authIdentifier") or "").strip()
    return login or None


def _product_facets_payload_for_owner(*, owner_login: str | None) -> dict[str, list[str]]:
    _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
    _ensure_ready_if_enabled()

    where_sql = ""
    params: list[Any] = []
    if owner_login:
        where_sql = " WHERE lower(COALESCE(owner_login, '')) = ?"
        params.append(owner_login.lower())

    instances = (
        _query_df(f"SELECT DISTINCT instance_name FROM final_build_products_catalog{where_sql} ORDER BY 1;", params)["instance_name"]
        .dropna()
        .astype(str)
        .tolist()
    )
    projects = (
        _query_df(f"SELECT DISTINCT project_key FROM final_build_products_catalog{where_sql} ORDER BY 1;", params)["project_key"]
        .dropna()
        .astype(str)
        .tolist()
    )
    types = (
        _query_df(f"SELECT DISTINCT product_type FROM final_build_products_catalog{where_sql} ORDER BY 1;", params)["product_type"]
        .dropna()
        .astype(str)
        .tolist()
    )
    owners = (
        _query_df(f"SELECT DISTINCT owner_login FROM final_build_products_catalog{where_sql} ORDER BY 1;", params)["owner_login"]
        .dropna()
        .astype(str)
        .tolist()
    )

    return {"instances": instances, "projects": projects, "types": types, "owners": owners}


def register_routes(bp: Blueprint) -> None:
    @bp.route("/api/build/products/facets")
    def build_products_facets():
        try:
            scoped_login = _current_authenticated_login() if _self_scope_requested() else None
            if _self_scope_requested() and not scoped_login:
                return _err("Unable to resolve authenticated user", status=403)
            return _ok(_product_facets_payload_for_owner(owner_login=scoped_login))
        except Exception as exc:
            logger.exception("products facets failed")
            return _err(str(exc), status=500)

    @bp.route("/api/build/products/details")
    def build_products_details():
        try:
            _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
            _ensure_ready_if_enabled()
            scoped_login = _current_authenticated_login() if _self_scope_requested() else None

            if _self_scope_requested() and not scoped_login:
                return _err("Unable to resolve authenticated user", status=403)

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
                  AND (? IS NULL OR lower(COALESCE(owner_login, '')) = ?)
                LIMIT 1;
                """.strip(),
                [asset_id, scoped_login, scoped_login.lower() if scoped_login else None],
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
                query_df=_query_df,
                project_key=project_key or None,
                object_type=event_object_type,
                object_key=product_key,
            )

            description = None
            try:
                description = _fetch_description(
                    query_df=_query_df,
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
        except Exception as exc:
            logger.exception("products details failed")
            return _err(str(exc), status=500)

    @bp.route("/api/build/products")
    def build_products():
        try:
            _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
            _ensure_ready_if_enabled()
            scoped_login = _current_authenticated_login() if _self_scope_requested() else None

            if _self_scope_requested() and not scoped_login:
                return _err("Unable to resolve authenticated user", status=403)

            q = (request.args.get("q") or "").strip()
            owner = (request.args.get("owner") or "").strip()
            instances = _parse_csv_list(request.args.get("instances"))
            projects = _parse_csv_list(request.args.get("projects"))
            types = _parse_csv_list(request.args.get("types"))
            completeness_status = (request.args.get("completenessStatus") or "").strip().lower()
            sort = (request.args.get("sort") or "updated_desc").strip()
            limit, offset = _parse_pagination(default_limit=25, max_limit=5000)
            score_sql, status_sql = _metadata_completeness_sql_for_product_inventory()

            limit = max(1, min(5000, limit))
            offset = max(0, offset)

            where: list[str] = []
            params: list[Any] = []

            if q:
                where.append("(lower(product_name) LIKE ? OR lower(product_key) LIKE ?)")
                qq = f"%{q.lower()}%"
                params.extend([qq, qq])

            if owner:
                where.append("owner_login = ?")
                params.append(owner)

            if scoped_login:
                where.append("lower(COALESCE(owner_login, '')) = ?")
                params.append(scoped_login.lower())

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

            count_sql = f"SELECT COUNT(*) AS n FROM final_build_products_catalog{where_sql};"  # nosec B608
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
                f"FROM final_build_products_catalog{where_sql}\n"  # nosec B608
                f"ORDER BY {order_by}\n"  # nosec B608
                "LIMIT ? OFFSET ?;"
            )
            rows = _query_df(sql, [*params, limit, offset])

            return _ok({"rows": _df_records(rows), "total": total})
        except RequestValidationError as exc:
            return _err(str(exc), status=400)
        except Exception as exc:
            logger.exception("products query failed")
            return _err(str(exc), status=500)

    @bp.route("/api/build/products/metadata-summary")
    def build_products_metadata_summary():
        try:
            _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
            _ensure_ready_if_enabled()
            scoped_login = _current_authenticated_login() if _self_scope_requested() else None

            if _self_scope_requested() and not scoped_login:
                return _err("Unable to resolve authenticated user", status=403)

            where_sql = ""
            params: list[Any] = []
            if scoped_login:
                where_sql = "\nWHERE lower(COALESCE(owner_login, '')) = ?"
                params.append(scoped_login.lower())

            rows_df = _query_df(
                (
                    "SELECT\n"
                    "  product_type AS productType,\n"
                    "  product_name AS productName,\n"
                    "  product_key AS productKey,\n"
                    "  owner_login AS ownerLogin,\n"
                    "  created_at AS createdAt,\n"
                    "  updated_at AS updatedAt\n"
                    f"FROM final_build_products_catalog{where_sql};"
                ),
                params,
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
        except Exception as exc:
            logger.exception("products metadata summary failed")
            return _err(str(exc), status=500)

    @bp.route("/api/build/products/type-metrics")
    def build_products_type_metrics():
        try:
            _query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
            _ensure_ready_if_enabled()
            scoped_login = _current_authenticated_login() if _self_scope_requested() else None

            if _self_scope_requested() and not scoped_login:
                return _err("Unable to resolve authenticated user", status=403)

            product_type = (request.args.get("type") or "").strip()
            if not product_type:
                return _err("Missing type", status=400)

            days = _parse_days_arg(default=30)

            allowed_sql = "SELECT DISTINCT product_type FROM final_build_products_catalog"
            allowed_params: list[Any] = []
            if scoped_login:
                allowed_sql += " WHERE lower(COALESCE(owner_login, '')) = ?"
                allowed_params.append(scoped_login.lower())
            allowed_sql += " ORDER BY 1;"
            allowed_df = _query_df(allowed_sql, allowed_params)
            allowed = set(str(row.get("product_type") or "") for row in _df_records(allowed_df))
            if product_type not in allowed:
                return _err("Invalid type", status=400)

            owner_where = " AND lower(COALESCE(owner_login, '')) = ?" if scoped_login else ""
            owner_params: list[Any] = [scoped_login.lower()] if scoped_login else []

            kpis_df = _query_df(
                (
                    "SELECT\n"
                    "  COUNT(*) AS total_products,\n"
                    "  COUNT(*) FILTER (WHERE activity_30d > 0) AS active_products_30d,\n"
                    "  SUM(activity_30d) AS events_30d,\n"
                    "  MAX(last_activity_at) AS last_activity_at\n"
                    "FROM final_build_products_catalog\n"
                    f"WHERE product_type = ?{owner_where};"
                ),
                [product_type, *owner_params],
            )
            kpis_row = _df_records(kpis_df)[0] if len(kpis_df.index) else {}

            owners_df = _query_df(
                (
                    "SELECT\n"
                    "  owner_login AS label,\n"
                    "  COUNT(*) AS value\n"
                    "FROM final_build_products_catalog\n"
                    f"WHERE product_type = ?{owner_where}\n"
                    "GROUP BY 1\n"
                    "ORDER BY value DESC\n"
                    "LIMIT 12;"
                ),
                [product_type, *owner_params],
            )

            top_products_df = _query_df(
                (
                    "SELECT\n"
                    "  product_id AS productId,\n"
                    "  product_name AS label,\n"
                    "  activity_30d AS value\n"
                    "FROM final_build_products_catalog\n"
                    f"WHERE product_type = ?{owner_where}\n"
                    "ORDER BY value DESC NULLS LAST, product_name\n"
                    "LIMIT 12;"
                ),
                [product_type, *owner_params],
            )

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
        except RequestValidationError as exc:
            return _err(str(exc), status=400)
        except Exception as exc:
            logger.exception("products type metrics failed")
            return _err(str(exc), status=500)
