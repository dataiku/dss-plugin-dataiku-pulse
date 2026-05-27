import logging
import tempfile
import time
import os
from filelock import FileLock, Timeout
import streamlit as st
from pulse_duckdb.engine import create_conn, expand_duckdb, raw_views, dataiku_usage, create_gold_tables
from pulse_duckdb.engine import dataiku_sources

logger = logging.getLogger(__name__)

STEP_LABELS = {
    "create_connection": "Initializing DuckDB",
    "configure_duckdb_runtime": "Configure DuckDB Runtime Procs",
    "register_partition_df": "Registering folder partitions",
    "register_raw_views": "Registering RAW views",
    "register_dataiku_usage_views": "Register Dataiku Usage Views",
    "register_gold_tables": "Building GOLD tables",
}

# -------------------------------------------------------
def _run_init_pipeline(init_funcs, *, reset=False, success_message="Initialization complete"):
    try:
        # Hard reset duckdb if needed
        create_conn.reset_duckdb(reset=reset)
        
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
            try:
                os.remove(lock_path)
            except FileNotFoundError:
                # `filelock` may already have removed the lock file
                pass
    return


def custom_recipe_create_gold_tables(reset: bool = False):
    init_funcs = [
        create_conn.create_connection,
        expand_duckdb.configure_duckdb_runtime,
        dataiku_sources.register_partition_df,
        raw_views.register_raw_views,
        dataiku_usage.register_dataiku_usage_views,
        create_gold_tables.register_gold_tables,
    ]

    # Run pipeline
    _run_init_pipeline(
        init_funcs,
        reset=reset,
        success_message="Dataiku PULSE Insights database fully rebuilt",
    )
    return


def streamlit_load_tables():
    init_funcs = [
        create_conn.create_connection,
        create_gold_tables.register_gold_tables,
    ]

    # Run pipeline
    _run_init_pipeline(
        init_funcs,
        reset=False,
        success_message="GOLD tables rebuilt successfully",
    )
    return