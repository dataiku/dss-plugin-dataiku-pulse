import streamlit as st
from components.utils import panel_filters, usages_tab
from backend.streamlit.registry import load_analytics

def display(tab, data_category):
    with st.sidebar:
        with st.container(border=True):
            instance_name_f = panel_filters.filter_instance_name()
    if instance_name_f == "All (General)":
        scope = {"mode": "overview"}
    else:
        scope = {"mode": "instance", "instance_name": instance_name_f}
    # Page Body Section
    st.divider()
    path = f"gold/{tab}/{data_category}/instances"
    analytics = load_analytics(path)
    if not any(analytics.values()):
        st.warning(f"No analytics found for path: {path}")
        return
    usages_tab.render(analytics, scope)