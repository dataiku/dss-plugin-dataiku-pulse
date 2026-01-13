META = {
    "id": "recipes.by_engine_top_10",
    "version": 1,
    "label": "Recipes by Engine (Top 10)",
    "description": "Top 10 recipe engines across instances",
    "type": "graph",
    "tab": "summary",
    "graph": {
        "kind": "bar",
        "x": "recipes_params_engineType",
        "y": "recipe_count",
        "color": "instance_name",
        "barmode": "group",
        "x_title": "Recipe Engine",
        "y_title": "Number of Recipes",
        "legend_title": "Instance",
        "labels": {
            "recipes_params_engineType": "Recipe Engine",
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
                recipes_params_engineType
            FROM recipes_metadata_base
            WHERE recipes_params_engineType IS NOT NULL
        ),
        top_engines AS (
            SELECT
                recipes_params_engineType
            FROM base
            GROUP BY recipes_params_engineType
            ORDER BY COUNT(*) DESC
            LIMIT 10
        )
        SELECT
            b.instance_name,
            b.recipes_params_engineType,
            COUNT(*) AS recipe_count
        FROM base b
        JOIN top_engines t
            ON b.recipes_params_engineType = t.recipes_params_engineType
        GROUP BY
            b.instance_name,
            b.recipes_params_engineType
        ORDER BY
            recipe_count DESC,
            b.instance_name
    ;
    """
