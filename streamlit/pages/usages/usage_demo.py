import streamlit as st
import plotly.express as px

from backend.duck_db import query

st.title("Developer Effort Allocation (Last 30 Days)")

view_mode = st.radio(
    "View Mode",
    ["Per Actor", "Per Instance"],
    horizontal=True
)

top_n = st.slider(
    "Top N Actors (by total activity)",
    min_value=5,
    max_value=25,
    value=10
)

# ------------------------------------------------
# Load data
# ------------------------------------------------
if view_mode == "Per Actor":
    df = query.query_df(f"""
        SELECT *
        FROM actor_usage_last_30_days_base
        WHERE login NOT LIKE 'api:%'
        ORDER BY build_events DESC
        LIMIT {top_n}
    """)

    x_col = "login"

else:
    df = query.query_df("""
        SELECT
          instance_name,

          SUM(data_engineering_events)::DOUBLE / SUM(build_events) AS data_engineering_ratio,
          SUM(advanced_analytics_ml_events)::DOUBLE / SUM(build_events) AS advanced_analytics_ml_ratio,
          SUM(genai_llm_events)::DOUBLE / SUM(build_events) AS genai_llm_ratio,
          SUM(automation_orchestration_events)::DOUBLE / SUM(build_events) AS automation_orchestration_ratio,
          SUM(applications_delivery_events)::DOUBLE / SUM(build_events) AS applications_delivery_ratio,
          SUM(apis_integration_events)::DOUBLE / SUM(build_events) AS apis_integration_ratio

        FROM actor_usage_last_30_days_base
        WHERE login NOT LIKE 'api:%'
        GROUP BY instance_name
    """)

    x_col = "instance_name"

# ------------------------------------------------
# Reshape for stacked bar
# ------------------------------------------------
ratio_cols = [
    "data_engineering_ratio",
    "advanced_analytics_ml_ratio",
    "genai_llm_ratio",
    "automation_orchestration_ratio",
    "applications_delivery_ratio",
    "apis_integration_ratio",
]

plot_df = df.melt(
    id_vars=[x_col],
    value_vars=ratio_cols,
    var_name="capability",
    value_name="ratio"
)

plot_df["capability"] = (
    plot_df["capability"]
    .str.replace("_ratio", "")
    .str.replace("_", " ")
    .str.title()
)

# ------------------------------------------------
# Plot
# ------------------------------------------------
fig = px.bar(
    plot_df,
    x=x_col,
    y="ratio",
    color="capability",
    title="Developer Effort Distribution by Capability",
    labels={
        "ratio": "Share of Activity",
        x_col: view_mode.replace("Per ", "")
    }
)

fig.update_layout(
    barmode="stack",
    yaxis=dict(tickformat=".0%"),
    height=500
)

st.plotly_chart(fig, use_container_width=True)
