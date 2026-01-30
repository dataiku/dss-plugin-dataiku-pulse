import logging
import streamlit as st
from backend import settings
from backend.utils import yaml_loader

logger = logging.getLogger(__name__)
queries = yaml_loader.load_yaml(settings.BASE_DIR / "backend/config/gold_tables/base_query.yaml")

def load_gold_tables(conn, *, show_ui: bool = False):
    progress_bar = None
    status_text = None

    try:
        if show_ui:
            progress_text = "Registering Dataiku Usage views"
            progress_bar = st.progress(0, text=progress_text)
            status_text = st.empty()

        # 1.
        logger.warning("Registering Dataiku Gold Tables...")

        tables_df = conn.execute(
            "SELECT * FROM gold_tables;"
        ).df()

        if tables_df.empty:
            logger.warning("No category/module combinations found.")
            if show_ui:
                status_text.text("No Gold Table to register")
                progress_bar.progress(100, text=progress_text)
            return False

        # 2.
        raw_queries = []
        for row in tables_df.itertuples():
            path = getattr(row, "paths").lstrip('/')
            table_name = getattr(row, "gold_table").replace(".parquet", "")
            parquet_path = (
                f"{settings.blob_header}://"
                f"{settings.blob_bket}/"
                f"{settings.dss_gold_tables_folder_root}/"
                f"{path}"
            )
            q = (
                f"CREATE OR REPLACE VIEW {table_name} AS "
                f"SELECT * "
                f"FROM read_parquet('{parquet_path}'); "
            )
            raw_queries.append(q)

        # 3.
        logger.warning(f"Number of Gold Table queries: {len(raw_queries)}")
        
        if show_ui:
            status_text.text(f"Found {len(raw_queries)} Gold Table")

        total = len(raw_queries)
        for idx, q in enumerate(raw_queries, start=1):
            if show_ui:
                status_text.text(f"Registering Gold Table {idx}/{total}")
                progress_bar.progress(int(idx / total * 100), text=progress_text)

            logger.debug(f"Executing view registration query:\n{q}")
            conn.execute(q)

        logger.warning("Gold Table registration complete.")

        if show_ui:
            status_text.text("Gold Table registered successfully")
            progress_bar.progress(100, text=progress_text)

        return True

    except Exception as e:
        logger.exception(f"Failed to build Gold Table. {e}")
        raise

    finally:
        if progress_bar is not None:
            progress_bar.empty()
        if status_text is not None:
            status_text.empty()
    return