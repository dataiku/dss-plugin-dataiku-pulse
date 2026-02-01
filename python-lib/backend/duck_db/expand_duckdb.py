import os
import logging

logger = logging.getLogger(__name__)

def configure_duckdb_runtime(conn, *, show_ui: bool = False, memory_pct: float = 0.8):
    """
    Configure DuckDB to use 80% of total system memory
    and (CPU cores - 1), leaving 1 core free for the OS / Streamlit.
    """

    # --- Get total system memory (Linux / Docker) ---
    mem_total_bytes = None
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mem_total_bytes = int(line.split()[1]) * 1024
                    break
    except Exception:
        logger.warning("Unable to read /proc/meminfo; DuckDB memory not configured")

    # --- Calculate DuckDB memory ---
    if mem_total_bytes:
        duckdb_mem_bytes = int(mem_total_bytes * memory_pct)
        duckdb_mem_gb = max(1, duckdb_mem_bytes // (1024 ** 3))
        conn.execute(f"PRAGMA memory_limit='{duckdb_mem_gb}GB'")
        logger.warning(f"DuckDB memory_limit set to {duckdb_mem_gb}GB")
    else:
        logger.warning("DuckDB memory_limit not set")

    # --- Calculate DuckDB threads ---
    cpu_count = os.cpu_count() or 1
    duckdb_threads = max(1, cpu_count - 1)

    conn.execute(f"PRAGMA threads={duckdb_threads}")
    logger.warning(f"DuckDB threads set to {duckdb_threads} (of {cpu_count} total CPUs)")

    # --- Temp spill location ---
    conn.execute("PRAGMA temp_directory='/tmp/duckdb'")
    logger.warning("DuckDB temp_directory set to /tmp/duckdb")
    return