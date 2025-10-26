from sage.dashboards.data_structures import structures
from sage.src import dss_duck
import plotly.express as px


def main(filters = {}):
    # Build SQL Query Statement and Query, 
    query = structures.get_query_dict()
    query["select"] = ["base.timestamp", "base.instance_name", "base.viewing_user_logins"]
    query["from"]   = ["users_rolling_viewing_user_logins as base"]
    df = dss_duck.funcs.query_build_sql(query, filters)

    # Perform logic here
    if df["timestamp"].dt.to_period("M").nunique() > 3:
        title = "Average Monthly User login"
        df["dt_range"] = df["timestamp"].dt.to_period("M")
        df = df.groupby(["instance_name", "dt_range"])["viewing_user_logins"].nunique().reset_index(name="total_logins")
        df["dt_range"] = df["dt_range"].dt.to_timestamp()
    else:
        title = "Average Daily User login"
        df["dt_range"] = df["timestamp"].dt.to_period("D")
        df = df.groupby(["instance_name", "dt_range"])["viewing_user_logins"].nunique().reset_index(name="total_logins")
        df["dt_range"] = df["dt_range"].dt.to_timestamp()

    # Initial fig
    fig = px.line(
        df,
        x="dt_range",
        y="total_logins",
        color="instance_name",
        text="total_logins",
        labels={"total_logins": "total login users"},
        color_discrete_sequence=px.colors.qualitative.Set2
    )

    # Customize layout for polish
    fig.update_layout(
        xaxis_title="Date Range",
        yaxis_title="Total Login Users",
        legend_title="Instance Names"
    )

    # Add text annotations inside bars
    fig.update_traces(textposition="top center")
    
    # Build the FIG construct to return
    FIG = structures.get("plotly")
    FIG["title"] = title
    FIG["data"] = fig
    
    return FIG