import streamlit as st

from pulse_streamlit.utils import helper


def get_actors(analytics, actor_filters, filters=None):
    scope = {}
    df = helper.get_filtered_actors(filters=actor_filters)
    if df.empty:
        st.info("No users match the selected filters.")
        return scope
    with st.sidebar:
        with st.container(border=True):
            actor_login = st.selectbox(
                label = "## Select Actor",
                options = df["login"].dropna().tolist(),
                index = 0
            )
    scope = {
        "mode": "actor",
        "login": actor_login
    } | actor_filters
    return scope