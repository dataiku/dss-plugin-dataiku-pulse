META = {
    "id": "development.capability_mix_last_30_days",
    "version": 5,
    "label": "Development Activity Mix (Last 30 Days)",
    "description": (
        "Distribution of development activity across core Dataiku "
        "capabilities over the last 30 days."
    ),
    "type": "graph",
    "usage_scoped": True,
    "tab": "Overview",
    "order": 10,
    "graph": {
        "kind": "bar",
        "x": "ratio",
        "y": "capability",
        "orientation": "h",
        "x_title": "Share of Development Activity",
        "y_title": "",
    },
}

def query():
    return """
        WITH aggregated AS (
            SELECT
                SUM(data_engineering_events) AS data_engineering_events,
                SUM(advanced_analytics_ml_events) AS advanced_analytics_ml_events,
                SUM(genai_llm_events) AS genai_llm_events,
                SUM(automation_orchestration_events) AS automation_orchestration_events,
                SUM(applications_delivery_events) AS applications_delivery_events,
                SUM(apis_integration_events) AS apis_integration_events,
                SUM(project_maintenance_events) AS project_maintenance_events,
                SUM(build_events) AS total_build_events
            FROM actor_usage_last_30_days_base
            WHERE login NOT LIKE 'api:%'
            {where_clause}
        ),
        long_form AS (
            SELECT 'PROJECT_MAINTENANCE' AS capability,
                   project_maintenance_events::DOUBLE / NULLIF(total_build_events, 0) AS ratio
            FROM aggregated
            UNION ALL
            SELECT 'DATA_ENGINEERING',
                   data_engineering_events::DOUBLE / NULLIF(total_build_events, 0)
            FROM aggregated
            UNION ALL
            SELECT 'ADVANCED_ANALYTICS_ML',
                   advanced_analytics_ml_events::DOUBLE / NULLIF(total_build_events, 0)
            FROM aggregated
            UNION ALL
            SELECT 'AUTOMATION_ORCHESTRATION',
                   automation_orchestration_events::DOUBLE / NULLIF(total_build_events, 0)
            FROM aggregated
            UNION ALL
            SELECT 'GENAI_LLM',
                   genai_llm_events::DOUBLE / NULLIF(total_build_events, 0)
            FROM aggregated
            UNION ALL
            SELECT 'APIS_INTEGRATION',
                   apis_integration_events::DOUBLE / NULLIF(total_build_events, 0)
            FROM aggregated
            UNION ALL
            SELECT 'APPLICATIONS_DELIVERY',
                   applications_delivery_events::DOUBLE / NULLIF(total_build_events, 0)
            FROM aggregated
        )
        SELECT capability, ratio
        FROM long_form
        ORDER BY ratio ASC   -- 🔄 IMPORTANT: ASC for horizontal so largest appears at top
    ;
    """
