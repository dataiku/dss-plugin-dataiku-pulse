from dashboards.data_structures import structures
from src import dss_duck
import plotly.express as px


def main(filters = {}):
    # Build SQL Query Statement and Query, 
    query = structures.get_query_dict()
    query["select"] = ["*"]
    query["from"]   = ["users_metadata as base"]
    df = dss_duck.funcs.query_build_sql(query, filters)

    # Perform logic here
    FIG = structures.get("metric")

    # Perform logic here
    FIGS = []
    FIG["label"] = "Users With Access -- All Instances"
    FIG["data"] = int(df.groupby("enabled")["login"].nunique().loc[True])
    FIGS.append(FIG)

    # Split by Instance
    for i, g in df.groupby("instance_name"):
        FIG = structures.get("metric")
        FIG["label"] = f"Users With Access -- {i}"
        FIG["data"] = int(g.groupby("enabled")["login"].nunique().loc[True])
        FIGS.append(FIG)

    return FIGS