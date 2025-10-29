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
    from datetime import date, timedelta
    FIG = structures.get("metric")

    # Perform logic here
    FIGS = []
    FIG["label"] = "New Users over the last 30 days -- All"
    FIG["data"] = df[df["creationdate"].dt.date >= (date.today() - timedelta(30))]["login"].nunique()
    FIGS.append(FIG)

    # Split by Instance
    for i, g in df.groupby("instance_name"):
        FIG = structures.get("metric")
        FIG["label"] = f"New Users over the last 30 days -- {i}"
        FIG["data"] = g[g["creationdate"].dt.date >= (date.today() - timedelta(30))]["login"].nunique()
        FIGS.append(FIG)

    return FIGS