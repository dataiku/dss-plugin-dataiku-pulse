import logging
from pathlib import Path
import streamlit as st
from pulse_duckdb import settings
from pulse_duckdb.helpers import yaml_loader

logger = logging.getLogger(__name__)

root = Path(settings.BASE_DIR / "pulse_duckdb/config/gold_tables")
queries = {}

for path in root.rglob("*.yaml"):
    queries |= yaml_loader.load_yaml(path)


# -------------------------------------------------------
# Register GOLD tables (FULL ONLY)
# -------------------------------------------------------
def register_gold_tables(conn, *, show_ui: bool = False):
    """
    Registers GOLD tables using full_sql only.
    GOLD tables are treated as fully rebuildable artifacts.
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

        logger.warning("Registering GOLD tables (full rebuild only)...")

        total = len(queries)
        if total == 0:
            logger.warning("No GOLD tables defined.")
            if show_ui:
                status_text.text("No GOLD tables to register")
                progress_bar.progress(100, text=progress_text)
            return False

        for idx, (table_name, cfg) in enumerate(queries.items(), start=1):
            current_table = table_name

            full_sql = cfg.get("full_sql")
            legacy_sql = cfg.get("sql")

            if show_ui:
                status_text.text(f"Registering GOLD table {idx}/{total}: {table_name}")
                progress_bar.progress(int(idx / total * 100), text=progress_text)

            if full_sql:
                current_sql = full_sql
                logger.warning(f"Full rebuild for `{table_name}`")
            elif legacy_sql:
                current_sql = legacy_sql
                logger.warning(f"Legacy rebuild for `{table_name}`")
            else:
                raise ValueError(f"No `full_sql` defined for GOLD table `{table_name}`")

            logger.debug(
                f"Executing GOLD table query for `{table_name}`:\n{current_sql}"
            )

            conn.execute(current_sql)

        logger.warning("GOLD tables registration complete.")

        if show_ui:
            status_text.text("GOLD tables registered successfully")
            progress_bar.progress(100, text=progress_text)

        return True

    except Exception:
        logger.exception(f"Failed to build GOLD table `{current_table}`")
        if current_sql:
            logger.exception(current_sql)
        raise

    finally:
        if progress_bar:
            progress_bar.empty()
        if status_text:
            status_text.empty()
