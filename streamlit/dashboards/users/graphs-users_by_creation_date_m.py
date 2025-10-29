from dashboards.data_structures import structures
from src import dss_duck
import plotly.express as px
import pandas as pd


def main(filters = {}):
    # Build SQL Query Statement and Query, 
    query = structures.get_query_dict()
    query["select"] = ["base.creationdate", "base.instance_name", "base.login"]
    query["from"]   = ["users_metadata as base"]
    df = dss_duck.funcs.query_build_sql(query, filters)

    # Perform logic here
    df["creationMonth"] = df["creationdate"].dt.to_period("M")
    df = df[df["creationdate"] != "1970-01-01"].reset_index()
    df = df.groupby(by=["instance_name", "creationMonth"])["login"].size().reset_index(name="user_count")
    cumsum = df.groupby("instance_name")["user_count"].cumsum()
    df = pd.concat([df.drop(columns=["user_count"]), cumsum], axis=1)
    df["creationMonth"] = df["creationMonth"].dt.to_timestamp()

    # Create the figure here
    fig = px.line(
        df,
        x="creationMonth",
        y="user_count",
        color="instance_name",
        text="user_count",
        labels={"creationMonth": "Creation Date (Month)", "user_count": "Cumulative Users"},
        color_discrete_sequence=px.colors.qualitative.Set2
    )

    # Update layout
    fig.update_layout(
        xaxis_tickformat="%Y-%m",
        xaxis=dict(tickangle=-45),
        legend_title="Instance Names"
    )

    # Add text annotations inside bars
    fig.update_traces(textposition="top center")

    # Build the FIG construct to return
    FIG = structures.get("plotly")
    FIG["title"] = "Cumulative Number of Users by Creation Date (Monthly)"
    FIG["data"] = fig
    
    return FIG