import streamlit as st
from backend.utils import helper
from backend.duck_db import query


def filter_instance_name():
    instance_name = st.selectbox(
        label = "## Select Overview or an Instance Name",
        options = ["All (General)"] + helper.get_instances(),
        index = 0
    )
    return instance_name


def filter_users_profile():
    profiles_df = query.query_df("""
        SELECT DISTINCT userProfile
        FROM users_metadata_base
        ORDER BY userProfile
    """)
    profiles = profiles_df["userProfile"].dropna().tolist()
    selected_profiles = st.multiselect(
        "User Profile",
        options=profiles
    )
    return selected_profiles


def filter_users_enabled():
    enabled_option = st.radio(
        "User Status",
        options=["Enabled only", "Disabled only", "All"],
        index=0
    )
    return enabled_option


def filter_users_activity_ts():
    days_since_activity = st.slider(
        "Last Activity (days)",
        min_value=0,
        max_value=180,
        value=60,
        help="Show users active within the last N days"
    )
    return days_since_activity