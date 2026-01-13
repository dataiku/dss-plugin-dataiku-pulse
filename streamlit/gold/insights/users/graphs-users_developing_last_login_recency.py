META = {
    "id": "users.developing_last_login_recency",
    "version": 1,
    "label": "Developing Users — Last Login Recency",
    "description": "Recency of last login for users performing development activities",
    "type": "graph",
    "tab": "activity",
    "graph": {
        "kind": "bar",
        "x": "days_since_last_login",
        "y": "user_count",
        "color": "instance_name",
        "barmode": "group",
        "x_title": "Days Since Last Login",
        "y_title": "Users",
        "legend_title": "Instance",
        "labels": {
            "days_since_last_login": "Days Since Last Login",
            "user_count": "Users",
            "instance_name": "Instance"
        }
    }
}

def query():
    return """
        WITH last_login AS (
            SELECT
                instance_name,
                login,
                MAX(timestamp) AS last_login_ts
            FROM users_user_login_activity_base
            WHERE activity_type = 'DEVELOPER'
            GROUP BY
                instance_name,
                login
        ),
        recency AS (
            SELECT
                instance_name,
                login,
                DATE_DIFF('day', last_login_ts, CURRENT_TIMESTAMP) AS days_since_last_login
            FROM last_login
        ),
        top_buckets AS (
            SELECT
                days_since_last_login
            FROM recency
            GROUP BY days_since_last_login
            ORDER BY COUNT(*) DESC
            LIMIT 10
        )
        SELECT
            r.instance_name,
            r.days_since_last_login,
            COUNT(*) AS user_count
        FROM recency r
        JOIN top_buckets t
            ON r.days_since_last_login = t.days_since_last_login
        GROUP BY
            r.instance_name,
            r.days_since_last_login
        ORDER BY
            r.days_since_last_login,
            r.instance_name
    ;
    """
