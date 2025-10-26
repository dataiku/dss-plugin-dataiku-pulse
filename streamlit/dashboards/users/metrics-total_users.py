from sage.dashboards.data_structures import structures
from sage.src import dss_duck
import plotly.express as px


def main(filters = {}):
    # Build SQL Query Statement and Query, 
    query = structures.get_query_dict()
    query["select"] = ["*"]
    query["from"]   = ["users_metadata as base"]
    df = dss_duck.funcs.query_build_sql(query, filters)

    # Perform logic here
    FIG = structures.get("metric")

    FIGS = []
    FIG["label"] = "Total Unique Users -- All"
    total_users = df["login"].nunique()
    enabled_users = int(df.groupby("enabled")["login"].nunique().loc[True])
    delta_users = total_users - enabled_users
    total_users, enabled_users, delta_users
    FIG["data"] = total_users
    FIGS.append(FIG)

    # Split by Instance
    for i, g in df.groupby("instance_name"):
        FIG = structures.get("metric")
        FIG["label"] = f"Total Unique Users -- {i}"
        total_users = g["login"].nunique()
        enabled_users = int(g.groupby("enabled")["login"].nunique().loc[True])
        delta_users = total_users - enabled_users
        total_users, enabled_users, delta_users
        FIG["data"] = total_users
        FIGS.append(FIG)

    return FIGS