META = {
    "id": "development.capability_subcategory_mix_last_30_days",
    "version": 2,  # 🔄 bumped version
    "label": "Capability Sub-Category Breakdown (Last 30 Days)",
    "description": (
        "Distribution of development activity within a canonical capability, "
        "broken down by underlying Dataiku categories over the last 30 days. "
        "Values represent the share of activity within the selected capability."
    ),
    "type": "graph",
    "usage_scoped": True,
    "tab": "CAPABILITY",
    "order": 20,
    "graph": {
        "kind": "bar",
        "x": "ratio",                 # 🔄 CHANGED
        "y": "dataiku_category",      # 🔄 CHANGED
        "orientation": "h",           # 🔄 NEW
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
            FROM capability_subcategory_usage_last_30_days_base
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
        ORDER BY ratio ASC   -- 🔄 ASC for horizontal (largest at top)
    ;
    """
