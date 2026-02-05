import streamlit as st

from pulse_streamlit.engine.executor import execute_analytic

def display(analytics, filters=None):
    if not analytics:
        st.info(f"No dataframes are available")
        return

    analytics_sorted = sorted(
        analytics.values(),
        key=lambda a: a["meta"].get("order", 999)
    )

    for i, analytic in enumerate(analytics_sorted):
        result = execute_analytic(analytic, filters)
        df = result["df"]
        meta = result["meta"]
        st.write(f"#### {meta['label']}")
        st.write(f"{meta['description']}")
        st.dataframe(df)

    return