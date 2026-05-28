import logging
import os
import platform
import tempfile
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
    """Ensure DuckDB `httpfs` is available.

    Strategy:
    1) Always attempt the standard internet-backed `INSTALL httpfs; LOAD httpfs`.
       This supports environments where outbound access is allowed.
    2) If that fails (no internet, blocked, or non-writable default dirs), fall back to a
       bundled `httpfs.duckdb_extension` shipped under the plugin `resource/` directory.
    3) If the bundled file isn't present on disk (common in some containerized runs),
       optionally download it from a URL specified via environment variable and then load.

    Environment variables:
    - PULSE_DUCKDB_HTTPFS_BUNDLE_URL: full URL to `httpfs.duckdb_extension` (optional)
    - PULSE_DUCKDB_HTTPFS_BUNDLE_TIMEOUT_SECONDS: download timeout (optional, default 30)
    """

    # Only bundle linux_amd64 for now
    if platform.system().lower() != "linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        return

    duckdb_version = duckdb.__version__

    logger.info(
        "Configuring DuckDB extensions (duckdb=%s, os=%s, arch=%s)",
        duckdb_version,
        platform.system(),
        platform.machine(),
    )

    # Make online INSTALL work in containers by using a writable directory.
    tmp_ext_dir = Path(tempfile.gettempdir()) / "duckdb" / "extensions"
    try:
        tmp_ext_dir.mkdir(parents=True, exist_ok=True)
        conn.execute(f"SET extension_directory='{tmp_ext_dir.as_posix()}'")
    except Exception:
        # If we can't set/create, don't block; proceed and let INSTALL attempt.
        logger.debug("Failed to set extension_directory to /tmp", exc_info=True)

    # 1) Standard method (internet-backed)
    try:
        conn.execute("INSTALL httpfs")
        conn.execute("LOAD httpfs")
        logger.info("DuckDB httpfs loaded via INSTALL/LOAD")
        return
    except Exception:
        logger.warning("DuckDB INSTALL/LOAD httpfs failed; trying bundled fallback", exc_info=True)

    # 2) Bundled fallback in plugin resources
    # DuckDB appends `v<version>/<platform>` under `extension_directory`.
    # So we set `extension_directory` to the root and store files under
    # `duckdb_extensions/vX.Y.Z/linux_amd64/`.
    ext_root = (BASE_DIR / ".." / "resource" / "duckdb_extensions").resolve()
    ext_file = ext_root / f"v{duckdb_version}" / "linux_amd64" / "httpfs.duckdb_extension"

    logger.info("Checking bundled httpfs at %s (exists=%s)", ext_file.as_posix(), ext_file.exists())

    if ext_file.exists():
        # Prefer direct path load (least sensitive to extension_directory behavior)
        try:
            conn.execute(f"LOAD '{ext_file.as_posix()}'")
            logger.info("DuckDB httpfs loaded from bundled file")
            return
        except Exception:
            logger.warning("Failed to LOAD bundled httpfs by path; trying directory-based load", exc_info=True)

        conn.execute(f"SET extension_directory='{ext_root.as_posix()}'")
        conn.execute("LOAD httpfs")
        logger.info("DuckDB httpfs loaded from bundled extension_directory")
        return

    # 3) Optional URL download fallback (for containers missing plugin resources)
    bundle_url = os.environ.get("PULSE_DUCKDB_HTTPFS_BUNDLE_URL")
    if bundle_url:
        timeout_seconds = int(os.environ.get("PULSE_DUCKDB_HTTPFS_BUNDLE_TIMEOUT_SECONDS", "30"))
        target_file = (
            Path(tempfile.gettempdir())
            / "duckdb_extensions"
            / f"v{duckdb_version}"
            / "linux_amd64"
            / "httpfs.duckdb_extension"
        )
        target_file.parent.mkdir(parents=True, exist_ok=True)

        logger.info("Downloading bundled httpfs from %s to %s", bundle_url, target_file.as_posix())

        try:
            import urllib.request
            from urllib.parse import urlparse

            parsed_url = urlparse(bundle_url)
            if parsed_url.scheme not in {"https"} or not parsed_url.netloc:
                raise ValueError(
                    "PULSE_DUCKDB_HTTPFS_BUNDLE_URL must be an https URL"
                )

            with urllib.request.urlopen(bundle_url, timeout=timeout_seconds) as resp:  # nosec B310
                target_file.write_bytes(resp.read())

            conn.execute(f"LOAD '{target_file.as_posix()}'")
            logger.info("DuckDB httpfs loaded from downloaded bundle")
            return
        except Exception as exc:
            logger.exception("Failed to download/load httpfs from PULSE_DUCKDB_HTTPFS_BUNDLE_URL")
            raise RuntimeError(
                "DuckDB httpfs extension not available. Online INSTALL failed and bundled resource file "
                "was not found; URL download fallback also failed."
            ) from exc

    raise FileNotFoundError(
        "DuckDB httpfs extension not available. Online INSTALL failed and bundled resource file not found. "
        "Ensure `resource/duckdb_extensions/v<duckdb_version>/linux_amd64/httpfs.duckdb_extension` is packaged "
        "with the plugin, or set PULSE_DUCKDB_HTTPFS_BUNDLE_URL for containerized runs."
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