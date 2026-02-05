import streamlit as st

from pulse_streamlit.registry import load_analytics
from components.dataiku_insights import (
    layout_metrics,
    layout_graphs,
)

st.set_page_config(initial_sidebar_state="expanded")
st.set_page_config(layout="wide")

def base_display(tab, category):
    # Setup base sidebar
    with st.sidebar:
        with st.container(border=True):
            genre = st.selectbox(
                label = "## Select an Insight",
                options = ["Metrics", "Graphs"], #, "Explore DataFrame"],
                index = 0
            )

    # Display
    data_category = category.lower().replace(" ", "_")
    st.header(f"Dataiku {category}: {genre}")
    st.divider()
    
    # Analytics
    analytics = load_analytics(f"gold/{tab}/{data_category}")
    if genre == "Metrics":
        layout_metrics.display(analytics["metrics"])
    elif genre == "Graphs":
        layout_graphs.display(analytics["graphs"])

    return