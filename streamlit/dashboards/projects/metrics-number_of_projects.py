from sage.dashboards.data_structures import structures
from sage.src import dss_duck
import plotly.express as px


def main(filters = {}):
    # Build SQL Query Statement and Query, 
    query = structures.get_query_dict()
    query["select"] = ["*"]
    query["from"]   = ["projects_metadata as base"]
    df = dss_duck.funcs.query_build_sql(query, filters)

    # load data structure
    FIG = structures.get("metric")
    
    # Perform logic here
    FIGS = []
    FIG["label"] = "Total DSS Projects -- All"
    FIG["data"] = df["project_key"].nunique()
    FIGS.append(FIG)

    # Split by Instance
    for i, g in df.groupby("instance_name"):
        FIG = structures.get("metric")
        FIG["label"] = f"Total DSS Projects -- {i}"
        FIG["data"] = g["project_key"].nunique()
        FIGS.append(FIG)

    return FIGS