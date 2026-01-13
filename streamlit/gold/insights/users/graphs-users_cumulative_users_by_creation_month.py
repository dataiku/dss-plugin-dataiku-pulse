META = {
    "id": "users.cumulative_users_by_creation_month",
    "version": 1,
    "label": "Cumulative Users by Creation Month",
    "description": "Cumulative count of users created over time by instance",
    "type": "graph",
    "tab": "activity",
    "graph": {
        "kind": "line",
        "x": "month",
        "y": "cumulative_user_count",
        "color": "instance_name",
        "x_title": "Month",
        "y_title": "Cumulative Users",
        "legend_title": "Instance",
        "labels": {
            "month": "Month",
            "cumulative_user_count": "Cumulative Users",
            "instance_name": "Instance"
        }
    }
}

def query():
    return """
        WITH monthly_users AS (
            SELECT
                instance_name,
                DATE_TRUNC('month', creationDate) AS month,
                COUNT(*) AS new_user_count
            FROM users_metadata_base
            WHERE creationDate IS NOT NULL
            GROUP BY
                instance_name,
                month
        )
        SELECT
            instance_name,
            month,
            SUM(new_user_count) OVER (
                PARTITION BY instance_name
                ORDER BY month
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) AS cumulative_user_count
        FROM monthly_users
        ORDER BY
            month,
            instance_name
    ;
    """
