META = {
    "id": "projects.by_owner_top_10",
    "version": 1,
    "label": "Projects by Owner (Top 10)",
    "description": "Top 10 project owners by number of projects",
    "type": "graph",
    "tab": "ownership",
    "graph": {
        "kind": "bar",
        "x": "project_owner",
        "y": "project_count",
        "color": "instance_name",
        "barmode": "group",
        "x_title": "Project Owner",
        "y_title": "Number of Projects",
        "legend_title": "Instance",
        "labels": {
            "project_owner": "Owner",
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
                project_ownerDisplayName AS project_owner
            FROM projects_metadata_base
            WHERE project_ownerDisplayName IS NOT NULL
        ),
        top_owners AS (
            SELECT
                project_owner
            FROM base
            GROUP BY project_owner
            ORDER BY COUNT(*) DESC
            LIMIT 10
        )
        SELECT
            b.instance_name,
            b.project_owner,
            COUNT(*) AS project_count
        FROM base b
        JOIN top_owners t
            ON b.project_owner = t.project_owner
        GROUP BY
            b.instance_name,
            b.project_owner
        ORDER BY
            project_count DESC,
            b.instance_name
    ;
    """
