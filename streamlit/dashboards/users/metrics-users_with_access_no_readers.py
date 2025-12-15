import pandas as pd
import plotly.express as px
from src import dss_duck
from dashboards.data_structures import structures


def main(filters = {}):
    # Build SQL Query Statement and Query, 
    query = structures.get_query_dict()
    query["select"] = ["*"]
    query["from"]   = ["users_metadata"]
    query["where"] = ["userProfile NOT IN ('AI_CONSUMER', 'READER')"]
    df = dss_duck.funcs.query_build_sql(query, filters)

    # Perform logic here
    FIG = structures.get("metric")

    # Perform logic here
    FIGS = []
    FIG["label"] = "Users With Access (No Reader/Consumer) -- All Instances"
    FIG["data"] = int(df.groupby("enabled")["login"].nunique().loc[True])
    FIGS.append(FIG)

    # Split by Instance
    for i, g in df.groupby("instance_name"):
        FIG = structures.get("metric")
        FIG["label"] = f"Users With Access (No Reader/Consumer)users -- {i}"
        FIG["data"] = int(g.groupby("enabled")["login"].nunique().loc[True])
        FIGS.append(FIG)

    return FIGS