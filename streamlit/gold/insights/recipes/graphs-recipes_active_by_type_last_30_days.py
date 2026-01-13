META = {
    "id": "recipes.active_by_type_last_30_days",
    "version": 1,
    "label": "Active Recipes by Type (30 Days)",
    "description": "Recipes updated in the last 30 days by recipe type",
    "type": "graph",
    "tab": "activity",
    "graph": {
        "kind": "bar",
        "x": "recipes_type",
        "y": "active_recipe_count",
        "color": "instance_name",
        "barmode": "group",
        "x_title": "Recipes Type",
        "y_title": "Active Recipes (30 Days)",
        "legend_title": "Instance",
        "labels": {
            "recipes_type": "Recipes Type",
            "active_recipe_count": "Active Recipes",
            "instance_name": "Instance"
        }
    }
}

def query():
    return """
        WITH active AS (
            SELECT
                instance_name,
                recipes_type
            FROM recipes_metadata_base
            WHERE
                recipes_versionTag_lastModifiedOn >= CURRENT_TIMESTAMP - INTERVAL 30 DAY
        ),
        top_types AS (
            SELECT
                recipes_type
            FROM active
            GROUP BY recipes_type
            ORDER BY COUNT(*) DESC
            LIMIT 10
        )
        SELECT
            a.instance_name,
            a.recipes_type,
            COUNT(*) AS active_recipe_count
        FROM active a
        JOIN top_types t
            ON a.recipes_type = t.recipes_type
        GROUP BY
            a.instance_name,
            a.recipes_type
        ORDER BY
            active_recipe_count DESC,
            a.instance_name
    ;
    """
