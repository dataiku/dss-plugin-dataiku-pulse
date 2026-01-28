import logging
from pathlib import Path
import streamlit as st
from backend import settings
from backend.utils import yaml_loader

logger = logging.getLogger(__name__)
root = Path(settings.BASE_DIR / "backend/config/gold_tables")
queries = {}
for path in root.rglob("*.yaml"):
    queries |= yaml_loader.load_yaml(path)


def table_exists(conn, table_name: str) -> bool:
    return conn.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_schema = 'main'
          AND table_name = ?
        """,
        [table_name]
    ).fetchone()[0] == 1


# -------------------------------------------------------
# Register GOLD tables
# -------------------------------------------------------
def register_gold_tables(conn, *, show_ui: bool = False, force_full: bool = False):
    """
    Registers GOLD tables using full or incremental SQL
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
            full_sql = cfg.get("full_sql")
            incremental_sql = cfg.get("incremental_sql")
            legacy_sql = cfg.get("sql")

            if show_ui:
                status_text.text(f"Registering GOLD table {idx}/{total}")
                progress_bar.progress(int(idx / total * 100), text=progress_text)

            if force_full and full_sql:
                current_sql = full_sql
                logger.info(f"Force full rebuild for `{table_name}`")
            elif full_sql and incremental_sql:
                base_table = f"{table_name.replace('_table', '')}_base"
                if table_exists(conn, base_table):
                    current_sql = incremental_sql
                    logger.info(f"Incremental rebuild for `{table_name}`")
                else:
                    current_sql = full_sql
                    logger.info(f"Bootstrap full rebuild for `{table_name}`")
            elif legacy_sql:
                current_sql = legacy_sql
                logger.info(f"Legacy rebuild for `{table_name}`")
            else:
                raise ValueError(f"No SQL defined for GOLD table `{table_name}`")

            logger.debug(f"Executing GOLD table query for `{table_name}`:\n{current_sql}")
            conn.execute(current_sql)

        logger.info("GOLD tables registration complete.")

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
