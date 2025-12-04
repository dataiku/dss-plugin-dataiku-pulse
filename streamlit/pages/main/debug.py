import streamlit as st
from src import dss_duck

st.set_page_config(initial_sidebar_state="collapsed")


with st.container(border=True):
    st.markdown("### DuckDB Table Listing")
    df = dss_duck.funcs.query_direct_sql("SHOW TABLES;")
    selected_rows = st.dataframe(
        df,
        key="my_dataframe",
        on_select="rerun",
        selection_mode="single-row",
        hide_index=True
    )
    st.markdown("---")
    index = selected_rows.selection["rows"][0]
    table_name = df.iat[index, 0]
    st.dataframe(dss_duck.funcs.query_direct_sql(f"DESCRIBE {table_name}"))