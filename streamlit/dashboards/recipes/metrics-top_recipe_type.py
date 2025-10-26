from sage.dashboards.data_structures import structures
from sage.src import dss_duck
import plotly.express as px


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
    FIG["label"] = "Top Recipe Type -- All"
    filtered_df = df.groupby("recipes_type").size().sort_values(ascending=False).reset_index(name="count")
    recipe = filtered_df["recipes_type"].iloc[0]
    FIG["data"] = recipe
    FIGS.append(FIG)

    # Split by Instance
    for i, g in df.groupby("instance_name"):
        FIG = structures.get("metric")
        FIG["label"] = f"Top Recipe Type -- {i}"
        filtered_df = g.groupby("recipes_type").size().sort_values(ascending=False).reset_index(name="count")
        recipe = filtered_df["recipes_type"].iloc[0]
        FIG["data"] = recipe
        FIGS.append(FIG)

    return FIGS