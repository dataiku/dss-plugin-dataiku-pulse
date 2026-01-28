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
def build_partition_df():
    """
    Builds a DataFrame of partitions from a Dataiku folder-like object.
    Returns:
        pd.DataFrame with columns:
        - layer
        - category
        - module
        - instance_name
        - date (converted to datetime)
    """
    logger.warning("Building partition dataframe from Dataiku folder...")
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
    logger.warning(f"Partition dataframe created with {len(df)} rows.")
    return df

# -------------------------------------------------------
# Register views from RAW parquet (example)
# -------------------------------------------------------
def register_partition_df(conn, *, show_ui: bool = False):
    """
    Loads the Dataiku folder partitions, registers them as a DuckDB view,
    and materializes them as a physical table.
    """
    progress_bar = None
    status_text = None

    try:
        if show_ui:
            progress_text = "Registering folder partitions"
            progress_bar = st.progress(0, text=progress_text)
            status_text = st.empty()

        logger.warning("Registering Dataiku Folder Partitioned DataFrame...")


        # Load DataFrame from Dataiku folder
        partitioned_df = build_partition_df()
        if partitioned_df.empty:
            logger.warning("Partition DataFrame is empty. Skipping table creation.")
            if show_ui:
                status_text.text("No partitions found — skipping")
                progress_bar.progress(100, text=progress_text)
            return False

        if show_ui:
            status_text.text(f"Loaded {len(partitioned_df)} partitions")

        # Register DataFrame as an in-memory view for SQL
        conn.register("df_view", partitioned_df)

        # Materialize as DuckDB table
        table_name = "folder_partitions"
        conn.execute(f"""
            CREATE OR REPLACE TABLE {table_name} AS
            SELECT * FROM df_view
        ;""")

        logger.warning(f"Materialized table `{table_name}` with {len(partitioned_df)} rows.")

        if show_ui:
            progress_bar.progress(100, text=progress_text)
            status_text.text(f"`{table_name}` ready ({len(partitioned_df)} rows)")

        return True

    except Exception as e:
        logger.exception(f"Failed to register folder_partitions. {e}")
        raise
    
    finally:
        if progress_bar is not None:
            progress_bar.empty()
        if status_text is not None:
            status_text.empty()
    return