import logging
from pathlib import Path

import duckdb
import streamlit as st

from pulse_duckdb.settings import DB_PATH
from pulse_duckdb.engine import storage_config

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# DuckDB Reset
# -------------------------------------------------------------------
def reset_duckdb():
    # Delete old DuckDB file if present
    if DB_PATH.exists():
        try:
            DB_PATH.unlink()
            logger.warning(f"Deleted existing DuckDB file: {DB_PATH}")
        except Exception as e:
            logger.exception(f"Failed to delete DuckDB file: {DB_PATH}")
            raise e
    
    # Ensure the directory exists
    DB_DIR = DB_PATH.parent
    try:
        DB_DIR.mkdir(parents=True, exist_ok=True)
        logger.warning(f"DuckDB directory ready: {DB_DIR}")
    except Exception as e:
        logger.exception(f"Failed to create DuckDB directory: {DB_DIR}")
        raise e
    return

# -------------------------------------------------------------------
# DuckDB initialization
# -------------------------------------------------------------------
def create_connection(*, read_only: bool, show_ui: bool = False):
    progress_bar = None
    status_text = None

    try:
        if show_ui:
            progress_text = "Creating Local DuckDB"
            progress_bar = st.progress(0, text=progress_text)
            status_text = st.empty()

        logger.warning("Initializing DuckDB connection...")
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)

        conn = duckdb.connect(str(DB_PATH), read_only=read_only)
        storage_config.configure_storage(conn)

        logger.warning(f"DuckDB connected at: {DB_PATH}")
        
        if show_ui:
            progress = int(1 / 1 * 100)
            progress_bar.progress(progress, text=progress_text)
            status_text.text(f"DuckDB Created")

        return conn

    except Exception as e:
        logger.exception(f"Failed to initialize DuckDB connection. {e}")
        raise

    finally:
        if progress_bar is not None:
            progress_bar.empty()
        if status_text is not None:
            status_text.empty()