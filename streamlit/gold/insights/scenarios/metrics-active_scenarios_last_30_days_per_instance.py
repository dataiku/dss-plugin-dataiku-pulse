META = {
    "id": "scenarios.active_scenarios_last_30_days_per_instance",
    "version": 1,
    "label": "Active Scenarios (30 Days)",
    "description": "Number of scenarios modified in the last 30 days per instance",
    "type": "metric",
    "order": 20,
    "value_column": "active_scenarios_30_days",
    "groupby": ["instance_name"],
}

def query():
    return """
        SELECT
            instance_name,
            COUNT(DISTINCT CASE
                WHEN scenarios_lastModifiedOn >= CURRENT_TIMESTAMP - INTERVAL 30 DAY
                THEN scenarios_id
            END) AS active_scenarios_30_days
        FROM scenarios_metadata_base
        GROUP BY instance_name
        ORDER BY instance_name
    ;
    """
