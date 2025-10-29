from dashboards.data_structures import structures
from src import dss_duck
import plotly.express as px
import pandas as pd


def main(filters = {}):
    # Build SQL Query Statement and Query, 
    query = structures.get_query_dict()
    query["select"] = [
        "COUNT(DISTINCT base.login) AS user_count",
        "EXTRACT(YEAR FROM last_session_activity) AS year",
        "EXTRACT(MONTH FROM last_session_activity) AS month",
        "base.instance_name"
    ]
    query["from"]   = ["users_metadata as base"]
    query["group"]  = ["year", "month", "base.instance_name"]
    query["where"]  = ["year <> 1970"]
    query["order"]  = ["year", "month"]
    df = dss_duck.funcs.query_build_sql(query, filters)

    # Perform logic here
    df["dt"] = df["YEAR"].astype(str) + "-" + df["MONTH"].astype(str)

    # Initial fig
    fig = px.bar(
        df,
        x="dt",
        y="user_count",
        color="instance_name",
        barmode="group",
        text="user_count",
        labels={"user_count": "Users with last session activity"},
        color_discrete_sequence=px.colors.qualitative.Set2
    )

    # Customize layout for polish
    fig.update_layout(
        xaxis_title="Date Range",
        yaxis_title="Active Users",
        legend_title="Instance Names"
    )

    # Add text annotations inside bars
    fig.update_traces(textposition="outside")
    
    # Build the FIG construct to return
    FIG = structures.get("plotly")
    FIG["title"] = "All User Attrition Over Time"
    FIG["desc"] = """
* Developer defined as Users who have logged in and created content.
* Attrition defined as Users who have logged in and have since stopped."""
    FIG["data"] = fig
    
    return FIG