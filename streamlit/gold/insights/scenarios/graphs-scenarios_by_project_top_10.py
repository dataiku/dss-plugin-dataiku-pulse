META = {
    "id": "scenarios.by_project_top_10",
    "version": 1,
    "label": "Scenarios by Project (Top 10)",
    "description": "Top 10 projects by scenario count",
    "type": "graph",
    "tab": "summary",
    "graph": {
        "kind": "bar",
        "x": "project_key",
        "y": "scenario_count",
        "color": "instance_name",
        "barmode": "group",
        "x_title": "Project",
        "y_title": "Number of Scenarios",
        "legend_title": "Instance",
        "labels": {
            "project_key": "Project",
            "scenario_count": "Scenarios",
            "instance_name": "Instance"
        }
    }
}

def query():
    return """
        WITH base AS (
            SELECT
                instance_name,
                project_key
            FROM scenarios_metadata_base
        ),
        top_projects AS (
            SELECT
                project_key
            FROM base
            GROUP BY project_key
            ORDER BY COUNT(*) DESC
            LIMIT 10
        )
        SELECT
            b.instance_name,
            b.project_key,
            COUNT(*) AS scenario_count
        FROM base b
        JOIN top_projects t
            ON b.project_key = t.project_key
        GROUP BY
            b.instance_name,
            b.project_key
        ORDER BY
            scenario_count DESC,
            b.instance_name
    ;
    """
