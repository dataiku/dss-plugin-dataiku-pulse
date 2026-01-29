def apply_filters(sql, meta, filters):
    sql = sql.strip().rstrip(";")
    
    clauses = []

    # Instance filter (most common)
    instance = filters.get("instance_name")
    if instance:
        clauses.append(f"instance_name = '{instance}'")

    if not clauses:
        return sql

    where_sql = " AND ".join(clauses)

    return f"""
    SELECT *
    FROM (
        {sql}
    ) base
    WHERE {where_sql}
    """
