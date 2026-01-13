import streamlit as st
from collections import defaultdict
from backend.streamlit.engine.executor import execute_analytic
from backend.streamlit.renderers.graphs import render_graph

PREFERRED_TAB_ORDER = [
    "Summary",
    "Activity",
    "License",
    "Trends",
]

def display(analytics, filters=None):
    if not analytics:
        st.info(f"No graphs are available")
        return

    # Group graphs by META.group
    grouped = defaultdict(list)
    for analytic in analytics.values():
        group = analytic["meta"].get("tab", "Other").replace("_", " ").title()
        grouped[group].append(analytic)

    # Create tabs
    tab_labels = (
        [tab for tab in PREFERRED_TAB_ORDER if tab in grouped]
        + [tab for tab in grouped.keys() if tab not in PREFERRED_TAB_ORDER]
    )
    tabs = st.tabs(tab_labels)

    # Render graphs inside each tab
    for tab, tab_label in zip(tabs, tab_labels):
        with tab:
            for analytic in grouped.get(tab_label, []):
                result = execute_analytic(analytic, filters)
                with st.expander(result["meta"]["label"], expanded=False):
                    render_graph(result)
