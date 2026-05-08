def apply_filters(sql, meta, filters):
    sql = sql.strip().rstrip(";")

    clauses = []

    # Instance filter (most common)
    instance = filters.get("instance_name")
    if instance:
        clauses.append("instance_name = ?")

    if not clauses:
        return sql, []

    where_sql = " AND ".join(clauses)

    params = []
    if instance:
        params.append(instance)

    return (
        f"""
        SELECT *
        FROM (
            {sql}
        ) base
        WHERE {where_sql}
        """,
        params,
    )
