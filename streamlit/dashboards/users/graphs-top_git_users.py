from sage.dashboards.data_structures import structures
from sage.src import dss_duck
import plotly.express as px


def main(filters = {}):
    # Build SQL Query Statement and Query, 
    query = structures.get_query_dict()
    query["select"] = ["timestamp", "author", "instance_name"]
    query["from"]   = ["users_git_history as base"]
    df = dss_duck.funcs.query_build_sql(query, filters)

    # Perform logic here
    from datetime import datetime, timedelta
    today = datetime.now()
    one_year_ago = today - timedelta(days=365)
    filtered_df = df[df["timestamp"].dt.date >= one_year_ago.date()]
    filtered_df = filtered_df[~filtered_df["author"].str.contains("api:")]
    filtered_df = filtered_df.groupby(["instance_name", "author"]).size().reset_index(name="count")
    filtered_df = filtered_df.sort_values(by=["instance_name", "count"], ascending=False)
    filtered_df = filtered_df.groupby("instance_name").head(10)

    # Initial fig
    fig = px.bar(
        filtered_df,
        x="author",
        y="count",
        color="instance_name",
        barmode="group",
        text="count",
        labels={"count": "Number of total GIT commits"},
        color_discrete_sequence=px.colors.qualitative.Set2
    )

    # Customize layout for polish
    fig.update_layout(
        xaxis_title="Dataiku login",
        yaxis_title="Total GIT Commits",
        legend_title="Instance Names"
    )

    # Add text annotations inside bars
    fig.update_traces(textposition="outside")

    # Build the FIG construct to return
    FIG = structures.get("plotly")
    FIG["title"] = "Top 10 Active Users (GIT) over the last 365 Days"
    FIG["data"] = fig

    return FIG