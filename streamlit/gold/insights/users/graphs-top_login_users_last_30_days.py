META = {
    "id": "users.top_login_users_last_30_days",
    "version": 1,
    "label": "Top Login Users (Last 30 Days)",
    "description": (
        "Top users by number of login days in the last 30 days, "
        "stacked by instance."
    ),
    "type": "graph",
    "tab": "summary",
    "order": 35,
    "graph": {
        "kind": "bar",
        "x": "login",
        "y": "login_days",
        "color": "instance_name",
        "barmode": "stack",
        "texttemplate": (
            "%{y} days<br>"
            "%{color}"
        ),
        "textposition": "inside",
        "x_title": "User Login",
        "y_title": "Login Days (Last 30 Days)",
    },
}


def query():
    return """
        WITH recent_logins AS (
            SELECT
                instance_name,
                login,
                COUNT(*) AS login_days
            FROM users_user_login_activity_base
            WHERE
                timestamp >= CURRENT_DATE - INTERVAL 30 DAY
            GROUP BY
                instance_name,
                login
        ),
        ranked_users AS (
            SELECT
                instance_name,
                login,
                login_days,
                ROW_NUMBER() OVER (
                    PARTITION BY instance_name
                    ORDER BY login_days DESC
                ) AS rn
            FROM recent_logins
        )
        SELECT
            instance_name,
            login,
            login_days
        FROM ranked_users
        WHERE rn <= 10
        ORDER BY
            login_days DESC,
            instance_name
    ;
    """
