import streamlit as st
import plotly.express as px
from src import dss_duck


# Global Variables
usage_display = "GENAI LLM"
year_month_range = st.session_state["partition_df"]["date"].dt.to_period('M').astype(str).unique().tolist()


# ------------------------------------------------------------------------------------
usage_category = usage_display.lower().replace(" ", "_")
usage_table = f"dataiku_usage_{usage_category}"

def potential():
    return


# SIDE BAR
with st.sidebar:
    # Variables
    instances = dss_duck.funcs.query_direct_sql(f"SELECT DISTINCT instance_name FROM {usage_table};")
    instances = instances["instance_name"].tolist()
    instances.insert(0, "All")
    # Get Values
    dt_range = st.selectbox("Select Month Range", year_month_range)
    metric = st.radio("Heat Map Metric", ["pct_of_instance", "msg_count"])
    add_radio = st.radio("Bar Chart Instance Filter", (instances))
    df = dss_duck.funcs.query_direct_sql(f"""
        SELECT
            instance_name,
            msgtypebase AS sub_category,
            COUNT(*) AS msg_count,
            ROUND(
                100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY instance_name),
                2
            ) AS pct_of_instance
        FROM {usage_table}
        GROUP BY instance_name, sub_category
        ORDER BY instance_name, pct_of_instance DESC;
    """)
    df_filtered = df.copy(deep=True)
    if add_radio != "All":
        df_filtered = df_filtered[df_filtered["instance_name"] == add_radio]



# Body
## CONTAINER
with st.container(border=True):
    pivot_df = df.pivot_table(
        index="instance_name", 
        columns="sub_category", 
        values=metric,
        aggfunc="sum",
        fill_value=0
    )
    title = f"{usage_display} - Sub Categories"
    if metric == "pct_of_instance":
        title = f"{title} (by % of Usage)"
    else:
        title = f"{title} (by Message Count)"
    fig = px.imshow(
        pivot_df,
        color_continuous_scale="Blues",
        text_auto=True,
        aspect="auto",
        title=title,
    )
    if metric == "pct_of_instance":
        title="% of Usage"
    else:
        title="MSG Count"
    fig.update_layout(
        xaxis_title="Dataiku Category",
        yaxis_title="Instance Name",
        coloraxis_colorbar=dict(title=title),
        margin=dict(l=60, r=30, t=60, b=60),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)"
    )
    st.plotly_chart(fig)