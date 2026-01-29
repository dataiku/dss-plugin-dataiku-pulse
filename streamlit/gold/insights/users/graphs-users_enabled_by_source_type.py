META = {
    "id": "users.enabled_by_source_type",
    "version": 1,
    "label": "Enabled Users by Source Type",
    "description": "Distribution of enabled users by authentication source",
    "type": "graph",
    "tab": "summary",
    "graph": {
        "kind": "bar",
        "x": "source_type",
        "y": "enabled_user_count",
        "color": "instance_name",
        "barmode": "group",
        "x_title": "Source Type",
        "y_title": "Enabled Users",
        "legend_title": "Instance",
        "labels": {
            "source_type": "Source Type",
            "enabled_user_count": "Enabled Users",
            "instance_name": "Instance"
        }
    }
}

def query():
    return """
        WITH enabled AS (
            SELECT
                instance_name,
                sourceType AS source_type
            FROM users_metadata_base
            WHERE enabled IS TRUE
        ),
        top_sources AS (
            SELECT
                source_type
            FROM enabled
            GROUP BY source_type
            ORDER BY COUNT(*) DESC
            LIMIT 10
        )
        SELECT
            e.instance_name,
            e.source_type,
            COUNT(*) AS enabled_user_count
        FROM enabled e
        JOIN top_sources t
            ON e.source_type = t.source_type
        GROUP BY
            e.instance_name,
            e.source_type
        ORDER BY
            enabled_user_count DESC,
            e.instance_name
    ;
    """
