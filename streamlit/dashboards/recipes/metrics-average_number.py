import pandas as pd
import plotly.express as px
from src import dss_duck
from dashboards.data_structures import structures


def main(filters = {}):
    # Build SQL Query Statement and Query, 
    query = structures.get_query_dict()
    query["select"] = ["*"]
    query["from"]   = ["recipes_metadata"]
    df = dss_duck.funcs.query_build_sql(query, filters)

    # load data structure
    FIG = structures.get("metric")
    
    # Perform logic here
    FIGS = []
    FIG["label"] = "Average number of Recipes per Project -- All"
    FIG["data"] = df.groupby("project_key")["recipes_name"].count().mean().round(0).astype(int)
    FIGS.append(FIG)

    # Split by Instance
    for i, g in df.groupby("instance_name"):
        FIG = structures.get("metric")
        FIG["label"] = f"Average number of Recipes per Project -- {i}"
        FIG["data"] = g.groupby("project_key")["recipes_name"].count().mean().round(0).astype(int)
        FIGS.append(FIG)

    return FIGS