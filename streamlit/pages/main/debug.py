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
    row_selected = selected_rows.selection["rows"]
    if row_selected:
        index = row_selected[0]
        table_name = df.iat[index, 0]
        st.dataframe(
            data=dss_duck.funcs.query_direct_sql(f"SELECT * FROM {table_name} LIMIT 10"),
            hide_index=True
        )
        st.dataframe(dss_duck.funcs.query_direct_sql(f"DESCRIBE {table_name}"), hide_index=True)
