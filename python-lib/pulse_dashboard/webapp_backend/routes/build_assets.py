from __future__ import annotations

import json
import logging
import re
from typing import Any

from flask import Blueprint, request

from pulse_dashboard.webapp_backend.services.users import _parse_csv_list
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
    "project": {"table": "base_projects_instance_metadata", "key_col": "project_key", "project_scoped": False},
    "dataset": {"table": "base_datasets_project_metadata", "key_col": "datasets_name", "project_scoped": True},
    "recipe": {"table": "base_recipes_project_metadata", "key_col": "recipes_name", "project_scoped": True},
    "scenario": {"table": "base_scenarios_project_metadata", "key_col": "scenarios_id", "project_scoped": True},
}


def _to_jsonable(value: Any) -> Any:
    try:
        import numpy as np

        if isinstance(value, np.ndarray):
            return value.tolist()
    except Exception:
        pass

    return value


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


def _asset_id_expr_for_build_assets() -> str:
    return "md5(concat_ws('|', instance_name, project_key, object_type, object_key))"


def _non_empty_sql(column_sql: str) -> str:
    return f"CASE WHEN {column_sql} IS NOT NULL AND length(trim(CAST({column_sql} AS VARCHAR))) > 0 THEN 1 ELSE 0 END"


def _metadata_status_from_score(score: float) -> str:
    if score >= 99.9:
        return "complete"
    if score >= 50.0:
        return "partial"
    return "sparse"


def _asset_inventory_metadata_score(row: dict[str, Any]) -> float:
    checks = [
        bool(str(row.get("objectName") or "").strip()),
        bool(str(row.get("objectKey") or "").strip()),
        bool(str(row.get("ownerLogin") or "").strip()),
        row.get("createdAt") is not None,
        row.get("updatedAt") is not None,
    ]
    if row.get("objectType") in {"dataset", "recipe", "scenario"}:
        checks.insert(2, bool(str(row.get("objectSubtype") or "").strip()))
    return 100.0 * (sum(1 for ok in checks if ok) / len(checks)) if checks else 0.0


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


def _self_scope_requested() -> bool:
    return (request.args.get("scope") or "").strip().lower() == "self"


def _current_authenticated_login() -> str | None:
    auth_info = _current_user_auth_info() or {}
    login = str(auth_info.get("authIdentifier") or "").strip()
    return login or None


def _resolve_scoped_owner_login() -> str | None:
    if not _self_scope_requested():
        return None
    owner = str(request.args.get("owner") or "").strip()
    if owner:
        return owner.lower()
    return _current_authenticated_login()


def register_routes(bp: Blueprint) -> None:
    @bp.route("/api/build/assets")
    def build_assets_list():
        try:
            query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
            _ensure_ready_if_enabled()

            q = (request.args.get("q") or "").strip()
            instances = _parse_csv_list(request.args.get("instances"))
            projects = _parse_csv_list(request.args.get("projects"))
            types = _parse_csv_list(request.args.get("types"))
            owner = (request.args.get("owner") or "").strip()
            completeness_status = (request.args.get("completenessStatus") or "").strip().lower()
            sort = (request.args.get("sort") or "updated_desc").strip()
            score_sql, status_sql = _metadata_completeness_sql_for_asset_inventory()
            scoped_login = _resolve_scoped_owner_login()

            if _self_scope_requested() and not scoped_login:
                return _err("Unable to resolve authenticated user", status=403)

            limit = int(request.args.get("limit") or 25)
            offset = int(request.args.get("offset") or 0)

            where: list[str] = []
            params: list[object] = []

            if q:
                where.append(
                    "(lower(t.object_name) LIKE ? OR lower(t.object_key) LIKE ? OR lower(t.project_key) LIKE ? OR lower(t.instance_name) LIKE ? OR lower(t.owner_login) LIKE ?)"
                )
                qlike = f"%{q.lower()}%"
                params.extend([qlike, qlike, qlike, qlike, qlike])

            if instances:
                where.append(f"t.instance_name IN ({','.join(['?'] * len(instances))})")
                params.extend(instances)

            if projects:
                where.append(f"t.project_key IN ({','.join(['?'] * len(projects))})")
                params.extend(projects)

            if types:
                where.append(f"t.object_type IN ({','.join(['?'] * len(types))})")
                params.extend(types)

            if owner:
                where.append("lower(t.owner_login) LIKE ?")
                params.append(f"%{owner.lower()}%")

            if scoped_login:
                where.append("lower(COALESCE(t.owner_login, '')) = ?")
                params.append(scoped_login.lower())

            if completeness_status:
                if completeness_status not in {"complete", "partial", "sparse"}:
                    return _err(f"Invalid completenessStatus: {completeness_status}")
                where.append(f"{status_sql} = ?")
                params.append(completeness_status)

            where_sql = ("WHERE " + " AND ".join(where)) if where else ""

            order_by = "t.updated_at DESC NULLS LAST"
            if sort == "updated_asc":
                order_by = "t.updated_at ASC NULLS LAST"
            elif sort == "activity_desc":
                order_by = "t.activity_30d DESC NULLS LAST"
            elif sort == "completeness_desc":
                order_by = f"{score_sql} DESC, t.updated_at DESC NULLS LAST"
            elif sort == "completeness_asc":
                order_by = f"{score_sql} ASC, t.updated_at DESC NULLS LAST"
            elif sort == "name_asc":
                order_by = "t.object_name ASC NULLS LAST"

            asset_count_sql = "\n".join(
                [
                    "SELECT COUNT(*) AS n",
                    "FROM (",
                    "  SELECT",
                    "    idx.*, COALESCE(act.activity_30d, 0) AS activity_30d",
                    "  FROM base_asset_index idx",
                    "  LEFT JOIN asset_activity_30d act",
                    "    ON act.instance_name = idx.instance_name",
                    "   AND act.project_key = idx.project_key",
                    "   AND act.object_type = idx.object_type",
                    "   AND act.object_key = idx.object_key",
                    ") t",
                    where_sql + ";",
                ]
            )
            count_df = query_df(asset_count_sql, params)
            total = int(count_df.iloc[0]["n"]) if len(count_df.index) else 0

            asset_rows_sql = "\n".join(
                [
                    "SELECT",
                    "  md5(concat_ws('|', t.instance_name, t.project_key, t.object_type, t.object_key)) AS assetId,",
                    "  t.instance_name AS instanceName,",
                    "  t.project_key AS projectKey,",
                    "  t.object_type AS objectType,",
                    "  t.object_key AS objectKey,",
                    "  t.object_name AS objectName,",
                    "  t.owner_login AS ownerLogin,",
                    "  t.updated_at AS updatedAt,",
                    "  t.object_subtype AS objectSubtype,",
                    "  t.activity_30d AS activity30d",
                    f"  ,ROUND(({score_sql}), 1) AS metadataCompletenessScore",
                    f"  ,{status_sql} AS metadataCompletenessStatus",
                    "FROM (",
                    "  SELECT",
                    "    idx.*, COALESCE(act.activity_30d, 0) AS activity_30d",
                    "  FROM base_asset_index idx",
                    "  LEFT JOIN asset_activity_30d act",
                    "    ON act.instance_name = idx.instance_name",
                    "   AND act.project_key = idx.project_key",
                    "   AND act.object_type = idx.object_type",
                    "   AND act.object_key = idx.object_key",
                    ") t",
                    where_sql,
                    "ORDER BY " + order_by,
                    "LIMIT ? OFFSET ?;",
                ]
            )
            rows_df = query_df(asset_rows_sql, params + [limit, offset])

            return _ok({"total": total, "rows": _df_records(rows_df)})
        except Exception as exc:
            logger.exception("assets list failed")
            return _err(str(exc), status=500)

    @bp.route("/api/build/assets/metadata-summary")
    def build_assets_metadata_summary():
        try:
            query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
            _ensure_ready_if_enabled()
            scoped_login = _resolve_scoped_owner_login()

            if _self_scope_requested() and not scoped_login:
                return _err("Unable to resolve authenticated user", status=403)

            where_sql = ""
            params: list[object] = []
            if scoped_login:
                where_sql = "\nWHERE lower(COALESCE(owner_login, '')) = ?"
                params.append(scoped_login.lower())

            rows_df = query_df(
                (
                    "SELECT\n"
                    "  object_type AS objectType,\n"
                    "  object_name AS objectName,\n"
                    "  object_key AS objectKey,\n"
                    "  object_subtype AS objectSubtype,\n"
                    "  owner_login AS ownerLogin,\n"
                    "  created_at AS createdAt,\n"
                    "  updated_at AS updatedAt\n"
                    f"FROM base_asset_index{where_sql};"
                ),
                params,
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
                        "avgScore": avg_score,
                        "completeCount": complete_count,
                        "partialCount": partial_count,
                        "sparseCount": sparse_count,
                    },
                    "byType": by_type_rows,
                }
            )
        except Exception as exc:
            logger.exception("assets metadata summary failed")
            return _err(str(exc), status=500)

    @bp.route("/api/build/assets/facets")
    def build_assets_facets():
        try:
            query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
            _ensure_ready_if_enabled()
            scoped_login = _resolve_scoped_owner_login()

            if _self_scope_requested() and not scoped_login:
                return _err("Unable to resolve authenticated user", status=403)

            where_sql = ""
            params: list[object] = []
            if scoped_login:
                where_sql = "\n                WHERE lower(COALESCE(owner_login, '')) = ?"
                params.append(scoped_login.lower())

            df = query_df(
                f"""
                SELECT
                  array_agg(DISTINCT instance_name ORDER BY instance_name) AS instances,
                  array_agg(DISTINCT project_key ORDER BY project_key) AS projects,
                  array_agg(DISTINCT object_type ORDER BY object_type) AS types,
                  array_agg(DISTINCT owner_login ORDER BY owner_login) AS owners
                FROM base_asset_index{where_sql};
                """,
                params,
            )
            row = df.iloc[0].to_dict() if len(df.index) else {}
            row = {key: _to_jsonable(value) for key, value in row.items()}
            return _ok(row)
        except Exception as exc:
            logger.exception("assets facets failed")
            return _err(str(exc), status=500)

    @bp.route("/api/build/assets/details")
    def build_assets_details():
        try:
            query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
            _ensure_ready_if_enabled()
            scoped_login = _resolve_scoped_owner_login()

            if _self_scope_requested() and not scoped_login:
                return _err("Unable to resolve authenticated user", status=403)

            asset_id = (request.args.get("assetId") or "").strip()
            if not _is_md5(asset_id):
                return _err("Invalid or missing assetId", status=400)

            asset_lookup_sql = "\n".join(
                [
                    "SELECT",
                    "  instance_name AS instanceName,",
                    "  project_key AS projectKey,",
                    "  object_type AS objectType,",
                    "  object_key AS objectKey,",
                    "  object_name AS objectName,",
                    "  owner_login AS ownerLogin,",
                    "  updated_at AS updatedAt",
                    "FROM base_asset_index",
                    "WHERE " + _asset_id_expr_for_build_assets() + " = ?"
                    + (" AND lower(COALESCE(owner_login, '')) = ?" if scoped_login else ""),
                    "LIMIT 1;",
                ]
            )
            params: list[object] = [asset_id]
            if scoped_login:
                params.append(scoped_login.lower())
            df = query_df(asset_lookup_sql, params)

            if not len(df.index):
                return _err("Asset not found", status=404)

            row = _df_records(df)[0]
            instance_name = str(row.get("instanceName") or "")
            project_key = str(row.get("projectKey") or "")
            object_type = str(row.get("objectType") or "")
            object_key = str(row.get("objectKey") or "")

            usage, related_assets = _fetch_usage_and_related_assets(
                query_df=query_df,
                project_key=project_key or None,
                object_type=object_type,
                object_key=object_key,
            )

            description = None
            try:
                description = _fetch_description(
                    query_df=query_df,
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
        except Exception as exc:
            logger.exception("assets details failed")
            return _err(str(exc), status=500)
