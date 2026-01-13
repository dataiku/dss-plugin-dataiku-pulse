META = {
    "id": "projects.active_last_30_days_top_10",
    "version": 1,
    "label": "Most Active Projects (Last 30 Days)",
    "description": "Top 10 most actively modified projects in the last 30 days",
    "type": "graph",
    "tab": "activity",
    "graph": {
        "kind": "bar",
        "x": "project_key",
        "y": "activity_count",
        "color": "instance_name",
        "barmode": "group",
        "x_title": "Project Key",
        "y_title": "Activity Count",
        "legend_title": "Instance",
        "labels": {
            "project_key": "Project",
            "activity_count": "Activity",
            "instance_name": "Instance"
        }
    }
}

def query():
    return """
        WITH recent AS (
            SELECT
                instance_name,
                project_key
            FROM projects_metadata_base
            WHERE
                project_versionTag_lastModifiedOn >= CURRENT_TIMESTAMP - INTERVAL 30 DAY
        ),
        top_projects AS (
            SELECT
                project_key
            FROM recent
            GROUP BY project_key
            ORDER BY COUNT(*) DESC
            LIMIT 10
        )
        SELECT
            r.instance_name,
            r.project_key,
            COUNT(*) AS activity_count
        FROM recent r
        JOIN top_projects t
            ON r.project_key = t.project_key
        GROUP BY
            r.instance_name,
            r.project_key
        ORDER BY
            activity_count DESC,
            r.instance_name
    ;
    """
