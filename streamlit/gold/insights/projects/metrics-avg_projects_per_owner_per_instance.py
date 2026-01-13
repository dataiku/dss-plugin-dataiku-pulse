META = {
    "id": "projects.avg_projects_per_owner_per_instance",
    "version": 1,
    "label": "Avg Projects per Owner",
    "description": "Average number of projects per owner within each instance",
    "type": "metric",
    "order": 20,
    "value_column": "avg_projects_per_owner",
    "groupby": ["instance_name"],
}

def query():
    return """
        WITH projects_per_owner AS (
            SELECT
                instance_name,
                project_ownerDisplayName AS owner,
                COUNT(*) AS project_count
            FROM projects_metadata_base
            WHERE project_ownerDisplayName IS NOT NULL
            GROUP BY
                instance_name,
                owner
        )
        SELECT
            instance_name,
            AVG(project_count) AS avg_projects_per_owner
        FROM projects_per_owner
        GROUP BY instance_name
        ORDER BY instance_name
    ;
    """
