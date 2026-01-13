META = {
    "id": "scenarios.avg_scenarios_per_project_per_instance",
    "version": 1,
    "label": "Avg Scenarios per Project",
    "description": "Average number of scenarios per project within each instance",
    "type": "metric",
    "order": 30,
    "value_column": "avg_scenarios_per_project",
    "groupby": ["instance_name"],
}

def query():
    return """
        WITH scenarios_per_project AS (
            SELECT
                instance_name,
                project_key,
                COUNT(*) AS scenario_count
            FROM scenarios_metadata_base
            GROUP BY
                instance_name,
                project_key
        )
        SELECT
            instance_name,
            AVG(scenario_count) AS avg_scenarios_per_project
        FROM scenarios_per_project
        GROUP BY instance_name
        ORDER BY instance_name
    ;
    """
