META = {
    "id": "projects.by_app_type_top_10",
    "version": 1,
    "label": "Projects by Application Type (Top 10)",
    "description": "Top 10 project application types across instances",
    "type": "graph",
    "tab": "summary",
    "graph": {
        "kind": "bar",
        "x": "project_app_type",
        "y": "project_count",
        "color": "instance_name",
        "barmode": "group",
        "x_title": "Application Type",
        "y_title": "Number of Projects",
        "legend_title": "Instance",
        "labels": {
            "project_app_type": "Application Type",
            "project_count": "Projects",
            "instance_name": "Instance"
        }
    }
}

def query():
    return """
        WITH base AS (
            SELECT
                instance_name,
                project_projectAppType AS project_app_type
            FROM projects_metadata_base
            WHERE project_projectAppType IS NOT NULL
        ),
        top_types AS (
            SELECT
                project_app_type
            FROM base
            GROUP BY project_app_type
            ORDER BY COUNT(*) DESC
            LIMIT 10
        )
        SELECT
            b.instance_name,
            b.project_app_type,
            COUNT(*) AS project_count
        FROM base b
        JOIN top_types t
            ON b.project_app_type = t.project_app_type
        GROUP BY
            b.instance_name,
            b.project_app_type
        ORDER BY
            project_count DESC,
            b.instance_name
    ;
    """
