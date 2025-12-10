import streamlit as st
from src import dss_duck

st.set_page_config(initial_sidebar_state="collapsed")


with st.container(border=True):
    st.markdown("### DuckDB Table Listing")
    df = dss_duck.funcs.query_direct_sql("""
        SELECT table_catalog, table_schema, table_name, table_type
        FROM information_schema.tables;"""
    )
    st.write(f"Total Tables: {len(df)}")
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
        table_name = df.iat[index, 2]
        st.write(table_name)
        st.dataframe(
            data=dss_duck.funcs.query_direct_sql(f"SELECT COUNT(*) AS count FROM {table_name}"),
            hide_index=True
        )
        st.markdown("---")
        st.dataframe(
            data=dss_duck.funcs.query_direct_sql(f"SELECT * FROM {table_name} LIMIT 10"),
            hide_index=True
        )
        st.markdown("---")
        st.dataframe(dss_duck.funcs.query_direct_sql(f"DESCRIBE {table_name}"), hide_index=True)
        st.markdown("---")
        