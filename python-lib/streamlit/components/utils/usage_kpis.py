from backend.duck_db import query

def _sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def format_subcategory_counts(df, limit=5):
    rows = []
    for _, row in df.head(limit).iterrows():
        rows.append(f"{row['dataiku_category']}: {int(row['total_events']):,}")
    return " | ".join(rows)


def gather_data():
    sql = """
        SELECT
            SUM(build_events)                     AS total_build_events,
            COUNT(DISTINCT login)                 AS active_builders,
            COUNT(DISTINCT instance_name)         AS active_instances,
            SUM(build_events)::DOUBLE
              / NULLIF(COUNT(DISTINCT login), 0)  AS avg_events_per_builder
        FROM actor_usage_last_30_days_base
        WHERE login NOT LIKE 'api:%'
    ;
    """
    df = query.query_df(sql)
    row = df.iloc[0]
    return {
        "total_events": int(row["total_build_events"] or 0),
        "builders": int(row["active_builders"] or 0),
        "instances": int(row["active_instances"] or 0),
        "avg_per_builder": float(row["avg_events_per_builder"] or 0),
    }

def get_total_build_events_last_30_days(tab_scope=None):
    where_clauses = ["login NOT LIKE 'api:%'"]
    if tab_scope:
        instance_name = tab_scope.get("instance_name")
        if instance_name and instance_name != "All (General)":
            where_clauses.append(
                f"instance_name = {_sql_quote(instance_name)}"
            )
        login = tab_scope.get("login")
        if login:
            where_clauses.append(
                f"login = {_sql_quote(login)}"
            )
    where_sql = " AND ".join(where_clauses)
    sql = f"""
        SELECT
            SUM(build_events) AS total_events
        FROM actor_usage_last_30_days_base
        WHERE {where_sql}
    ;
    """
    df = query.query_df(sql)
    return int(df.iloc[0]["total_events"] or 0)


def get_subcategory_counts_last_30_days(capability, instance_name=None):
    where_clauses = [
        f"canonical_capability = {_sql_quote(capability)}"
    ]
    if instance_name and instance_name != "All (General)":
        where_clauses.append(
            f"instance_name = {_sql_quote(instance_name)}"
        )
    where_sql = " AND ".join(where_clauses)
    sql = f"""
        SELECT
            dataiku_category,
            SUM(event_count) AS total_events
        FROM capability_subcategory_usage_last_30_days_base
        WHERE {where_sql}
        GROUP BY dataiku_category
        ORDER BY total_events DESC
    ;
    """
    return query.query_df(sql)