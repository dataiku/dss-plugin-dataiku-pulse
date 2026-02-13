import streamlit as st

from components.dataiku_usages import (
    layout_usage_instances,
    layout_usage_actors,
)
from pulse_streamlit.utils import usage_kpis


st.set_page_config(initial_sidebar_state="expanded")
st.set_page_config(layout="wide")

def base_display(tab, category):
    # Setup base sidebar
    with st.sidebar:
        with st.container(border=True):
            genre = st.selectbox(
                label = "## Select an Usage Insight",
                options = ["Instances", "Actors"],
                index = 0
            )

    # Display
    data_category = category.lower().replace(" ", "_")
    st.header(f"Dataiku {category} -- {genre}")

    # Header KPIs
    kpis = usage_kpis.gather_data()
    st.caption("Analyze how development activity is distributed across Dataiku capabilities.")
    with st.container(border=True):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric(
            label="Total Build Events (30d)",
            value=f"{kpis['total_events']:,}"
        )
        c2.metric(
            label="Active Builders",
            value=f"{kpis['builders']}"
        )
        c3.metric(
            label="Active Instances",
            value=f"{kpis['instances']}"
        )
        c4.metric(
            label="Avg Events / Builder",
            value=f"{kpis['avg_per_builder']:,.0f}"
        )
        st.caption("Counts reflect all development activity over the last 30 days across the platform.")

    # Select Page to load
    if genre == "Instances":
        layout_usage_instances.display(tab, data_category)
    elif genre == "Actors":
        layout_usage_actors.display(tab, data_category)
    return