import logging

import streamlit as st

from pulse_duckdb import settings

logger = logging.getLogger(__name__)

def load_gold_tables(conn, *, show_ui: bool = False) -> bool:
    progress_bar = None
    status_text = None

    success_tables = []
    failed_tables = []

    try:
        if show_ui:
            progress_text = "Registering Dataiku GOLD tables"
            progress_bar = st.progress(0, text=progress_text)
            status_text = st.empty()

        logger.info("Registering Dataiku GOLD tables")

        tables_df = conn.execute("SELECT * FROM gold_tables").df()

        if tables_df.empty:
            logger.info("No GOLD tables found to register")
            if show_ui:
                status_text.text("No GOLD tables to register")
                progress_bar.progress(100, text=progress_text)
            return False

        total = len(tables_df)
        logger.info(f"Found {total} GOLD tables to register")

        for idx, row in enumerate(tables_df.itertuples(index=False), start=1):
            path = getattr(row, "paths").lstrip("/")
            table_name = getattr(row, "gold_table").replace(".parquet", "")

            # Skip non-base tables
            if not table_name.endswith("_base"):
                logger.debug(f"Skipping non-base GOLD table: {table_name}")
                continue

            parquet_path = (
                f"{settings.blob_header}://"
                f"{settings.blob_bket}/"
                f"{settings.dss_gold_tables_folder_root}/"
                f"{path}"
            )

            if show_ui:
                status_text.text(
                    f"Registering GOLD table {idx}/{total}: {table_name}"
                )
                progress_bar.progress(int(idx / total * 100), text=progress_text)

            query = (
                f"CREATE OR REPLACE TABLE {table_name} AS "  # nosec
                f"SELECT * FROM read_parquet('{parquet_path}')"
            )

            try:
                logger.debug(f"Creating GOLD table '{table_name}' from {parquet_path}")
                conn.execute(query)
                success_tables.append(table_name)

            except Exception:
                logger.exception(f"Failed to create GOLD table '{table_name}' from {parquet_path}")
                failed_tables.append(table_name)
                continue

        # --- Summary ---
        logger.info(f"GOLD table registration complete: {len(success_tables)} succeeded, {len(failed_tables)} failed")
        if show_ui:
            if failed_tables:
                status_text.text(
                    f"GOLD tables loaded with errors "
                    f"({len(success_tables)} ok, {len(failed_tables)} failed)"
                )
            else:
                status_text.text("All GOLD tables registered successfully")

            progress_bar.progress(100, text=progress_text)

        # Return True if at least one table succeeded
        return bool(success_tables)

    finally:
        if progress_bar is not None:
            progress_bar.empty()
        if status_text is not None:
            status_text.empty()
    return