import logging
import shutil
from pathlib import Path

import pandas as pd
import streamlit as st

from pulse_duckdb import settings
from pulse_duckdb.engine import query

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# Create Dataiku Partitioned DataFrame
# -------------------------------------------------------------------
def build_partitioned_data_df() -> pd.DataFrame:
    logger.info("Building DataFrame from Dataiku partitioned data folder")
    partitions = settings.dss_partitioned_folder.list_partitions()
    if not partitions:
        logger.info("No partitions found in Dataiku partitioned folder")
        return pd.DataFrame(
            columns=["partitions", "layer", "category", "module", "instance_name"]
        )
    # Build DF
    df = pd.DataFrame(partitions, columns=["partitions"])
    cols = ["layer", "category", "module", "instance_name"]
    df[cols] = df["partitions"].str.split("|", expand=True)
    # Filter to silver layer only
    df = df.loc[df["layer"].isin(["silver"])].reset_index(drop=True)
    # Normalize partition values
    df["category"] = df["category"].str.replace("category=", "", regex=False)
    df["module"] = df["module"].str.replace("module=", "", regex=False)
    df["instance_name"] = df["instance_name"].str.replace("instance_name=", "", regex=False)
    return df


def build_gold_tables_df() -> pd.DataFrame:
    logger.info("Building DataFrame from Dataiku GOLD tables folder")
    paths = settings.dss_gold_tables_folder.list_paths_in_partition()
    if not paths:
        logger.info("No GOLD tables found in Dataiku folder")
        return pd.DataFrame(columns=["layer", "gold_table"])
    # Build DF
    df = pd.DataFrame(paths, columns=["paths"])
    cols = ["dot", "layer", "gold_table"]
    df[cols] = df["paths"].str.split("/", expand=True)
    df = df.drop(columns=["dot"])
    return df


# -------------------------------------------------------
# Register views from RAW parquet (example)
# -------------------------------------------------------
def reg_dss_source_folder_df(conn, *, data_src: str, show_ui: bool = False) -> bool:
    progress_bar = None
    status_text = None

    SOURCE_REGISTRY = {
        "partitioned_data": (build_partitioned_data_df, "folder_partitions"),
        "gold_tables": (build_gold_tables_df, "gold_tables"),
        # Future:
        # "customer_parquet": (build_customer_parquet_df, "customer_parquet_files"),
    }

    try:
        if show_ui:
            progress_text = "Registering Dataiku Source Folders"
            progress_bar = st.progress(0, text=progress_text)
            status_text = st.empty()

        logger.info(f"Registering Dataiku source folder: {data_src}")

        if data_src not in SOURCE_REGISTRY:
            raise ValueError(f"Invalid data source type: {data_src}")

        build_fn, table_name = SOURCE_REGISTRY[data_src]
        dss_src_fld_df = build_fn()

        if dss_src_fld_df.empty:
            logger.info(f"Dataiku source folder '{data_src}' returned no rows; skipping table creation")
            if show_ui:
                status_text.text("No source data found — skipping")
                progress_bar.progress(100, text=progress_text)
            return False

        logger.info(f"GOLD tables DataFrame created with {len(dss_src_fld_df)} rows")
        if show_ui:
            status_text.text(f"Loading {len(dss_src_fld_df)} sources")

        conn.register("df_view", dss_src_fld_df)

        safe_table = '"' + table_name.replace('"', '""') + '"'

        conn.execute(f"""  # nosec
            CREATE OR REPLACE TABLE {safe_table} AS
            SELECT * FROM df_view
        """)

        logger.info(f"Materialized table '{table_name}' with {len(dss_src_fld_df)} rows")

        if show_ui:
            progress_bar.progress(100, text=progress_text)
            status_text.text(f"`{table_name}` ready ({len(dss_src_fld_df)} rows)")

        return True

    except Exception:
        logger.exception(f"Failed to register data source: {data_src}")
        raise
    finally:
        if progress_bar is not None:
            progress_bar.empty()
        if status_text is not None:
            status_text.empty()

    return