def _sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def apply_usage_scope(sql_template: str, scope: dict) -> str:
    where_clauses = []
    capability_clause = ""
    group_by_clause = ""
    scope_expr = "'All Instances'"

    # --------------------------------------------------
    # Normalize scope (drop no-op values)
    # --------------------------------------------------
    normalized_scope = {}
    for key, value in scope.items():
        if value is None:
            continue
        if value == "All (General)":
            continue
        normalized_scope[key] = value

    # --------------------------------------------------
    # WHERE clauses (declarative, key-based)
    # --------------------------------------------------
    for key, value in normalized_scope.items():
        if key == "mode":
            continue

        if key == "capability":
            capability_clause = (
                f"AND canonical_capability = {_sql_quote(value)}"
            )
            continue

        # Only allow known scope filters
        if key in ("login", "instance_name"):
            where_clauses.append(f"{key} = {_sql_quote(value)}")

    where_clause = ""
    if where_clauses:
        where_clause = " AND " + " AND ".join(where_clauses)

    # --------------------------------------------------
    # 2. GROUP BY + scope label (mode-driven only)
    # --------------------------------------------------
    mode = scope.get("mode")

    if mode == "actor":
        scope_expr = "login"
        group_by_clause = "GROUP BY login"

    elif mode == "instance":
        scope_expr = "instance_name"
        group_by_clause = "GROUP BY instance_name"

    elif mode == "overview" or mode is None:
        scope_expr = "'All Instances'"
        group_by_clause = ""

    else:
        raise ValueError(f"Unknown usage scope mode: {mode}")

    # --------------------------------------------------
    # Final SQL expansion
    # --------------------------------------------------
    return sql_template.format(
        scope_expr=scope_expr,
        where_clause=where_clause,
        group_by_clause=group_by_clause,
        capability_clause=capability_clause,
    )
