META = {
    "id": "development.actors_capability_subcategory_mix_last_30_days",
    "version": 1,
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
        "x": "scope",
        "y": "ratio",
        "color": "dataiku_category",
        "barmode": "stack",
        "yaxis_tickformat": ".0%",
        "x_title": "",
        "y_title": "Share of Capability Activity",
        "legend_title": "Sub-Category",
    },
}


def query():
    return """
        SELECT
            {scope_expr} AS scope,
            dataiku_category,
            event_count::DOUBLE
              / SUM(event_count) OVER () AS ratio
        FROM actor_capability_subcategory_usage_last_30_days_base
        WHERE 1=1
          {capability_clause}
          {where_clause}
        GROUP BY
            scope,
            dataiku_category,
            event_count
    ;
    """
