from dashboards.data_structures import structures
from src import dss_duck
import plotly.express as px


def main(filters = {}):
    # Build SQL Query Statement and Query, 
    query = structures.get_query_dict()
    query["select"] = ["*"]
    query["from"]   = ["scenarios_metadata as base"]
    df = dss_duck.funcs.query_build_sql(query, filters)

    # load data structure
    FIG = structures.get("metric")
    
    # Perform logic here
    filtered_df = df[df["scenarios_active"] == True]
    FIGS = []
    FIG["label"] = "Total number of Enabled Scenarios -- All"
    FIG["data"] = filtered_df["scenarios_id"].count()
    FIGS.append(FIG)

    # Split by Instance
    for i, g in filtered_df.groupby("instance_name"):
        FIG = structures.get("metric")
        FIG["label"] = f"Total number of Enabled Scenarios -- {i}"
        FIG["data"] = g["scenarios_id"].count()
        FIGS.append(FIG)

    return FIGS
