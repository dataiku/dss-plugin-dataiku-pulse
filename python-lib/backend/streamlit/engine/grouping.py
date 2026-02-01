def apply_grouping(sql, meta, filters):
    if not filters.get("group_by"):
        return sql

    group_cols = filters["group_by"]

    # naive example, safe version would parse SQL
    return sql.replace(
        "GROUP BY month",
        f"GROUP BY month, {', '.join(group_cols)}"
    )
