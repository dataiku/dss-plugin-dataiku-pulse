import streamlit as st
from collections import defaultdict
from components import panel_filters
from backend.utils import helper
from backend.streamlit.registry import load_analytics
from backend.streamlit.engine.executor import execute_analytic
from backend.streamlit.renderers.graphs import render_graph

def dataiku_login(analytics, actor_filters, filters=None):
    filters = filters or {}

    df = helper.get_filtered_actors(filters=actor_filters)
    if df.empty:
        st.info("No users match the selected filters.")
        return
    selected_rows = st.dataframe(
        df,
        key="users_df",
        on_select="rerun",
        selection_mode="single-row",
        hide_index=True
    )
    row_selected = selected_rows.selection["rows"]
    if row_selected:
        index = row_selected[0]
        actor_login = df.loc[df.index[index], "login"]
        scope = {
            "mode": "actor",
            "login": actor_login
        } | actor_filters
        for analytic in analytics["graphs"].values():
            result = execute_analytic(
                analytic,
                filters={},
                scope=scope
            )
            render_graph(result)
    return


def display(tab, data_category):
    with st.sidebar:
        with st.container(border=True):
            st.subheader("Actor Filters")
            instance_name_f = panel_filters.filter_instance_name()
            selected_profiles_f = panel_filters.filter_users_profile()
            enabled_option_f = panel_filters.filter_users_enabled()
            days_since_activity_f = panel_filters.filter_users_activity_ts()
    #
    actor_filters = {
        "instance_name": instance_name_f,
        "user_profiles": selected_profiles_f,
        "enabled_option": enabled_option_f,
        "days_since_activity": days_since_activity_f
    }
    analytics = load_analytics(f"gold/{tab}/{data_category}/actors")
    if not analytics:
        st.warning(
            f"No analytics found for path: gold/{tab}/{data_category}/instances"
        )
        return
    dataiku_login(analytics, actor_filters)