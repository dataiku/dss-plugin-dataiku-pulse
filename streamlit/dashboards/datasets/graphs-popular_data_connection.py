from sage.dashboards.data_structures import structures
from sage.src import dss_duck
import plotly.express as px


def main(filters = {}):
    # Build SQL Query Statement and Query, 
    query = structures.get_query_dict()
    query["select"] = ["base.instance_name", "base.dataset_type"]
    query["from"]   = ["datasets_metadata as base"]
    df = dss_duck.funcs.query_build_sql(query, filters)

    # perform logic here
    filtered_df = df.groupby(["instance_name", "dataset_type"]).size().reset_index(name="count")
    filtered_df = filtered_df.sort_values(by=["instance_name", "count"], ascending=False)
    filtered_df = filtered_df.groupby("instance_name").head(10)

    # Plot
    fig = px.bar(
        filtered_df,
        y="dataset_type",
        x="count",
        color="instance_name",
        barmode="group",
        text="count",
        labels={"count": "count of dataset type"},
        color_discrete_sequence=px.colors.qualitative.Set2
    )

    # Customize layout for polish
    fig.update_layout(
        yaxis_title="Dataset Type",
        xaxis_title="Total Count",
        legend_title="Instance Name",
        template="plotly_white",
        font=dict(size=14),
        bargap=0.15,
        bargroupgap=0.1
    )

    # Add text annotations inside bars and background lines
    fig.update_traces(textposition="outside")
    fig.update_layout(
        xaxis=dict(showgrid=True, gridcolor='lightgray', zeroline=False),
        yaxis=dict(showgrid=True, gridcolor='lightgray', zeroline=False)
    )

    # Build the FIG construct to return
    FIG = structures.get("plotly")
    FIG["title"] = "Top 10 Dataset Types"
    FIG["data"] = fig
    
    return FIG