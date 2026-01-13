META = {
    "id": "recipes.total_recipes_per_instance",
    "version": 1,
    "label": "Total Recipes",
    "description": "Total number of recipes per instance",
    "type": "metric",
    "order": 10,
    "value_column": "total_recipes",
    "groupby": ["instance_name"],
}

def query():
    return """
        SELECT
            instance_name,
            COUNT(*) AS total_recipes
        FROM recipes_metadata_base
        GROUP BY instance_name
        ORDER BY instance_name
    ;
    """
