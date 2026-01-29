META = {
    "id": "recipes.active_recipes_last_30_days_per_instance",
    "version": 1,
    "label": "Active Recipes (30 Days)",
    "description": "Number of recipes modified in the last 30 days per instance",
    "type": "metric",
    "order": 20,
    "value_column": "active_recipes_30_days",
    "groupby": ["instance_name"],
}

def query():
    return """
        SELECT
            instance_name,
            COUNT(DISTINCT recipes_type) AS active_recipes_30_days
        FROM recipes_metadata_base
        WHERE
            recipes_versionTag_lastModifiedOn >= CURRENT_TIMESTAMP - INTERVAL 30 DAY
        GROUP BY instance_name
        ORDER BY instance_name
    ;
    """
