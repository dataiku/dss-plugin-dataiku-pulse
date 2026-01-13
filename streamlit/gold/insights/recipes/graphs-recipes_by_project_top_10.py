META = {
    "id": "recipes.by_project_top_10",
    "version": 1,
    "label": "Recipes by Project (Top 10)",
    "description": "Top 10 projects by recipe count",
    "type": "graph",
    "tab": "summary",
    "graph": {
        "kind": "bar",
        "x": "project_key",
        "y": "recipe_count",
        "color": "instance_name",
        "barmode": "group",
        "x_title": "Project",
        "y_title": "Number of Recipes",
        "legend_title": "Instance",
        "labels": {
            "project_key": "Project",
            "recipe_count": "Recipes",
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
            FROM recipes_metadata_base
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
            COUNT(*) AS recipe_count
        FROM base b
        JOIN top_projects t
            ON b.project_key = t.project_key
        GROUP BY
            b.instance_name,
            b.project_key
        ORDER BY
            recipe_count DESC,
            b.instance_name
    ;
    """
