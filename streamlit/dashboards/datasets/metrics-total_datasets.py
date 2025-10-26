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
    FIG["label"] = "Average number of Datasets per Project -- All"
    FIG["data"] = int(round(df.groupby("project_key")["dataset_name"].size().mean(), 0))
    FIGS.append(FIG)

    # Split by Instance
    for i, g in df.groupby("instance_name"):
        FIG = structures.get("metric")
        FIG["label"] = f"Average number of Datasets per Project -- {i}"
        FIG["data"] = int(round(g.groupby("project_key")["dataset_name"].size().mean(), 0))
        FIGS.append(FIG)

    return FIGS