from sage.dashboards.data_structures import structures
from sage.src import dss_duck
import plotly.express as px


def main(filters = {}):
    # Build SQL Query Statement and Query, 
    query = structures.get_query_dict()
    query["select"] = ["COUNT(DISTINCT base.login) AS profile_count", "base.instance_name", "base.userprofile"]
    query["from"]   = ["users_metadata as base"]
    query["group"]  = ["base.instance_name", "base.userprofile"]
    query["where"]  = ["base.enabled is True"]
    df = dss_duck.funcs.query_build_sql(query, filters)

    # Initial fig
    fig = px.bar(
        df,
        x="userprofile",
        y="profile_count",
        color="instance_name",
        barmode="group",
        text="profile_count",
        labels={"userprofile": "Dataiku profile", "profile_count": "Cumulative Profile Usage"},
        color_discrete_sequence=px.colors.qualitative.Set2
    )

    # Customize layout for polish
    fig.update_layout(
        legend_title="Instance Names"
    )

    # Add text annotations inside bars
    fig.update_traces(textposition="outside")

    # Build the FIG construct to return
    FIG = structures.get("plotly")
    FIG["title"] = "Dataiku Active Profile Usage"
    FIG["data"] = fig
    
    return FIG