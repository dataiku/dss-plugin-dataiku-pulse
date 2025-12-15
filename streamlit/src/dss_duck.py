# ./src/dss_duck.py
# -----------------------------------------------------------------------------
# Modules
import streamlit as st
import logging
import tempfile
import os
from filelock import FileLock, Timeout
import datetime, time
from src.duckdb import funcs


# -----------------------------------------------------------------------------
## Logger
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.ERROR)
logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Initiate DB
def initiate_db():
    try:
        with tempfile.NamedTemporaryFile(delete=False) as temp_lock_file:
            lock_path = temp_lock_file.name
        lock = FileLock(lock_path, timeout=900)
        with lock:
            start_time = time.perf_counter()
            progress_text = "Setting up Dataiku PULSE Insights Database. Please wait......"
            progress_bar = st.progress(0, text=progress_text)
            status_text = st.empty()
            # partition df
            partition_df = funcs.build_partition_df()
            st.session_state["partition_df"] = partition_df
            # functions
            init_funcs = [
                funcs.create_duckdb,
                funcs.load_base_tables,
                funcs.load_dataiku_usage,
                funcs.load_additional_tables
            ]
            total_funcs = len(init_funcs)
            for i, func in enumerate(init_funcs, start=1):
                try:
                    func_name = func.__name__
                    logger.warn(f" - Running {func_name}")
                    if func_name in ["load_base_tables","load_dataiku_usage"]:
                        r = func(partition_df)
                    else:
                        r = func()
                except Exception as e:
                    logger.error(e)
                    r = False
                    raise Exception(f"Failed in function {func_name}. Check Logs.")
                if not r:
                    raise Exception(f"Failed in function {func_name}. Check Logs.")
                progress = int(i / total_funcs * 100)
                progress_bar.progress(progress, text=progress_text)
                status_text.text(f"Completed step {i}/{total_funcs}")
                time.sleep(1)
            end_time = time.perf_counter()
            elapsed_time = end_time - start_time
            st.success(f"Dataiku PULSE Insights data loaded Successful!! ({elapsed_time:.6f} seconds)")
        os.remove(lock_path)
    except Timeout:
        logger.error("Could not acquire lock. Another process is using the resource.")
        st.warning("Could not acquire lock. Another process is using the resource.")
        return
    except Exception as e:
        logger.error(f"An error occurred: {e}")
        if os.path.exists(lock_path):
            os.remove(lock_path)
    return