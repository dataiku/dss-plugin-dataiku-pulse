import streamlit as st
from collections import defaultdict
from components import panel_filters
from backend.utils import helper
from backend.streamlit.registry import load_analytics
from backend.streamlit.engine.executor import execute_analytic
from backend.streamlit.renderers.graphs import render_graph

def display(tab, data_category):
    # Sidebar: Instance selection
    with st.sidebar:
        with st.container(border=True):
            instance_name_f = panel_filters.filter_instance_name()

    # Usage scope (NOT filters)
    if instance_name_f == "All (General)":
        base_scope = {
            "mode": "overview"
        }
    else:
        base_scope = {
            "mode": "instance",
            "instance_name": instance_name_f
        }

    # Filters remain empty for now (dates, flags later)
    filters = {}

    # Load analytics
    analytics = load_analytics(f"gold/{tab}/{data_category}/instances")
    if not analytics:
        st.warning(
            f"No analytics found for path: gold/{tab}/{data_category}/instances"
        )
        return
    
    # Tabs: Overview + Capabilities
    capabilities = ["Overview"] + helper.get_canonical_capabilities()
    tab_labels =  [helper.format_capability_label(c) for c in capabilities]
    tabs = st.tabs(tab_labels)

    # Group graphs by tab
    graphs_by_tab = defaultdict(list)
    for analytic in analytics.get("graphs", {}).values():
        tab_name = analytic["meta"].get("tab")
        if tab_name == "CAPABILITY":
            for cap in helper.get_canonical_capabilities():
                graphs_by_tab[cap].append(analytic)
        else:
            graphs_by_tab[tab_name].append(analytic)

    # Render graphs per tab
    for tab_obj, capability in zip(tabs, capabilities):
        with tab_obj:
            # Build scope for this tab
            scope = dict(base_scope)
            if capability != "Overview":
                scope["capability"] = capability
            # Capability explanation
            if capability != "Overview":
                subcats = helper.get_subcategories_for_capability(capability)
                st.markdown(
                    f"**{helper.format_capability_label(capability)}** includes: "
                    + ", ".join(subcats)
                )
                signal = helper.get_capability_summary_signal(
                    capability,
                    instance_name_f if instance_name_f != "All (General)" else None
                )
                if not signal:
                    st.warning(
                        "No development activity detected for this capability in the last 30 days."
                    )
                    continue
                summary = helper.format_capability_summary(capability, signal)
                if summary:
                    st.info(summary)

            # Render graphs for this tab
            for meta in graphs_by_tab.get(capability, []):
                result = execute_analytic(
                    meta,
                    filters=filters,
                    scope=scope
                )
                with st.expander(result["meta"]["label"], expanded=False):
                    render_graph(
                        result,
                        key=f"{result['meta']['id']}::{capability}"
                    )
