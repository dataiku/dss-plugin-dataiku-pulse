META = {
    "id": "users.new_users_per_month",
    "version": 1,
    "label": "New Users per Month",
    "description": "Monthly count of newly created users by instance",
    "type": "graph",
    "tab": "activity",
    "graph": {
        "kind": "line",
        "x": "month",
        "y": "new_user_count",
        "color": "instance_name",
        "x_title": "Month",
        "y_title": "New Users",
        "legend_title": "Instance",
        "labels": {
            "month": "Month",
            "new_user_count": "New Users",
            "instance_name": "Instance"
        }
    }
}

def query():
    return """
        SELECT
            instance_name,
            DATE_TRUNC('month', creationDate) AS month,
            COUNT(*) AS new_user_count
        FROM users_metadata_base
        WHERE creationDate IS NOT NULL
        GROUP BY
            instance_name,
            month
        ORDER BY
            month,
            instance_name
    ;
    """
