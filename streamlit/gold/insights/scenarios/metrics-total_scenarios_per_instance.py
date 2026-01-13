META = {
    "id": "scenarios.total_scenarios_per_instance",
    "version": 1,
    "label": "Total Scenarios",
    "description": "Total number of scenarios per instance",
    "type": "metric",
    "order": 10,
    "value_column": "total_scenarios",
    "groupby": ["instance_name"],
}

def query():
    return """
        SELECT
            instance_name,
            COUNT(*) AS total_scenarios
        FROM scenarios_metadata_base
        GROUP BY instance_name
        ORDER BY instance_name
    ;
    """
