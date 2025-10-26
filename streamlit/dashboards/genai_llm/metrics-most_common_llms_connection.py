from sage.dashboards.data_structures import structures
from sage.src import dss_duck
import plotly.express as px


def main(filters = {}):
    # Build SQL Query Statement and Query, 
    query = structures.get_query_dict()
    query["select"] = ["llms_conn_name", "instance_name"]
    query["from"]   = ["addon_llm_mapping as base"]
    df = dss_duck.funcs.query_build_sql(query, filters)

    # load data structure
    FIG = structures.get("metric")
    
    # Perform logic here
    FIGS = []
    FIG["label"] = "Most used LLM DSS Connection -- All"
    FIG["data"] = df.groupby("llms_conn_name").size().sort_values().tail(1).reset_index()["llms_conn_name"][0]
    FIGS.append(FIG)

    # Split by Instance
    for i, g in df.groupby("instance_name"):
        FIG = structures.get("metric")
        FIG["label"] = f"Most used LLM DSS Connection  -- {i}"
        FIG["data"] = g.groupby("llms_conn_name").size().sort_values().tail(1).reset_index()["llms_conn_name"][0]
        FIGS.append(FIG)

    return FIGS