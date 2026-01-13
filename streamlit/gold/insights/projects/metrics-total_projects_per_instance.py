META = {
    "id": "projects.total_projects_per_instance",
    "version": 1,
    "label": "Total Projects",
    "description": "Total number of projects per instance",
    "type": "metric",
    "order": 10,
    "value_column": "total_projects",
    "groupby": ["instance_name"],
}

def query():
    return """
        SELECT
            instance_name,
            COUNT(*) AS total_projects
        FROM projects_metadata_base
        GROUP BY instance_name
        ORDER BY instance_name
    ;
    """
