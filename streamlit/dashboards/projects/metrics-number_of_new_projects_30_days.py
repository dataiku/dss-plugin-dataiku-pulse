from dashboards.data_structures import structures
from src import dss_duck
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
    from datetime import date, timedelta
    FIGS = []
    FIG["label"] = "Total new projects last 30 days -- All"
    FIG["data"] = len(df[df["project_creationtag_lastmodifiedon"].dt.date >= (date.today() - timedelta(30))]["project_key"])
    FIGS.append(FIG)

    # Split by Instance
    for i, g in df.groupby("instance_name"):
        FIG = structures.get("metric")
        FIG["label"] = f"Total new projects last 30 days -- {i}"
        FIG["data"] = len(g[g["project_creationtag_lastmodifiedon"].dt.date >= (date.today() - timedelta(30))]["project_key"])
        FIGS.append(FIG)

    return FIGS