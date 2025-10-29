import streamlit as st
import pandas as pd
import random

from src import dss_streamlit
from src import dss_duck
from dashboards.data_structures import display_graph

filter_df = df = dss_duck.funcs.query_direct_sql("SELECT * FROM metadata_primary_keys")
if "filter_df" not in st.session_state:
    st.session_state.filter_df = filter_df


# ------------------------------------------------------------------------------------
def main(data_category, display_data, modules, graphs):
    # Enable Filtering
    with st.sidebar:
        with st.container(border=True):
            # Build the filters dictionary
            st.write("Filter Graphs on.....")
            st_filters = {}
            st_filters["instance_name"] = {"label": "Instance Name", "options": filter_df["instance_name"].unique().tolist()}
            st_filters["userprofile"]   = {"label": "License Type", "options": filter_df["userprofile"].unique().tolist()}
            st_filters["enabled"]       = {"label": "Current Users Enabled/Disabled", "options": filter_df["enabled"].unique().tolist()}
            st_filters["login"]         = {"label": "Login Name", "options": filter_df["login"].unique().tolist()}
            st_filters["project_key"]   = {"label": "Project key", "options": filter_df["project_key"].unique().tolist()}
            display_filter = {}
            for key in st_filters:
                label = st_filters[key]["label"]
                options = st_filters[key]["options"]
                display_filter[key] = st.multiselect(label=label, options=options)
    
    # Create the final filter
    final_filter = {}
    for key in display_filter.keys():
        if display_filter[key]:
            final_filter[key] = display_filter[key]

    # load only the graph dashboards
    dashboards = graphs
    if not dashboards:
        for value in display_data:
            if value.split(" ")[0].lower() == "graphs":
                dashboards.append(value)
        if not dashboards:
            st.error(f"No {data_category} Graphs to display.")
            return

    # Display
    st.markdown("**Caution** - Utilizing certain filters may void/zero-out a graph.")
    random_integers = []
    for key in dashboards:
        with st.container(border=True):
            module_name = modules[key][0]
            fp = modules[key][1]
            FIG = dss_streamlit.load_insights(module_name, fp, final_filter)
            if "key" in FIG:
                while True:
                    random_integer = random.randint(1, 10000)
                    if random_integer not in random_integers:
                        random_integers.append(random_integer)
                        break # break the loop, this is intended
                FIG["key"] = FIG["key"] + f"_display.{random_integer}"
            display_graph.main(FIG)
    
    return