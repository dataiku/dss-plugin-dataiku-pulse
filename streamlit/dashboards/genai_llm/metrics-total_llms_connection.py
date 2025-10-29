from dashboards.data_structures import structures
from src import dss_duck
import plotly.express as px


def main(filters = {}):
    # Build SQL Query Statement and Query, 
    query = structures.get_query_dict()
    query["select"] = [
        "base.instance_name",
        "COUNT(*) as llm_mesh_count"
    ]
    query["from"] = ["instance_connections as base"]
    query["where"] = ["connection_category in ('llm_mesh')"]
    query["group"]  = ["base.instance_name"]
    df = dss_duck.funcs.query_build_sql(query, filters)

    # load data structure
    FIG = structures.get("metric")
    
    # Perform logic here
    FIGS = []
    FIG["label"] = "Total LLM Mesh Connections -- All"
    FIG["data"] = df["llm_mesh_count"].sum()
    FIGS.append(FIG)

    # Split by Instance
    for i, g in df.groupby("instance_name"):
        FIG = structures.get("metric")
        FIG["label"] = f"Total LLM Mesh Connections -- {i}"
        FIG["data"] = g["llm_mesh_count"].sum()
        FIGS.append(FIG)

    return FIGS