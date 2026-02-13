import logging
import os
import tempfile
import time

import streamlit as st
from filelock import FileLock, Timeout

from pulse_duckdb.engine import (
    create_conn,
    expand_duckdb,
    dataiku_sources,
    load_gold_tables,
)

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# Initialize loop
# -------------------------------------------------------------------
def initialize_database():
    STEP_LABELS = {
        "create_connection": "Initializing DuckDB",
        "configure_duckdb_runtime": "Configure DuckDB Runtime Procs",
        "reg_dss_source_folder_df": "Registering Dataiku Source Folder",
        "load_gold_tables": "Load Gold Tables",
    }
    init_funcs = [
        create_conn.create_connection,
        expand_duckdb.configure_duckdb_runtime,
        dataiku_sources.reg_dss_source_folder_df,
        load_gold_tables.load_gold_tables,
    ]
    success_message="Dataiku PULSE Insights database fully rebuilt"

    create_conn.reset_duckdb()

    try:
        # Create lock and run duckdb init functions required
        with tempfile.NamedTemporaryFile(delete=False) as temp_lock_file:
            lock_path = temp_lock_file.name
        lock = FileLock(lock_path, timeout=900)

        with lock:
            start_time = time.perf_counter()
            progress_text = "Setting up Dataiku PULSE Insights Database. Please wait......"
            progress_bar = st.progress(0, text=progress_text)
            status_text = st.empty()

            conn = None
            try:
                total_funcs = len(init_funcs)
                for i, func in enumerate(init_funcs, start=1):
                    func_name = func.__name__
                    label = STEP_LABELS.get(func_name, func_name)
                    status_text.text(f"Running step {i}/{total_funcs}: {label}")
                    logger.warning(f"Running DuckDB init function {func_name}")
                    if func_name == "create_connection":
                        conn = func(read_only=False, show_ui=True)
                    elif func_name == "reg_dss_source_folder_df":
                        func(conn, data_src="partitioned_data", show_ui=True)
                        func(conn, data_src="gold_tables", show_ui=True)
                    else:
                        func(conn, show_ui=True)
                    progress = int(i / total_funcs * 100)
                    progress_bar.progress(progress, text=progress_text)
                    status_text.text(f"Completed step {i}/{total_funcs}: {label}")
                    time.sleep(1)
                elapsed = time.perf_counter() - start_time
                st.success(f"{success_message} ({elapsed:.2f}s)")
                logger.warning(f"{success_message} ({elapsed:.2f}s)")
            
            except Exception as e:
                logger.exception(f"DuckDB init function {func_name} :: e")
                raise

            finally:
                if conn is not None:
                    conn.close()
                    logger.warning("Write-mode DuckDB connection closed.")

    except Timeout:
        st.warning("Could not acquire lock. Another process is using the resource.")
        logger.error("Lock acquisition timeout")
        return

    finally:
        if "lock_path" in locals():
            os.remove(lock_path)
    return