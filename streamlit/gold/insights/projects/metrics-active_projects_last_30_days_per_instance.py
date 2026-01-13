META = {
    "id": "projects.active_projects_last_30_days_per_instance",
    "version": 1,
    "label": "Active Projects (30 Days)",
    "description": "Number of projects modified in the last 30 days per instance",
    "type": "metric",
    "order": 30,
    "value_column": "active_projects_30_days",
    "groupby": ["instance_name"],
}

def query():
    return """
        SELECT
            instance_name,
            COUNT(DISTINCT project_key) AS active_projects_30_days
        FROM projects_metadata_base
        WHERE
            project_versionTag_lastModifiedOn >= CURRENT_TIMESTAMP - INTERVAL 30 DAY
        GROUP BY instance_name
        ORDER BY instance_name
    ;
    """
