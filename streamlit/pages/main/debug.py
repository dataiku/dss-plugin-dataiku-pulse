import streamlit as st
from backend.duck_db import init_duckdb
from backend.duck_db import query

st.set_page_config(initial_sidebar_state="collapsed")

def reload_duckdb():
    with st.container(border=True):
        st.subheader("DuckDB Data Maintenance")
        genre = st.radio(
            "DuckDB Maintenance",
            ["🔄 Complete Reload DuckDB", "✨ Reload GOLD Tables Only"],
            horizontal=True,
            label_visibility="collapsed"
        )
        pwd = st.text_input("Password", type="password", width="stretch")
        btn_duckdb = st.button("Start", use_container_width=True)
        if btn_duckdb:
            if pwd == "dataikupulse2026":
                st.cache_data.clear()
                st.cache_resource.clear()
                if genre == "🔄 Complete Reload DuckDB":
                    init_duckdb.initialize_database(reset=True)
                elif genre == "✨ Reload GOLD Tables Only":
                    init_duckdb.rebuild_gold_tables()
            else:
                st.error("Invalid Password.")
    return

def debug_duckdb():
    with st.container(border=True):
        st.subheader("DuckDB Table Listing")
        object_type = st.radio(
            "DuckDB Object Type:",
            ["view", "base"],
            format_func=lambda x: "Views (virtual)" if x == "view" else "Base Tables (materialized)",
            horizontal=True
        )
        st.markdown("---")
        df = query.query_df("PRAGMA show_tables_expanded;", page="DEBUG")
        df = df.loc[df["name"].str.contains(f"_{object_type}")]
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
            data_df = query.query_df(f"SELECT * FROM {table_name} LIMIT 10", page="DEBUG")
            st.dataframe(
                data=data_df,
                hide_index=True
            )
            st.markdown("---")
            st.dataframe(query.query_df(f"DESCRIBE {table_name}", page="DEBUG"), hide_index=True)
            st.markdown("---")
            for col in data_df.columns:
                st.write(col)
    return

def display():
    tab1, tab2 = st.tabs(["Administrative Actions", "DuckDB Quick Viewer"])
    with tab1:
        reload_duckdb()
    with tab2:
        debug_duckdb()

if __name__ == "__main__":
    display()