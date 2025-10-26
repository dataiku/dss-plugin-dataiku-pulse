import streamlit as st
import plotly.express as px
from sage.src import dss_duck

# FUNCS
def make_category_chart(df, instance_name):
    # Filter for instance and get top 10 categories by msg_count
    df_inst = (
        df[df["instance_name"] == instance_name]
        .sort_values("msg_count", ascending=False)  # largest at top in horizontal chart
    )
    # Build the bar chart
    fig = px.bar(
        df_inst,
        x="msg_count",
        y="dataiku_category",
        orientation="h",
        color="dataiku_category",
        text="pct_of_instance",
        color_discrete_sequence=px.colors.qualitative.Set3,
        title=f"Top Dataiku Categories – {instance_name}",
    )
    # Tidy up visuals
    fig.update_traces(
        texttemplate="%{text:.2f}%",  # show pct as %
        textposition="outside"
    )
    fig.update_layout(
        xaxis_title="Message Count",
        yaxis_title="Dataiku Category",
        showlegend=False,
        margin=dict(l=100, r=30, t=60, b=40),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)"
    )

    return fig

# Variables
instances = dss_duck.funcs.query_direct_sql("SELECT DISTINCT instance_name FROM dataiku_usage_overview;")
instances = instances["instance_name"].tolist()
instances.insert(0, "All")

# SIDE BAR
with st.sidebar:
    # Get Values
    dt_range = st.selectbox("Select Month Range", dss_duck.funcs.year_month_range)
    metric = st.radio("Heat Map Metric", ["msg_count", "pct_of_instance"])
    add_radio = st.radio("Bar Chart Instance Filter", (instances))
    # Get DF
    query = f"""
        SELECT
            instance_name,
            dataiku_category,
            COUNT(*) AS msg_count,
            ROUND(
                100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY instance_name),
                2
            ) AS pct_of_instance
        FROM dataiku_usage_overview
        WHERE project_key IS NOT NULL
        AND project_key <> ''
        AND login IS NOT NULL
        AND login <> ''
        AND strftime(timestamp, '%Y-%m') = '{dt_range}'
        GROUP BY instance_name, dataiku_category
        ORDER BY instance_name, pct_of_instance DESC;
    """
    df = dss_duck.funcs.query_direct_sql(query)
    df_filtered = df.copy(deep=True)
    if add_radio != "All":
        df_filtered = df_filtered[df_filtered["instance_name"] == add_radio]

# Body
## CONTAINER
with st.container(border=True):
    # --- 2. Optionally limit to top categories across all instances ---
    top_categories = (
        df.groupby("dataiku_category")["msg_count"]
        .sum()
        .nlargest(10)
        .index
    )
    df_top = df[df["dataiku_category"].isin(top_categories)]

    # --- 3. Create pivot table for the heatmap ---
    pivot_df = df_top.pivot_table(
        index="instance_name", 
        columns="dataiku_category", 
        values=metric,
        aggfunc="sum",
        fill_value=0
    )

    # --- 4. Build the heatmap ---
    title="All Instances vs Top 10 Dataiku Categories (by Message Count)"
    if metric == "pct_of_instance":
        title="All Instances vs Top 10 Dataiku Categories (by % of Usage)"
    fig = px.imshow(
        pivot_df,
        color_continuous_scale="Blues",
        text_auto=True,
        aspect="auto",
        title=title,
    )

    # --- 5. Style & formatting ---
    title="MSG Count"
    if metric == "pct_of_instance":
        title="% of Usage"
    fig.update_layout(
        xaxis_title="Dataiku Category",
        yaxis_title="Instance Name",
        coloraxis_colorbar=dict(title=title),
        margin=dict(l=60, r=30, t=60, b=60),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)"
    )
    st.plotly_chart(fig)

with st.container(border = True):
    n = 0
    ncol = df_filtered["instance_name"].nunique()
    cols = st.columns(ncol, gap="small", border=True)
    for i, grp in df_filtered.groupby("instance_name"):
        with cols[n]:
            st.plotly_chart(make_category_chart(grp, i), use_container_width=True)
        n+=1

with st.container(border = True):
    st.dataframe(df_filtered.sort_values(by=["instance_name", "msg_count"], ascending=True))

# EOF