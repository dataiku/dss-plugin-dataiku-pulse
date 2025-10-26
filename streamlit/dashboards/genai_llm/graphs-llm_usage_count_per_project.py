from sage.dashboards.data_structures import structures
from sage.src import dss_duck
import plotly.express as px
import pandas as pd


def main(filters = {}):
    # Build SQL Query Statement and Query, 
    query = structures.get_query_dict()
    query["select"] = [
        "EXTRACT(YEAR FROM timestamp) AS year",
        "EXTRACT(MONTH FROM timestamp) AS month",
        "count(*) AS genai_llm_count",
        "instance_name",
        "project_key"
    ]
    query["from"]   = ["dataiku_usage_genai_llm as base"]
    query["group"]  = ["year", "month", "instance_name", "project_key"]
    df = dss_duck.funcs.query_build_sql(query, filters)

    # Additional Processing
    df["date"] = df["YEAR"].astype(str) + "-" + df["MONTH"].astype(str)
    df["date"] = pd.to_datetime(df["date"])

    # Get top 10 projects by genai_llm_count
    top_projects = (
        df.sort_values(["instance_name", "genai_llm_count"], ascending=[True, False])
        .groupby("instance_name", group_keys=False)
        .head(10)
    )


    # Plotly bar chart
    fig = px.bar(
        top_projects,
        x="project_key",
        y="genai_llm_count",
        color="instance_name",
        text="genai_llm_count",
        title="Top 10 Projects per Instance by LLM Calls",
        labels={"project_key": "Project", "genai_llm_count": "LLM Calls"},
        color_discrete_sequence=px.colors.qualitative.Set2
    )

    # Customize layout for polish
    fig.update_layout(
        xaxis_title="Project Keys",
        yaxis_title="Total Count",
        legend_title="Instance Name",
        template="plotly_white",
        font=dict(size=14),
        bargap=0.15,
        bargroupgap=0.1
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(xaxis_tickangle=-45, bargap=0.3)

    # Build the FIG construct to return
    FIG = structures.get("plotly")
    FIG["title"] = "Total Count of GenAI LLM Dataiku Usage"
    FIG["data"] = fig
    
    return FIG