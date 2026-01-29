META = {
    "id": "users.total_enabled_users_per_instance",
    "version": 1,
    "label": "Total Enabled Users",
    "description": "Total number of enabled users per instance",
    "type": "metric",
    "order": 10,
    "value_column": "total_users",
    "groupby": ["instance_name"],
}

def query():
    return """
        SELECT
            instance_name,
            COUNT(*) AS total_users
        FROM users_metadata_base
        WHERE enabled IS TRUE
        GROUP BY instance_name
        ORDER BY instance_name
    ;"""
