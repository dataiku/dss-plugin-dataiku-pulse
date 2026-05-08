import streamlit as st

from pulse_duckdb import settings
from pulse_duckdb.engine import init_streamlit, query


st.set_page_config(initial_sidebar_state="collapsed")


def reload_duckdb():
    with st.container(border=True):
        st.subheader("DuckDB Data Maintenance")
        pwd = st.text_input("Password", type="password", width="stretch")
        btn_duckdb = st.button("Start", use_container_width=True)
        if btn_duckdb:
            if pwd == "dataikupulse2026":
                st.cache_data.clear()
                st.cache_resource.clear()
                init_streamlit.initialize_database()
            else:
                st.error("Invalid Password.")
    return


def debug_duckdb():
    with st.container(border=True):
        st.subheader("DuckDB Table Listing")
        st.markdown("---")
        df = query.query_df("PRAGMA show_tables_expanded;", page="DEBUG")
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
            data_df = query.query_df(f"SELECT * FROM {table_name} LIMIT 10", page="DEBUG")  # nosec
            st.dataframe(
                data=data_df,
                hide_index=True
            )
            st.markdown("---")
            st.dataframe(query.query_df(f"DESCRIBE {table_name}", page="DEBUG"), hide_index=True)  # nosec
            st.markdown("---")
            for col in data_df.columns:
                st.write(col)
    return


tab1, tab2 = st.tabs(["Administrative Actions", "DuckDB Quick Viewer"])
with tab1:
    reload_duckdb()
with tab2:
    if settings.DB_PATH.exists():
        debug_duckdb()
    else:
        st.error("No DuckDB to read from")