META = {
    "id": "users.active_by_profile_last_30_days",
    "version": 1,
    "label": "Active Users by Profile (30 Days)",
    "description": "Top user profiles by active users in the last 30 days",
    "type": "graph",
    "tab": "license",
    "graph": {
        "kind": "bar",
        "x": "user_profile",
        "y": "active_user_count",
        "color": "instance_name",
        "barmode": "group",
        "x_title": "User Profile",
        "y_title": "Active Users (30 Days)",
        "legend_title": "Instance",
        "labels": {
            "user_profile": "User Profile",
            "active_user_count": "Active Users",
            "instance_name": "Instance"
        }
    }
}

def query():
    return """
        WITH active AS (
            SELECT
                instance_name,
                userProfile AS user_profile
            FROM users_metadata_base
            WHERE
                last_activity_ts >= CURRENT_TIMESTAMP - INTERVAL 30 DAY
        ),
        top_profiles AS (
            SELECT
                user_profile
            FROM active
            GROUP BY user_profile
            ORDER BY COUNT(*) DESC
            LIMIT 10
        )
        SELECT
            a.instance_name,
            a.user_profile,
            COUNT(*) AS active_user_count
        FROM active a
        JOIN top_profiles t
            ON a.user_profile = t.user_profile
        GROUP BY
            a.instance_name,
            a.user_profile
        ORDER BY
            active_user_count DESC,
            a.instance_name
    ;
    """
