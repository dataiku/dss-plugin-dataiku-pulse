from sage.dashboards.data_structures import structures
from sage.src import dss_duck
import plotly.express as px
import pandas as pd
import streamlit as st


def main(filters = {}):
    # Build SQL Query Statement and Query, 
    query = structures.get_query_dict()
    query["select"] = ["base.project_creationtag_lastmodifiedon", "base.instance_name"]
    query["from"]   = ["projects_metadata as base"]
    df = dss_duck.funcs.query_build_sql(query, filters)

    # Perform logic here
    df["month"] = df["project_creationtag_lastmodifiedon"].dt.to_period("M")
    dfs = []
    for i,g in df.groupby(by="month"):
        tdf = g.groupby(["instance_name"]).size().reset_index(name="count")
        tdf["month"] = i
        dfs.append(tdf)
    filtered_df = pd.concat(dfs, ignore_index=True)
    filtered_df["month"] = filtered_df["month"].dt.to_timestamp()

    # Initial fig
    fig = px.bar(
        filtered_df,
        x="month",
        y="count",
        color="instance_name",
        barmode="group",
        text="count",
        labels={"count": "count of projects last modified"},
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
    FIG["title"] = "Total Count of Last Modified Projects"
    FIG["data"] = fig
    
    return FIG