import streamlit as st

from pulse_duckdb.engine import query
from pulse_streamlit.registry import load_analytics
from pulse_streamlit.utils import panel_filters, usages_tab

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
    sql = """
        SELECT
            instance_name AS instance,
            SUM(build_events) AS total_events,
            SUM(data_engineering_events) AS data_engineering,
            SUM(advanced_analytics_ml_events) AS advanced_analytics_ml,
            SUM(genai_llm_events) AS genai_llm,
            SUM(automation_orchestration_events) AS automation_orchestration,
            SUM(applications_delivery_events) AS applications_delivery,
            SUM(apis_integration_events) AS apis_integration,
            SUM(project_maintenance_events) AS project_maintenance
        FROM actor_usage_last_30_days_base
        WHERE login NOT LIKE 'api:%'
        GROUP BY instance_name
        ORDER BY total_events DESC
    """
    df = query.query_df(sql)
    st.dataframe(
        df,
        height=300,
        hide_index=True,
        on_select="ignore"
    )
    # Tabs
    st.divider()
    path = f"gold/{tab}/{data_category}/instances"
    analytics = load_analytics(path)
    if not any(analytics.values()):
        st.warning(f"No analytics found for path: {path}")
        return
    usages_tab.render(analytics, scope)