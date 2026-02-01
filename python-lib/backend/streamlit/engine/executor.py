import logging
import json
import streamlit as st

from backend.duck_db import create_conn
from backend.duck_db import sql_logger
from backend.duck_db import query
from backend.streamlit.engine.filters import apply_filters
from backend.streamlit.engine.grouping import apply_grouping
from backend.streamlit.engine.usage_scope import apply_usage_scope

logger = logging.getLogger(__name__)

# Execute Analytics query & caches data
@st.cache_data(
    show_spinner=False,
    ttl=120, # 2 minutes (tune this)
    max_entries=256, # prevents unbounded growth
)
def _execute_analytic_cached(analytic_id: str, sql: str, filters_key: str):
    return query.query_df(sql)

def execute_analytic(analytic, filters=None, scope=None):
    filters = filters or {}
    scope = scope or {}
    
    meta = analytic["meta"]
    sql = analytic["module"].query()

    if scope and meta.get("usage_scoped"):
        sql = apply_usage_scope(sql, scope)

    if filters:
        sql = apply_filters(sql, meta, filters)
        #sql = apply_grouping(sql, meta, filters)

    # Query intent logging (must NOT affect execution)
    log_conn = None
    try:
        log_conn = create_conn.create_connection(read_only=False)
        normalized = sql_logger.normalize_sql(sql)
        sql_logger.log_query(
            conn=log_conn,
            sql_text=sql,
            normalized_sql=normalized,
            page = meta.get("id").split(".")[0].lower()
        )
    except Exception:
        logger.debug("Query logging failed", exc_info=True)
    finally:
        if log_conn is not None:
            log_conn.close()

    filters_key = json.dumps(
        {
            "filters": filters,
            "scope": scope,
        },
        sort_keys=True
    )

    # Execute analytic (cached)
    df = _execute_analytic_cached(
        meta["id"],
        sql,
        filters_key
    )
    return {
        "type": meta.get("type"),
        "meta": meta,
        "df": df,
    }
