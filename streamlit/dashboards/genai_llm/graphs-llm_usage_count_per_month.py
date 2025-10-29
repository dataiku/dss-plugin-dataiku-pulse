from dashboards.data_structures import structures
from src import dss_duck
import plotly.express as px
import pandas as pd


def main(filters = {}):
    # Build SQL Query Statement and Query, 
    query = structures.get_query_dict()
    query["select"] = [
        "EXTRACT(YEAR FROM timestamp) AS YEAR", 
        "EXTRACT(MONTH FROM timestamp) AS MONTH",
        "COUNT(*) AS genai_llm_count",
        "instance_name"
    ]
    query["from"]   = ["dataiku_usage_genai_llm as base"]
    query["group"]  = ["year", "month", "instance_name"]
    df = dss_duck.funcs.query_build_sql(query, filters)

    # Additional Processing
    df["date"] = df["YEAR"].astype(str) + "-" + df["MONTH"].astype(str)
    df["date"] = pd.to_datetime(df["date"])

    # Initial fig
    fig = px.bar(
        df,
        x="date",
        y="genai_llm_count",
        color="instance_name",
        barmode="group",
        text="genai_llm_count",
        labels={"genai_llm_count": "Count of GenAI LLM Dataiku Usage"},
        color_discrete_sequence=px.colors.qualitative.Set2
    )

    # Customize layout for polish
    fig.update_layout(
        xaxis_title="Date Range",
        yaxis_title="Total Count",
        legend_title="Instance Name",
        template="plotly_white",
        font=dict(size=14),
        bargap=0.15,
        bargroupgap=0.1
    )

    # Add text annotations inside bars
    fig.update_traces(textposition="outside")

    # Build the FIG construct to return
    FIG = structures.get("plotly")
    FIG["title"] = "Total Count of GenAI LLM Dataiku Usage"
    FIG["data"] = fig
    
    return FIG