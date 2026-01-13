import streamlit as st
from backend.streamlit.engine.executor import execute_analytic
from backend.streamlit.renderers.metrics import render_metric

def display(analytics, filters=None):
    if not analytics:
        st.info(f"No metrics are available")
        return

    analytics_sorted = sorted(
        analytics.values(),
        key=lambda a: a["meta"].get("order", 999)
    )

    num_cols = min(3, len(analytics_sorted))
    cols = st.columns(num_cols)

    for i, analytic in enumerate(analytics_sorted):
        result = execute_analytic(analytic, filters)
        with cols[i % num_cols]:
            render_metric(result)

    return