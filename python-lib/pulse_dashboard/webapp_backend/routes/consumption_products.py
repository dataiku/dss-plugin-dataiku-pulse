from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from flask import Blueprint, request

from pulse_dashboard.webapp_backend.services.users import _parse_days_arg
from pulse_dashboard.webapp_backend.support import (
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

_SUPPORTED_CONSUMPTION_PRODUCT_TYPES = (
    "agent_tool",
    "api_service",
    "dashboard",
    "dataiku_application",
    "insight",
    "retrieval_augmented_llm",
    "saved_model",
    "web_application",
)

_MD5_RE = re.compile(r"^[a-fA-F0-9]{32}$")


def _is_md5(value: str | None) -> bool:
    if not value:
        return False
    return bool(_MD5_RE.match(str(value).strip()))


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


def _supported_consumption_product_types() -> list[str]:
    return list(_SUPPORTED_CONSUMPTION_PRODUCT_TYPES)


def _parse_string_list_param(value: str | None) -> list[str]:
    if not value:
        return []
    items = [part.strip() for part in str(value).split(",")]
    return [part for part in items if part]


@dataclass(frozen=True)
class ConsumptionProductQueryContext:
    matched_events_cte: str
    params: list[Any]
    days: int
    filters: dict[str, Any]
    matched_where_sql: str
    matched_where_params: list[Any]


def _consumption_product_filters_from_request(*, default_days: int = 30) -> dict[str, Any]:
    days = _parse_days_arg(default=default_days)
    q = (request.args.get("q") or "").strip()
    instances = _parse_string_list_param(request.args.get("instances"))
    projects = _parse_string_list_param(request.args.get("projects"))
    types = _parse_string_list_param(request.args.get("types"))
    owner = (request.args.get("owner") or "").strip()
    allowed_types = set(_supported_consumption_product_types())
    return {
        "days": days,
        "q": q,
        "instances": instances,
        "projects": projects,
        "types": [value for value in types if value in allowed_types],
        "owner": owner,
    }


def _consumption_product_summary_sql(context: ConsumptionProductQueryContext, select_sql: str) -> str:
    return f"WITH {context.matched_events_cte}\n{select_sql.strip()}"


def _build_consumption_product_query_context(
    *,
    days: int,
    q: str,
    instances: list[str],
    projects: list[str],
    types: list[str],
    owner: str,
) -> ConsumptionProductQueryContext:
    days_val = max(1, min(365, int(days)))
    params: list[Any] = []
    object_types = types or _supported_consumption_product_types()
    catalog_type_sql = ", ".join("'" + product_type.replace("'", "''") + "'" for product_type in object_types)
    event_type_sql = ", ".join("'" + product_type.replace("'", "''") + "'" for product_type in object_types)

    eligible_event_filters: list[str] = [
        f"e.timestamp >= now() - INTERVAL '{days_val} days'",
        f"e.object_type IN ({event_type_sql})",
        "e.object_key IS NOT NULL",
        "length(trim(CAST(e.object_key AS VARCHAR))) > 0",
    ]
    if instances:
        eligible_event_filters.append("e.instance_name IN (" + ", ".join(["?"] * len(instances)) + ")")
        params.extend(instances)
    if projects:
        eligible_event_filters.append("e.project_key IN (" + ", ".join(["?"] * len(projects)) + ")")
        params.extend(projects)

    matching_ctes_sql = f"""
catalog AS (
    SELECT
        product_id,
        instance_name,
        project_key,
        product_type,
        product_key,
        product_name,
        owner_login,
        lower(trim(CAST(instance_name AS VARCHAR))) AS normalized_instance_name,
        lower(NULLIF(trim(CAST(project_key AS VARCHAR)), '')) AS normalized_project_key,
        lower(trim(CAST(product_type AS VARCHAR))) AS normalized_product_type,
        lower(trim(CAST(product_key AS VARCHAR))) AS normalized_product_key,
        lower(trim(CAST(instance_name AS VARCHAR))) AS instance_name_norm,
        lower(NULLIF(trim(CAST(project_key AS VARCHAR)), '')) AS project_key_norm,
        lower(trim(CAST(product_type AS VARCHAR))) AS product_type_norm,
        lower(trim(CAST(product_key AS VARCHAR))) AS product_key_norm
    FROM final_build_products_catalog
    WHERE product_type IN ({catalog_type_sql})
      AND product_key IS NOT NULL
      AND length(trim(CAST(product_key AS VARCHAR))) > 0
),
catalog_fallback_keys AS (
    SELECT
        instance_name_norm,
        product_type_norm,
        product_key_norm,
        MIN(product_id) AS product_id,
        COUNT(DISTINCT product_id) AS candidate_count
    FROM catalog
    GROUP BY 1, 2, 3
),
eligible_events AS (
    SELECT
        e.*
    FROM v_object_activity_events e
    WHERE {' AND '.join(eligible_event_filters)}
),
matched_events AS (
    SELECT
        e.timestamp,
        e.instance_name,
        e.project_key,
        e.object_type,
        e.object_key,
        e.login,
        exact.product_id AS exact_product_id,
        fallback.product_id AS fallback_product_id,
        COALESCE(exact.product_id, fallback.product_id) AS matched_product_id,
        CASE
            WHEN exact.product_id IS NOT NULL THEN 'exact'
            WHEN fallback.product_id IS NOT NULL THEN 'fallback'
            ELSE 'unmatched'
        END AS match_type
    FROM eligible_events e
    LEFT JOIN catalog exact
      ON exact.instance_name_norm = lower(trim(CAST(e.instance_name AS VARCHAR)))
     AND exact.project_key_norm IS NOT DISTINCT FROM lower(NULLIF(trim(CAST(e.project_key AS VARCHAR)), ''))
     AND exact.product_type_norm = lower(trim(CAST(e.object_type AS VARCHAR)))
     AND exact.product_key_norm = lower(trim(CAST(e.object_key AS VARCHAR)))
    LEFT JOIN catalog_fallback_keys fallback
      ON fallback.instance_name_norm = lower(trim(CAST(e.instance_name AS VARCHAR)))
     AND fallback.product_type_norm = lower(trim(CAST(e.object_type AS VARCHAR)))
     AND fallback.product_key_norm = lower(trim(CAST(e.object_key AS VARCHAR)))
     AND fallback.candidate_count = 1
     AND exact.product_id IS NULL
)
    """.strip()  # nosec B608

    matched_where_clauses: list[str] = [" AND e.matched_product_id IS NOT NULL"]
    matched_where_params: list[Any] = []

    if q:
        matched_where_clauses.append(
            " AND (lower(COALESCE(c.product_name, e.object_key)) LIKE ? OR lower(COALESCE(e.object_key, '')) LIKE ?)"
        )
        qq = f"%{q.lower()}%"
        matched_where_params.extend([qq, qq])

    if owner:
        matched_where_clauses.append(" AND c.owner_login = ?")
        matched_where_params.append(owner)

    if instances:
        matched_where_clauses.append(" AND e.instance_name IN (" + ", ".join(["?"] * len(instances)) + ")")
        matched_where_params.extend(instances)

    if projects:
        matched_where_clauses.append(" AND e.project_key IN (" + ", ".join(["?"] * len(projects)) + ")")
        matched_where_params.extend(projects)

    matched_where_sql = "".join(matched_where_clauses)
    return ConsumptionProductQueryContext(
        matched_events_cte=matching_ctes_sql,
        params=params,
        days=days_val,
        filters={
            "days": days_val,
            "q": q,
            "instances": instances,
            "projects": projects,
            "types": object_types,
            "owner": owner,
        },
        matched_where_sql=matched_where_sql,
        matched_where_params=matched_where_params,
    )


def _parse_consumption_product_summary_filters() -> dict[str, Any]:
    return _consumption_product_filters_from_request(default_days=30)


def _query_consumption_product_totals(query_df, context: ConsumptionProductQueryContext) -> dict[str, Any]:
    sql = _consumption_product_summary_sql(
        context,
        f"""
SELECT
  COUNT(*) AS events,
  COUNT(DISTINCT e.login) AS activeUsers,
  COUNT(DISTINCT e.matched_product_id) AS activeProducts
FROM matched_events e
LEFT JOIN catalog c ON c.product_id = e.matched_product_id
WHERE 1=1{context.matched_where_sql};
        """,
    ).strip()  # nosec B608
    df = query_df(sql, [*context.params, *context.matched_where_params])
    return _df_records(df)[0] if len(df.index) else {}


def _query_consumption_product_product_rollups(query_df, context: ConsumptionProductQueryContext) -> dict[str, Any]:
    sql = f"""
WITH {context.matched_events_cte},
product_stats AS (
  SELECT
    concat_ws('|', e.instance_name, COALESCE(e.project_key, ''), e.object_type, e.object_key) AS product_id,
    COUNT(*) AS events,
    COUNT(DISTINCT e.login) AS active_users
  FROM eligible_events e
  WHERE 1=1
  GROUP BY 1
),
product_rollup AS (
  SELECT
    avg(active_users) AS avg_users_per_product,
    COUNT(*) FILTER (WHERE active_users >= 2) AS collaborative_products,
    COUNT(*) FILTER (WHERE active_users = 1) AS single_user_products,
    COUNT(*) FILTER (WHERE active_users >= 2 AND events < 5) AS multi_user_light_products,
    COUNT(*) FILTER (WHERE events >= 5) AS repeat_products,
    COUNT(*) FILTER (WHERE active_users >= 2 AND events >= 5) AS adopted_products,
    MAX(events) AS top_product_events
  FROM product_stats
),
product_concentration AS (
  SELECT
    COALESCE(SUM(events) FILTER (WHERE product_rank = 1), 0) AS top1_product_events,
    COALESCE(SUM(events) FILTER (WHERE product_rank <= 5), 0) AS top5_product_events
  FROM (
    SELECT
      events,
      ROW_NUMBER() OVER (ORDER BY events DESC, product_id) AS product_rank
    FROM product_stats
  ) ranked_products
)
SELECT
  p.avg_users_per_product,
  p.collaborative_products,
  p.single_user_products,
  p.multi_user_light_products,
  p.repeat_products,
  p.adopted_products,
  p.top_product_events,
  pc.top1_product_events,
  pc.top5_product_events
FROM product_rollup p
CROSS JOIN product_concentration pc;
    """.strip()  # nosec B608
    df = query_df(sql, [*context.params, *context.matched_where_params])
    row = _df_records(df)[0] if len(df.index) else {}
    return {
        "avgUsersPerProduct": float(row.get("avg_users_per_product") or 0.0),
        "collaborativeProducts": int(row.get("collaborative_products") or 0),
        "singleUserProducts": int(row.get("single_user_products") or 0),
        "multiUserLightProducts": int(row.get("multi_user_light_products") or 0),
        "repeatProducts": int(row.get("repeat_products") or 0),
        "adoptedProducts": int(row.get("adopted_products") or 0),
        "topProductEvents": int(row.get("top_product_events") or 0),
        "top1ProductEvents": int(row.get("top1_product_events") or 0),
        "top5ProductEvents": int(row.get("top5_product_events") or 0),
    }


def _query_consumption_product_user_rollups(query_df, context: ConsumptionProductQueryContext) -> dict[str, Any]:
    sql = f"""
WITH {context.matched_events_cte},
user_product_stats AS (
  SELECT
    e.login,
    COUNT(*) AS events,
    COUNT(DISTINCT concat_ws('|', e.instance_name, COALESCE(e.project_key, ''), e.object_type, e.object_key)) AS active_products
  FROM eligible_events e
  WHERE 1=1
  GROUP BY 1
),
user_rollup AS (
  SELECT
    avg(active_products) AS avg_products_per_user,
    MAX(active_products) AS top_user_products
  FROM user_product_stats
),
user_concentration AS (
  SELECT
    COALESCE(SUM(events) FILTER (WHERE user_rank = 1), 0) AS top1_user_events,
    COALESCE(SUM(events) FILTER (WHERE user_rank <= 5), 0) AS top5_user_events
  FROM (
    SELECT
      events,
      ROW_NUMBER() OVER (ORDER BY events DESC, login) AS user_rank
    FROM user_product_stats
  ) ranked_users
)
SELECT
  u.avg_products_per_user,
  u.top_user_products,
  uc.top1_user_events,
  uc.top5_user_events
FROM user_rollup u
CROSS JOIN user_concentration uc;
    """.strip()  # nosec B608
    df = query_df(sql, [*context.params, *context.matched_where_params])
    row = _df_records(df)[0] if len(df.index) else {}
    return {
        "avgProductsPerUser": float(row.get("avg_products_per_user") or 0.0),
        "topUserProducts": int(row.get("top_user_products") or 0),
        "top1UserEvents": int(row.get("top1_user_events") or 0),
        "top5UserEvents": int(row.get("top5_user_events") or 0),
    }


def _calculate_consumption_product_maturity(
    totals: dict[str, Any],
    product_rollups: dict[str, Any],
    user_rollups: dict[str, Any],
) -> dict[str, Any]:
    active_products = int(totals.get("activeProducts") or 0)
    active_users = int(totals.get("activeUsers") or 0)
    collaborative_products = int(product_rollups.get("collaborativeProducts") or 0)
    repeat_products = int(product_rollups.get("repeatProducts") or 0)
    adopted_products = int(product_rollups.get("adoptedProducts") or 0)
    top1_product_events = int(product_rollups.get("top1ProductEvents") or 0)
    top5_product_events = int(product_rollups.get("top5ProductEvents") or 0)
    events = int(totals.get("events") or 0)

    breadth_score = min((active_products / 25.0) * 100.0, 100.0) if active_products > 0 else 0.0
    repeat_score = min((repeat_products / max(active_products, 1)) * 100.0, 100.0) if active_products > 0 else 0.0
    collaboration_score = min((collaborative_products / max(active_products, 1)) * 100.0, 100.0) if active_products > 0 else 0.0
    concentration_score = 0.0
    if events > 0:
        top5_share = min(max(top5_product_events / max(events, 1), 0.0), 1.0)
        top1_share = min(max(top1_product_events / max(events, 1), 0.0), 1.0)
        concentration_score = max(0.0, (1.0 - ((top5_share * 0.6) + (top1_share * 0.4))) * 100.0)

    maturity_score = round((breadth_score * 0.30) + (repeat_score * 0.30) + (collaboration_score * 0.20) + (concentration_score * 0.20), 1)
    if maturity_score >= 75:
        maturity_tier = "scaled"
    elif maturity_score >= 45:
        maturity_tier = "growing"
    else:
        maturity_tier = "nascent"

    return {
        "maturityScore": maturity_score,
        "maturityTier": maturity_tier,
        "maturityComponents": {
            "breadth": round(breadth_score, 1),
            "repeat": round(repeat_score, 1),
            "collaboration": round(collaboration_score, 1),
            "concentration": round(concentration_score, 1),
            "activeUsers": active_users,
            "adoptedProducts": adopted_products,
        },
    }


def _query_consumption_product_activity_daily(query_df, context: ConsumptionProductQueryContext) -> dict[str, Any]:
    sql = _consumption_product_summary_sql(
        context,
        f"""
SELECT
  CAST(CAST(date_trunc('day', e.timestamp) AS DATE) AS VARCHAR) AS label,
  COUNT(*) AS value
FROM matched_events e
LEFT JOIN catalog c ON c.product_id = e.matched_product_id
WHERE 1=1{context.matched_where_sql}
GROUP BY 1
ORDER BY 1;
        """,
    ).strip()  # nosec B608
    df = query_df(sql, [*context.params, *context.matched_where_params])
    return {"activityDaily": _df_records(df)}


def _query_consumption_product_by_type(query_df, context: ConsumptionProductQueryContext) -> dict[str, Any]:
    supported_types_sql = ", ".join(
        "('" + product_type.replace("'", "''") + "')" for product_type in _supported_consumption_product_types()
    )
    sql = f"""
WITH {context.matched_events_cte},
supported_types(label) AS (
  VALUES {supported_types_sql}
),
product_rollup AS (
  SELECT
    e.object_type AS label,
    e.matched_product_id AS product_id,
    COUNT(*) AS events,
    COUNT(DISTINCT e.login) AS active_users
  FROM matched_events e
  LEFT JOIN catalog c ON c.product_id = e.matched_product_id
  WHERE 1=1{context.matched_where_sql}
  GROUP BY 1,2
),
type_rollup AS (
  SELECT
    label,
    COUNT(DISTINCT product_id) AS active_products,
    AVG(active_users) AS avg_users_per_product,
    MAX(active_users) AS max_users_on_product,
    COUNT(*) FILTER (WHERE active_users >= 2 AND events >= 5) AS adoption_count,
    SUM(events) AS events
  FROM product_rollup
  GROUP BY 1
),
type_event_rollup AS (
  SELECT
    e.object_type AS label,
    COUNT(*) AS events,
    COUNT(DISTINCT e.login) AS active_users
  FROM matched_events e
  LEFT JOIN catalog c ON c.product_id = e.matched_product_id
  WHERE 1=1{context.matched_where_sql}
  GROUP BY 1
),
type_maturity AS (
  SELECT
    label,
    AVG(maturity_score) AS avg_maturity_score,
    MAX(maturity_score) AS max_maturity_score
  FROM (
    SELECT
      e.object_type AS label,
      e.matched_product_id AS product_id,
      (
        LEAST((COUNT(DISTINCT e.login) / 5.0) * 100.0, 100.0) * 0.25 +
        LEAST((COUNT(*) / 5.0) * 100.0, 100.0) * 0.25 +
        CASE WHEN COUNT(DISTINCT e.login) >= 2 THEN 100.0 ELSE LEAST((COUNT(DISTINCT e.login) / 2.0) * 100.0, 100.0) END * 0.25 +
        GREATEST(0.0, 100.0 - (COUNT(*) / GREATEST(COUNT(*), 10)) * 100.0) * 0.25
      ) AS maturity_score
    FROM matched_events e
    LEFT JOIN catalog c ON c.product_id = e.matched_product_id
    WHERE 1=1{context.matched_where_sql}
    GROUP BY 1,2
  ) score_by_product
  GROUP BY 1
)
SELECT
  st.label,
  COALESCE(ter.events, 0) AS events,
  COALESCE(ter.active_users, 0) AS active_users,
  COALESCE(tr.active_products, 0) AS active_products,
  COALESCE(tr.avg_users_per_product, 0) AS avg_users_per_product,
  COALESCE(tr.max_users_on_product, 0) AS max_users_on_product,
  COALESCE(tm.avg_maturity_score, 0) AS avg_maturity_score,
  COALESCE(tm.max_maturity_score, 0) AS max_maturity_score,
  COALESCE(tr.adoption_count, 0) AS adoption_count
FROM supported_types st
LEFT JOIN type_rollup tr ON tr.label = st.label
LEFT JOIN type_event_rollup ter ON ter.label = st.label
LEFT JOIN type_maturity tm ON tm.label = st.label
ORDER BY COALESCE(tr.events, 0) DESC, st.label;
    """.strip()  # nosec B608
    df = query_df(sql, [*context.params, *context.matched_where_params, *context.matched_where_params, *context.matched_where_params])
    return {"byType": _df_records(df)}


def _query_consumption_product_top_products(query_df, context: ConsumptionProductQueryContext) -> dict[str, Any]:
    sql = f"""
WITH {context.matched_events_cte},
act AS (
  SELECT
    md5(concat_ws('|', e.instance_name, COALESCE(e.project_key, ''), e.object_type, e.object_key)) AS product_id,
    e.instance_name,
    e.project_key,
    e.object_type,
    e.object_key,
    COUNT(*) AS events,
    COUNT(DISTINCT e.login) AS active_users,
    MAX(e.timestamp) AS last_activity_at
  FROM eligible_events e
  WHERE 1=1
  GROUP BY 1,2,3,4,5
),
catalog_null_project_unique AS (
  SELECT
    instance_name,
    product_type,
    product_key,
    MIN(product_name) AS product_name,
    MIN(owner_login) AS owner_login
  FROM final_build_products_catalog
  GROUP BY 1,2,3
  HAVING COUNT(*) = 1
)
SELECT
  act.product_id AS productId,
  act.instance_name AS instanceName,
  act.project_key AS projectKey,
  act.object_type AS productType,
  act.object_key AS productKey,
  COALESCE(
    {_high_signal_product_name_sql('c.product_name')},
    CASE WHEN act.project_key IS NULL THEN {_high_signal_product_name_sql('c_null.product_name')} ELSE NULL END,
    {_non_empty_value_sql('c.product_name')},
    CASE WHEN act.project_key IS NULL THEN {_non_empty_value_sql('c_null.product_name')} ELSE NULL END,
    act.object_key
  ) AS productName,
  COALESCE(
    {_non_empty_value_sql('c.owner_login')},
    CASE WHEN act.project_key IS NULL THEN {_non_empty_value_sql('c_null.owner_login')} ELSE NULL END
  ) AS ownerLogin,
  act.events AS events,
  act.active_users AS activeUsers,
  act.last_activity_at AS lastActivityAt
FROM act
LEFT JOIN final_build_products_catalog c
  ON c.instance_name = act.instance_name
 AND c.product_key = act.object_key
 AND c.product_type = act.object_type
 AND c.project_key IS NOT DISTINCT FROM act.project_key
LEFT JOIN catalog_null_project_unique c_null
  ON act.project_key IS NULL
 AND c_null.instance_name = act.instance_name
 AND c_null.product_key = act.object_key
 AND c_null.product_type = act.object_type
ORDER BY act.events DESC NULLS LAST, act.last_activity_at DESC NULLS LAST, act.instance_name, act.project_key, act.object_type, act.object_key
LIMIT 50;
    """.strip()  # nosec B608
    df = query_df(sql, context.params)
    return {"topProducts": _df_records(df)}


def _build_consumption_product_summary_payload(
    *,
    context: ConsumptionProductQueryContext,
    totals: dict[str, Any],
    product_rollups: dict[str, Any],
    user_rollups: dict[str, Any],
    activity_daily: dict[str, Any],
    by_type: dict[str, Any],
    top_products: dict[str, Any],
    maturity: dict[str, Any],
) -> dict[str, Any]:
    return {
        "windowDays": context.days,
        "totals": {
            "events": int(totals.get("events") or 0),
            "activeUsers": int(totals.get("activeUsers") or 0),
            "activeProducts": int(totals.get("activeProducts") or 0),
            "avgUsersPerProduct": float(product_rollups.get("avgUsersPerProduct") or 0.0),
            "collaborativeProducts": int(product_rollups.get("collaborativeProducts") or 0),
            "repeatProducts": int(product_rollups.get("repeatProducts") or 0),
            "singleUserProducts": int(product_rollups.get("singleUserProducts") or 0),
            "multiUserLightProducts": int(product_rollups.get("multiUserLightProducts") or 0),
            "adoptedProducts": int(product_rollups.get("adoptedProducts") or 0),
            "topProductEvents": int(product_rollups.get("topProductEvents") or 0),
            "topUserProducts": int(user_rollups.get("topUserProducts") or 0),
            "avgProductsPerUser": float(user_rollups.get("avgProductsPerUser") or 0.0),
            "top1ProductEvents": int(product_rollups.get("top1ProductEvents") or 0),
            "top5ProductEvents": int(product_rollups.get("top5ProductEvents") or 0),
            "top1UserEvents": int(user_rollups.get("top1UserEvents") or 0),
            "top5UserEvents": int(user_rollups.get("top5UserEvents") or 0),
            "maturityScore": float(maturity.get("maturityScore") or 0.0),
            "maturityTier": maturity.get("maturityTier") or "nascent",
            "maturityComponents": maturity.get("maturityComponents") or {},
        },
        "activityDaily": activity_daily.get("activityDaily") or [],
        "byType": by_type.get("byType") or [],
        "topProducts": top_products.get("topProducts") or [],
    }


def register_routes(bp: Blueprint) -> None:
    @bp.route("/api/consumption/products/facets")
    def consumption_products_facets():
        try:
            query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
            _ensure_ready_if_enabled()

            supported_types = _supported_consumption_product_types()
            facets_df = query_df(
                """
                WITH allowed_types AS (
                  SELECT UNNEST(?::VARCHAR[]) AS object_type
                )
                SELECT
                  instance_name,
                  project_key,
                  object_type
                FROM v_object_activity_events
                WHERE object_type IN (SELECT object_type FROM allowed_types)
                  AND object_key IS NOT NULL
                  AND length(trim(CAST(object_key AS VARCHAR))) > 0;
                """.strip(),
                [supported_types],
            )

            instances = (
                facets_df["instance_name"]
                .dropna()
                .astype(str)
                .drop_duplicates()
                .sort_values()
                .tolist()
            )
            projects = (
                facets_df["project_key"]
                .dropna()
                .astype(str)
                .drop_duplicates()
                .sort_values()
                .tolist()
            )
            types = (
                facets_df["object_type"]
                .dropna()
                .astype(str)
                .drop_duplicates()
                .sort_values()
                .tolist()
            )
            owners = (
                query_df("SELECT DISTINCT owner_login FROM final_build_products_catalog ORDER BY 1;")["owner_login"]
                .dropna()
                .astype(str)
                .tolist()
            )

            return _ok({"instances": instances, "projects": projects, "types": types, "owners": owners})
        except Exception as exc:
            logger.exception("consumption products facets failed")
            return _err(str(exc), status=500)

    @bp.route("/api/consumption/products/details")
    def consumption_products_details():
        try:
            query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
            _ensure_ready_if_enabled()

            product_id = (request.args.get("productId") or "").strip()
            if not _is_md5(product_id):
                return _err("Invalid or missing productId", status=400)

            days = _parse_days_arg(default=30)

            row_df = query_df(
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
            instance_name = str(row.get("instanceName") or "")
            project_key = row.get("projectKey")
            product_type = str(row.get("productType") or "")
            product_key = str(row.get("productKey") or "")
            params: list[Any] = [days, instance_name, project_key, product_type, product_key]

            totals_df = query_df(
                """
SELECT
  COUNT(*) AS events,
  COUNT(DISTINCT login) AS active_users,
  MAX(timestamp) AS last_activity_at,
  CASE WHEN COUNT(*) >= 5 THEN 1 ELSE 0 END AS repeat_use_status,
  CASE WHEN COUNT(DISTINCT login) >= 2 THEN 1 ELSE 0 END AS collaborative_status
FROM v_object_activity_events
WHERE timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY
  AND instance_name = ?
  AND project_key IS NOT DISTINCT FROM ?
  AND object_type = ?
  AND object_key = ?;
                """.strip(),
                params,
            )
            totals = _df_records(totals_df)[0] if len(totals_df.index) else {}

            daily_df = query_df(
                """
SELECT
  CAST(CAST(date_trunc('day', timestamp) AS DATE) AS VARCHAR) AS label,
  COUNT(*) AS value
FROM v_object_activity_events
WHERE timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY
  AND instance_name = ?
  AND project_key IS NOT DISTINCT FROM ?
  AND object_type = ?
  AND object_key = ?
GROUP BY 1
ORDER BY 1;
                """.strip(),
                params,
            )

            top_users_df = query_df(
                """
SELECT
  login AS label,
  COUNT(*) AS value
FROM v_object_activity_events
WHERE timestamp >= now() - (?::INTEGER) * INTERVAL 1 DAY
  AND instance_name = ?
  AND project_key IS NOT DISTINCT FROM ?
  AND object_type = ?
  AND object_key = ?
GROUP BY 1
ORDER BY value DESC
LIMIT 25;
                """.strip(),
                params,
            )

            activity_daily_rows = []
            for record in _df_records(daily_df):
                label = record.get("label") or record.get("day")
                if label is None:
                    continue
                activity_daily_rows.append({"label": label, "value": record.get("value")})

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
        except Exception as exc:
            logger.exception("consumption products details failed")
            return _err(str(exc), status=500)

    @bp.route("/api/consumption/products/summary")
    def consumption_products_summary():
        try:
            query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
            _ensure_ready_if_enabled()
            filters = _parse_consumption_product_summary_filters()
            context = _build_consumption_product_query_context(**filters)
            totals = _query_consumption_product_totals(query_df, context)
            product_rollups = _query_consumption_product_product_rollups(query_df, context)
            user_rollups = _query_consumption_product_user_rollups(query_df, context)
            activity_daily = _query_consumption_product_activity_daily(query_df, context)
            by_type = _query_consumption_product_by_type(query_df, context)
            top_products = _query_consumption_product_top_products(query_df, context)
            maturity = _calculate_consumption_product_maturity(totals, product_rollups, user_rollups)
            payload = _build_consumption_product_summary_payload(
                context=context,
                totals=totals,
                product_rollups=product_rollups,
                user_rollups=user_rollups,
                activity_daily=activity_daily,
                by_type=by_type,
                top_products=top_products,
                maturity=maturity,
            )
            return _ok(payload)
        except Exception as exc:
            logger.exception("consumption products summary failed")
            return _err(str(exc), status=500)

    @bp.route("/api/consumption/products/lifecycle-summary")
    def consumption_products_lifecycle_summary():
        try:
            query_df, _create_connection, _ensure_database_ready = _require_duckdb_engine()
            _ensure_ready_if_enabled()
            filters = _consumption_product_filters_from_request(default_days=365)
            context = _build_consumption_product_query_context(**filters)

            sql = _consumption_product_summary_sql(
                context,
                """
SELECT
  COUNT(*) AS products_with_created_at,
  COUNT(*) FILTER (WHERE first_consumption_at IS NOT NULL) AS products_with_first_consumption,
  COUNT(*) FILTER (WHERE multi_user_at IS NOT NULL) AS products_with_multi_user,
  COUNT(*) FILTER (WHERE repeat_use_at IS NOT NULL) AS products_with_repeat_use,
  median(days_to_first_consumption) FILTER (WHERE days_to_first_consumption IS NOT NULL AND days_to_first_consumption >= 0) AS median_days_to_first_consumption,
  avg(days_to_first_consumption) FILTER (WHERE days_to_first_consumption IS NOT NULL AND days_to_first_consumption >= 0) AS avg_days_to_first_consumption,
  median(days_to_multi_user) FILTER (WHERE days_to_multi_user IS NOT NULL AND days_to_multi_user >= 0) AS median_days_to_multi_user,
  avg(days_to_multi_user) FILTER (WHERE days_to_multi_user IS NOT NULL AND days_to_multi_user >= 0) AS avg_days_to_multi_user,
  median(days_to_repeat_use) FILTER (WHERE days_to_repeat_use IS NOT NULL AND days_to_repeat_use >= 0) AS median_days_to_repeat_use,
  avg(days_to_repeat_use) FILTER (WHERE days_to_repeat_use IS NOT NULL AND days_to_repeat_use >= 0) AS avg_days_to_repeat_use
FROM (
  WITH matched_product_events AS (
    SELECT
      e.instance_name,
      e.project_key,
      e.object_type AS product_type,
      e.object_key AS product_key,
      CAST(date_trunc('day', e.timestamp) AS DATE) AS event_day,
      MIN(e.timestamp) AS first_event_at_day,
      COUNT(*) AS daily_events,
      COUNT(DISTINCT e.login) AS daily_users
    FROM eligible_events e
    GROUP BY 1, 2, 3, 4, 5
  ),
  milestones AS (
    SELECT
      mpe.*, 
      SUM(daily_events) OVER (
        PARTITION BY instance_name, project_key, product_type, product_key
        ORDER BY event_day
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
      ) AS cumulative_events
    FROM matched_product_events mpe
  ),
  users_by_product AS (
    SELECT
      mpe.instance_name,
      mpe.project_key,
      mpe.product_type,
      mpe.product_key,
      mpe.event_day,
      (
        SELECT COUNT(DISTINCT e2.login)
        FROM eligible_events e2
        WHERE e2.instance_name = mpe.instance_name
          AND e2.project_key IS NOT DISTINCT FROM mpe.project_key
          AND e2.object_type = mpe.product_type
          AND e2.object_key = mpe.product_key
          AND CAST(date_trunc('day', e2.timestamp) AS DATE) <= mpe.event_day
      ) AS cumulative_users
    FROM matched_product_events mpe
  ),
  firsts AS (
    SELECT
      COALESCE(p.product_id, md5(concat_ws('||', m.instance_name, COALESCE(m.project_key, ''), m.product_type, m.product_key))) AS product_id,
      p.created_at,
      MIN(m.first_event_at_day) AS first_consumption_at,
      MIN(CASE WHEN u.cumulative_users >= 2 THEN u.event_day END) AS multi_user_at,
      MIN(CASE WHEN m.cumulative_events >= 5 THEN m.event_day END) AS repeat_use_at
    FROM milestones m
    LEFT JOIN final_build_products_catalog p
      ON p.instance_name = m.instance_name
     AND p.project_key IS NOT DISTINCT FROM m.project_key
     AND p.product_type = m.product_type
     AND p.product_key = m.product_key
    LEFT JOIN users_by_product u
      ON u.instance_name = m.instance_name
     AND u.project_key IS NOT DISTINCT FROM m.project_key
     AND u.product_type = m.product_type
     AND u.product_key = m.product_key
     AND u.event_day = m.event_day
    WHERE m.product_type IN (SELECT DISTINCT product_type FROM catalog)
    GROUP BY 1, 2
  )
  SELECT
    *,
    datediff('day', CAST(created_at AS DATE), CAST(first_consumption_at AS DATE)) AS days_to_first_consumption,
    datediff('day', CAST(created_at AS DATE), CAST(multi_user_at AS DATE)) AS days_to_multi_user,
    datediff('day', CAST(created_at AS DATE), CAST(repeat_use_at AS DATE)) AS days_to_repeat_use
  FROM firsts
) durations;
                """,
            )
            df = query_df(sql, [*context.params, *context.matched_where_params])
            row = _df_records(df)[0] if len(df.index) else {}
            return _ok(
                {
                    "days": int(filters["days"]),
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
        except Exception as exc:
            logger.exception("consumption lifecycle summary failed")
            return _err(str(exc), status=500)
