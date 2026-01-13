META = {
    "id": "users.licenses_used_by_profile",
    "version": 1,
    "label": "Licenses Used by Profile",
    "description": "Count of enabled user licenses by profile type and instance",
    "type": "graph",
    "tab": "license",
    "graph": {
        "kind": "bar",
        "x": "user_profile",
        "y": "license_count",
        "color": "instance_name",
        "barmode": "group",
        "x_title": "License Type",
        "y_title": "Licenses Used",
        "legend_title": "Instance",
        "labels": {
            "user_profile": "License Type",
            "license_count": "Licenses Used",
            "instance_name": "Instance"
        }
    }
}

def query():
    return """
        SELECT
            instance_name,
            userProfile AS user_profile,
            COUNT(*) AS license_count
        FROM users_metadata_base
        WHERE enabled IS TRUE
        GROUP BY
            instance_name,
            user_profile
        ORDER BY
            user_profile,
            license_count DESC
    ;
    """
