META = {
    "id": "development.actor_capability_mix_last_30_days",
    "version": 1,
    "label": "Actor Development Activity Mix (Last 30 Days)",
    "description": (
        "Distribution of development activity across core Dataiku "
        "capabilities for the selected user over the last 30 days."
    ),
    "type": "graph",
    "usage_scoped": True,
    "tab": "Overview",
    "order": 10,
    "graph": {
        "kind": "bar",
        "x": "scope",
        "y": [
            "data_engineering_ratio",
            "advanced_analytics_ml_ratio",
            "genai_llm_ratio",
            "automation_orchestration_ratio",
            "applications_delivery_ratio",
            "apis_integration_ratio",
        ],
        "barmode": "stack",
        "yaxis_tickformat": ".0%",
        "x_title": "",
        "y_title": "Share of Development Activity",
        "legend_title": "Capability",
    },
}

def query():
    return """
        SELECT
          login AS scope,

          SUM(data_engineering_events)::DOUBLE
            / SUM(build_events) AS data_engineering_ratio,

          SUM(advanced_analytics_ml_events)::DOUBLE
            / SUM(build_events) AS advanced_analytics_ml_ratio,

          SUM(genai_llm_events)::DOUBLE
            / SUM(build_events) AS genai_llm_ratio,

          SUM(automation_orchestration_events)::DOUBLE
            / SUM(build_events) AS automation_orchestration_ratio,

          SUM(applications_delivery_events)::DOUBLE
            / SUM(build_events) AS applications_delivery_ratio,

          SUM(apis_integration_events)::DOUBLE
            / SUM(build_events) AS apis_integration_ratio

        FROM actor_usage_last_30_days_base
        WHERE 1=1
            AND login NOT LIKE 'api:%'
           {where_clause}
        GROUP BY login
    ;
    """
