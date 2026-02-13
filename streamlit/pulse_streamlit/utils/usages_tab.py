from collections import defaultdict

import streamlit as st

from pulse_streamlit.utils import usage_kpis
from pulse_streamlit.utils import helper
from pulse_streamlit.engine.executor import execute_analytic
from pulse_streamlit.renderers.graphs import render_graph


def render(analytics, scope, filters=None):
    scope = dict(scope)
    if not scope:
        return
    filters = filters or {}
    instance_name = scope.get("instance_name")
    if instance_name == "All (General)":
        instance_name = None

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
            tab_scope = dict(scope)
            if capability == "Overview":
                total_events = usage_kpis.get_total_build_events_last_30_days(tab_scope)
                st.markdown(f"**Total development events (last 30 days): {total_events:,}**")
            else:
                tab_scope["capability"] = capability
                subcats = helper.get_subcategories_for_capability(capability)
                st.markdown(
                    f"**{helper.format_capability_label(capability)}** includes: "
                    + ", ".join(subcats)
                )
                signal = helper.get_capability_summary_signal(capability, instance_name)
                if not signal:
                    st.warning(
                        "No development activity detected for this capability in the last 30 days."
                    )
                    continue
                summary = helper.format_capability_summary(capability, signal)
                if summary:
                    st.info(summary)
                counts_df = usage_kpis.get_subcategory_counts_last_30_days(
                    capability=capability,
                    instance_name=instance_name
                )
                if not counts_df.empty:
                    summary = usage_kpis.format_subcategory_counts(counts_df)
                    st.caption("Sub-category activity counts (last 30 days):")
                    st.caption(summary)

            # Render graphs for this tab
            for meta in graphs_by_tab.get(capability, []):
                result = execute_analytic(
                    meta,
                    filters=filters,
                    scope=tab_scope
                )
                with st.expander(result["meta"]["label"], expanded=False):
                    render_graph(
                        result,
                        key=f"{result['meta']['id']}::{capability}"
                    )
    return