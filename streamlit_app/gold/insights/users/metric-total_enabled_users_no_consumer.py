META = {
    "id": "users.total_enabled_users_per_instance_no_consumer",
    "version": 1,
    "label": "Total Enabled Users, No Consumer license(s)",
    "description": "Total number of enabled users per instance, No Consumer license(s)",
    "type": "metric",
    "order": 20,
    "value_column": "total_users",
    "groupby": ["instance_name"],
}

def query():
    return """
        SELECT
            instance_name,
            COUNT(*) AS total_users
        FROM users_metadata_base
        WHERE
            enabled IS TRUE
            AND userProfile NOT IN (
                'READER',
                'AI_CONSUMER'
            )
        GROUP BY instance_name
        ORDER BY instance_name
    ;"""
