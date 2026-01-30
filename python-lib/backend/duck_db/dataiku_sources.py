import logging
import shutil
import pandas as pd
from pathlib import Path
import streamlit as st
from backend import settings
from backend.duck_db import query

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# Create Dataiku Partitioned DataFrame
# -------------------------------------------------------------------
def build_partitioned_data_df():
    logger.warning("Building dataframe from Dataiku Partitioned Data folder...")
    partitions = settings.dss_partitioned_folder.list_partitions()
    df = pd.DataFrame(partitions, columns=["partitions"])
    cols = ["layer", "category", "module", "instance_name"]
    df[cols] = df["partitions"].str.split("|", expand=True)
    df = (
        df
        .loc[df["layer"]
        .isin(["silver"])]
        .reset_index(drop=True)
    )
    df["category"] = df["category"].str.replace("category=", "", regex=False)
    df["module"] = df["module"].str.replace("module=", "", regex=False)
    df["instance_name"] = df["instance_name"].str.replace("cateinstance_namegory=", "", regex=False)
    logger.warning(f"Dataframe created with {len(df)} rows.")
    return df

def build_gold_tables_df():
    logger.warning("Building dataframe from Dataiku Partitioned Data folder...")
    paths = settings.dss_gold_tables_folder.list_paths_in_partition()
    if not paths:
        return pd.DataFrame()
    df = pd.DataFrame(paths, columns=["paths"])
    cols = ["dot", "layer", "gold_table"]
    df[cols] = df["paths"].str.split("/", expand=True)
    del df["dot"]
    logger.warning(f"Dataframe created with {len(df)} rows.")
    return df

# -------------------------------------------------------
# Register views from RAW parquet (example)
# -------------------------------------------------------
def reg_dss_source_folder_df(conn, *, data_src: str = "", show_ui: bool = False):
    progress_bar = None
    status_text = None

    try:
        if show_ui:
            progress_text = "Registering Dataiku Source Folders"
            progress_bar = st.progress(0, text=progress_text)
            status_text = st.empty()

        # Load DataFrame from Dataiku folder
        logger.warning("Registering Dataiku Source Folders...")
        if data_src == "partitioned_data":
            dss_src_fld_df = build_partitioned_data_df()
            table_name = "folder_partitions"
        elif data_src == "gold_tables":
            dss_src_fld_df = build_gold_tables_df()
            table_name = "gold_tables"
        else:
            logger.exception(f"Invalid data type: {data}")
            raise
            
        if dss_src_fld_df.empty:
            logger.warning("Dataiku Source Folder DataFrame is empty. Skipping table creation.")
            if show_ui:
                status_text.text("No Dataiku Source Folder data found — skipping")
                progress_bar.progress(100, text=progress_text)
            raise

        if show_ui:
            status_text.text(f"Loading {len(dss_src_fld_df)} sources")

        # Register DataFrame as an in-memory view for SQL
        conn.register("df_view", dss_src_fld_df)

        # Materialize as DuckDB table
        conn.execute(f"""
            CREATE OR REPLACE TABLE {table_name} AS
            SELECT * FROM df_view
        ;""")

        logger.warning(f"Materialized table `{table_name}` with {len(dss_src_fld_df)} rows.")

        if show_ui:
            progress_bar.progress(100, text=progress_text)
            status_text.text(f"`{table_name}` ready ({len(dss_src_fld_df)} rows)")

        return True

    except Exception as e:
        logger.exception(f"Failed to register data source: {data_src} -- {e}")
        raise
    
    finally:
        if progress_bar is not None:
            progress_bar.empty()
        if status_text is not None:
            status_text.empty()
    return