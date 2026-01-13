import logging
import streamlit as st
from backend import settings
from backend.utils import yaml_loader

logger = logging.getLogger(__name__)
queries = yaml_loader.load_yaml(settings.BASE_DIR / "backend/config/raw_views.yaml")

# -------------------------------------------------------
# Register views from RAW parquet
# -------------------------------------------------------
def register_raw_views(conn, *, show_ui: bool = False):
    """
    Registers RAW parquet views from blob storage.
    """
    progress_bar = None
    status_text = None

    try:
        if show_ui:
            progress_text = "Registering RAW views"
            progress_bar = st.progress(0, text=progress_text)
            status_text = st.empty()

        logger.warning("Registering RAW views...")
        
        tables_df = conn.execute(
            "SELECT DISTINCT category, module "
            "FROM folder_partitions "
            "WHERE category != 'dataiku_usage';"
        ).df()

        if tables_df.empty:
            logger.warning("No category/module combinations found.")
            if show_ui:
                status_text.text("No RAW views to register")
                progress_bar.progress(100, text=progress_text)
            return False

        raw_queries = []
        for row in tables_df.itertuples():
            category = getattr(row, "category")
            module = getattr(row, "module")
            table_name = f"{category}_{module}_view"
            
            paths = [
                f"{settings.blob_header}://{settings.blob_bket}/"
                f"{settings.blob_root}/silver/{category}/{module}/**/*.parquet"
            ]

            raw_queries.append(
                yaml_loader.render_query(
                    queries["raw_view"],
                    table_name = table_name,
                    paths = paths,
                )
            )

        logger.warning(f"Number of RAW view queries: {len(raw_queries)}")

        if show_ui:
            status_text.text(f"Found {len(raw_queries)} RAW views")

        total = len(raw_queries)
        for idx, q in enumerate(raw_queries, start=1):
            if show_ui:
                status_text.text(f"Registering RAW view {idx}/{total}")
                progress_bar.progress(int(idx / total * 100), text=progress_text)

            logger.debug(f"Executing view registration query:\n{q}")
            conn.execute(q)

        logger.warning("RAW view registration complete.")

        if show_ui:
            status_text.text("RAW views registered successfully")
            progress_bar.progress(100, text=progress_text)

        return True

    except Exception as e:
        logger.exception(f"Failed to build raw views. {e}")
        raise

    finally:
        if progress_bar is not None:
            progress_bar.empty()
        if status_text is not None:
            status_text.empty()
    return