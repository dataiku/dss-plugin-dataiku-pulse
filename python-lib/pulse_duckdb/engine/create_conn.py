import logging
import os
import platform
from pathlib import Path

import duckdb
import streamlit as st

from pulse_duckdb.settings import BASE_DIR, DB_PATH
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
def _configure_extensions(conn) -> None:
    """Load DuckDB extensions bundled with the plugin.

    This avoids `INSTALL` calls that would reach out to the internet,
    which fails for customers behind a firewall.
    """

    # Only bundle linux_amd64 for now
    if platform.system().lower() != "linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        return

    duckdb_version = duckdb.__version__

    # DuckDB appends `v<version>/<platform>` under `extension_directory`.
    # So we set `extension_directory` to the root and store files under
    # `duckdb_extensions/vX.Y.Z/linux_amd64/`.
    ext_roots = [
        # Dev Streamlit runs from repo
        (BASE_DIR / ".." / "streamlit" / "resource" / "duckdb_extensions").resolve(),
        # DSS plugin resource root
        (BASE_DIR / ".." / "resource" / "duckdb_extensions").resolve(),
    ]

    for ext_root in ext_roots:
        ext_file = (
            ext_root
            / f"v{duckdb_version}"
            / "linux_amd64"
            / "httpfs.duckdb_extension"
        )

        if not ext_file.exists():
            continue

        conn.execute(f"SET extension_directory='{ext_root.as_posix()}'")
        try:
            conn.execute("LOAD httpfs")
        except Exception:
            # Fallback: direct path load (works even if DuckDB doesn't use our directory)
            conn.execute(f"LOAD '{ext_file.as_posix()}'")

        return

    raise FileNotFoundError(
        "Bundled DuckDB httpfs extension not found in plugin resources. "
        "Ensure httpfs.duckdb_extension is packaged under "
        "streamlit/resource/duckdb_extensions/v<duckdb_version>/linux_amd64/ or "
        "resource/duckdb_extensions/v<duckdb_version>/linux_amd64/."
    )


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

        try:
            _configure_extensions(conn)
        except Exception as exc:
            logger.exception("Failed to load bundled DuckDB extensions")
            raise

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