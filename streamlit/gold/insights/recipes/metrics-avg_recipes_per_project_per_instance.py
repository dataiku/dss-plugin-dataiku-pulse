META = {
    "id": "recipes.avg_recipes_per_project_per_instance",
    "version": 1,
    "label": "Avg Recipes per Project",
    "description": "Average number of recipes per project within each instance",
    "type": "metric",
    "order": 30,
    "value_column": "avg_recipes_per_project",
    "groupby": ["instance_name"],
}

def query():
    return """
        WITH recipes_per_project AS (
            SELECT
                instance_name,
                project_key,
                COUNT(*) AS recipe_count
            FROM recipes_metadata_base
            GROUP BY
                instance_name,
                project_key
        )
        SELECT
            instance_name,
            AVG(recipe_count) AS avg_recipes_per_project
        FROM recipes_per_project
        GROUP BY instance_name
        ORDER BY instance_name
    ;
    """
