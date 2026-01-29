import streamlit as st
from backend.streamlit.registry import load_analytics
from backend.utils import helper
from components.dataiku_insights import layout_metrics, layout_graphs, layout_dataframes

st.set_page_config(initial_sidebar_state="expanded")
st.set_page_config(layout="wide")

def base_display(tab, category):
    # Setup base sidebar
    with st.sidebar:
        with st.container(border=True):
            genre = st.selectbox(
                label = "## Select an Insight",
                options = ["Metrics", "Graphs", "DataFrames"],
                index = 0
            )

        with st.container(border=True):
            instances = helper.get_instances()
            instance_name = st.selectbox(
                label = "## Select an Instance Name",
                options = instances,
                index = 0
            )

    # Display
    data_category = category.lower().replace(" ", "_")
    st.header(f"Dataiku {category}")
    st.divider()

    # Analytics
    analytics = load_analytics(f"gold/{tab}/{data_category}")
    filters = {"instance_name": instance_name}

    # Select Page to load
    if genre == "Metrics":
        layout_metrics.display(analytics["metrics"], filters=filters)
    elif genre == "Graphs":
        layout_graphs.display(analytics["graphs"], filters=filters)
    elif genre == "DataFrames":
        layout_dataframes.display(analytics["dataframes"], filters=filters)
    return