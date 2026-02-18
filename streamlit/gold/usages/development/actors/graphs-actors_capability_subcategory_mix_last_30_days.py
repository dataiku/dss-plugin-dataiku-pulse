META = {
    "id": "development.actors_capability_subcategory_mix_last_30_days",
    "version": 2,  # 🔄 bumped version
    "label": "Actor Capability Sub-Category Breakdown (Last 30 Days)",
    "description": (
        "Distribution of an individual actor’s development activity within a "
        "selected canonical capability, broken down by underlying Dataiku "
        "categories over the last 30 days. Values represent the share of "
        "activity within the selected capability."
    ),
    "type": "graph",
    "usage_scoped": True,
    "tab": "CAPABILITY",
    "order": 30,
    "graph": {
        "kind": "bar",
        "x": "ratio",
        "y": "dataiku_category",
        "orientation": "h",
        "x_title": "Share of Capability Activity",
        "y_title": "",
    },
}

def query():
    return """
        WITH base AS (
            SELECT
                dataiku_category,
                SUM(event_count) AS event_count
            FROM actor_capability_subcategory_usage_last_30_days_base
            WHERE 1=1
              {capability_clause}
              {where_clause}
            GROUP BY dataiku_category
        )

        SELECT
            dataiku_category,
            event_count::DOUBLE
              / NULLIF(SUM(event_count) OVER (), 0) AS ratio
        FROM base
        ORDER BY ratio ASC   -- 🔄 ASC so largest appears at top (horizontal rule)
    ;
    """
