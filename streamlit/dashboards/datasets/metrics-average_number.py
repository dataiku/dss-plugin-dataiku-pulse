from sage.dashboards.data_structures import structures
from sage.src import dss_duck
import plotly.express as px


def main(filters = {}):
    # Build SQL Query Statement and Query, 
    query = structures.get_query_dict()
    query["select"] = ["*"]
    query["from"]   = ["datasets_metadata as base"]
    df = dss_duck.funcs.query_build_sql(query, filters)

    # load data structure
    FIG = structures.get("metric")

    FIGS = []
    FIG["label"] = "Total number of Dataset Names -- All"
    FIG["data"] = df["dataset_name"].count()
    FIGS.append(FIG)

    # Split by Instance
    for i, g in df.groupby("instance_name"):
        FIG = structures.get("metric")
        FIG["label"] = f"Total number of Dataset Names -- {i}"
        FIG["data"] = g["dataset_name"].count()
        FIGS.append(FIG)

    return FIGS