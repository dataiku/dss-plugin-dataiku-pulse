from dashboards.data_structures import structures
from src import dss_duck
import plotly.express as px


def main(filters = {}):
    # Build SQL Query Statement and Query, 
    query = structures.get_query_dict()
    query["select"] = ["*"]
    query["from"]   = ["operating_system_filesystem as base"]
    df = dss_duck.funcs.query_build_sql(query, filters)

    # Perform logic here
    df = df.groupby(["instance_name", "mounted_on"])["used_pct"].max().reset_index(name="used_pct")

    # Plot
    fig = px.bar(
        df,
        y="mounted_on",
        x="used_pct",
        color="instance_name",
        barmode="group",
        text="used_pct",
        orientation='h',
        labels={"used_pct": "used_pct of filesystem name"},
        color_discrete_sequence=px.colors.qualitative.Set2
    )

    # Customize layout for polish
    fig.update_layout(
        yaxis_title="Filesystem Mount",
        xaxis_title="Percent Used",
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
    FIG["title"] = "Filesystem Used percent"
    FIG["data"] = fig
    
    return FIG