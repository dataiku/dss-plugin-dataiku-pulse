META = {
    "id": "scenarios.active_by_type_last_30_days",
    "version": 1,
    "label": "Active Scenarios by Type (30 Days)",
    "description": "Scenarios updated in the last 30 days by scenario type",
    "type": "graph",
    "tab": "activity",
    "graph": {
        "kind": "bar",
        "x": "scenarios_type",
        "y": "active_scenario_count",
        "color": "instance_name",
        "barmode": "group",
        "x_title": "Scenario Type",
        "y_title": "Active Scenarios (30 Days)",
        "legend_title": "Instance",
        "labels": {
            "scenarios_type": "Scenario Type",
            "active_scenario_count": "Active Scenarios",
            "instance_name": "Instance"
        }
    }
}

def query():
    return """
        WITH active AS (
            SELECT
                instance_name,
                scenarios_type
            FROM scenarios_metadata_base
            WHERE
                scenarios_lastModifiedOn >= CURRENT_TIMESTAMP - INTERVAL 30 DAY
        ),
        top_types AS (
            SELECT
                scenarios_type
            FROM active
            GROUP BY scenarios_type
            ORDER BY COUNT(*) DESC
            LIMIT 10
        )
        SELECT
            a.instance_name,
            a.scenarios_type,
            COUNT(*) AS active_scenario_count
        FROM active a
        JOIN top_types t
            ON a.scenarios_type = t.scenarios_type
        GROUP BY
            a.instance_name,
            a.scenarios_type
        ORDER BY
            active_scenario_count DESC,
            a.instance_name
    ;
    """
