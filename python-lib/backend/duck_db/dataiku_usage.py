import logging
import streamlit as st
from backend import settings
from backend.utils import yaml_loader

logger = logging.getLogger(__name__)
queries = yaml_loader.load_yaml(settings.BASE_DIR / "backend/config/raw_views.yaml")

# --------------------------------------------------------------------------------------------
def register_dataiku_usage_views(conn, *, show_ui: bool = False):
    """
    Registers the canonical Dataiku Usage RAW and semantic views from blob storage.
    """
    progress_bar = None
    status_text = None

    try:
        if show_ui:
            progress_text = "Registering Dataiku Usage views"
            progress_bar = st.progress(0, text=progress_text)
            status_text = st.empty()

        # -------------------------------------------------
        # 1. RAW usage events view (physical unification)
        # -------------------------------------------------
        logger.warning("Registering Dataiku Usage RAW view...")
        parquet_path = (
            f"{settings.blob_header}://"
            f"{settings.blob_bket}/"
            f"{settings.blob_root}/silver/dataiku_usage/**/*.parquet"
        )
        q_raw = yaml_loader.render_query(
            queries["usage_events_raw"],
            parquet_path = parquet_path,
        )
        logger.debug(f"Executing view registration query:\n{q_raw}")
        conn.execute(q_raw)
        logger.warning("Dataiku Usage RAW view registration complete.")

        # -------------------------------------------------
        # 2. Semantic usage view (capability mapping)
        # -------------------------------------------------
        logger.warning("Registering Dataiku Usage semantic view...")
        q_semantic = queries["usage_events"]
        logger.debug(f"Executing semantic view registration query:\n{q_semantic}")
        conn.execute(q_semantic)
        logger.warning("Dataiku Usage semantic view registration complete.")
    
        if show_ui:
            status_text.text("Dataiku Usage views registered successfully")
            progress_bar.progress(100, text=progress_text)
        return True

    except Exception as e:
        logger.exception(f"Failed to build Dataiku Usage views. {e}")
        raise

    finally:
        if progress_bar is not None:
            progress_bar.empty()
        if status_text is not None:
            status_text.empty()
