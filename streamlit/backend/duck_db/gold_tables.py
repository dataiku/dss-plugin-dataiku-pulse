import logging
from pathlib import Path
import streamlit as st
from backend import settings
from backend.utils import yaml_loader

logger = logging.getLogger(__name__)
root = Path(settings.BASE_DIR / "backend/config/gold_tables")
queries = {}
for path in root.rglob("*.yaml"):
    queries = queries | yaml_loader.load_yaml(path)

# -------------------------------------------------------
# Register views from RAW parquet
# -------------------------------------------------------
def register_gold_tables(conn, *, show_ui: bool = False):
    """
    Registers GOLD tables from gold_tables.yaml
    """
    progress_bar = None
    status_text = None
    current_table = None
    current_sql = None

    try:
        if show_ui:
            progress_text = "Registering GOLD tables"
            progress_bar = st.progress(0, text=progress_text)
            status_text = st.empty()

        logger.info("Registering GOLD tables...")

        total = len(queries)
        if total == 0:
            logger.warning("No GOLD tables defined.")
            if show_ui:
                status_text.text("No GOLD tables to register")
                progress_bar.progress(100, text=progress_text)
            return False

        for idx, (table_name, cfg) in enumerate(queries.items(), start=1):
            current_table = table_name
            current_sql = cfg.get("sql")

            if not current_sql:
                raise ValueError(f"No SQL defined for GOLD table `{table_name}`")

            if show_ui:
                status_text.text(f"Registering GOLD table {idx}/{total}: {table_name}")
                progress_bar.progress(int(idx / total * 100), text=progress_text)

            logger.debug(f"Executing GOLD table query for `{table_name}`:\n{current_sql}")
            conn.execute(current_sql)

        logger.info("GOLD tables registration complete.")

        if show_ui:
            status_text.text("GOLD tables registered successfully")
            progress_bar.progress(100, text=progress_text)

        return True

    except Exception:
        logger.exception(
            f"Failed to build GOLD table `{current_table}`"
        )
        if current_sql:
            logger.exception(current_sql)
        raise

    finally:
        if progress_bar is not None:
            progress_bar.empty()
        if status_text is not None:
            status_text.empty()
    return