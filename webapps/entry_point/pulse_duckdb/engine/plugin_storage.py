"""Shared storage helpers (webapp uses plugin implementation).

This module intentionally reuses the plugin's data-collection code so:
- blob provider setup is identical between nightly recipe and webapp
- no drift in credential handling across S3/Azure/GCS

Mode A: fail fast if plugin libs are missing.
"""

from __future__ import annotations

import functools

import dataiku
import duckdb

# These imports come from the plugin python-lib (pulse_plugin ensures sys.path).
# In the repo checkout, they live under `data_collection.*`.
# In the workspace-managed plugin checkout, they live under `pulse_duckdb.*`.
try:
    from data_collection.pulse_duckdb.context import build_storage_context
    from data_collection.pulse_duckdb.engine.storage_config import configure_storage
except Exception:  # pragma: no cover
    # Workspace-managed plugin checkout doesn't expose a storage context builder.
    # In that environment the webapp should use its own managed-folder reads.
    build_storage_context = None  # type: ignore
    configure_storage = None  # type: ignore

import settings


@functools.lru_cache(maxsize=1)
def _gold_storage_ctx():
    if build_storage_context is None:
        raise RuntimeError("build_storage_context unavailable (plugin python-lib not on path)")

    # Webapp reads GOLD from the current/default DSS project.
    return build_storage_context(
        project_key=dataiku.default_project_key(),
        folder_lookup=(settings.PULSE_GOLD_TABLES_FOLDER_ID or settings.PULSE_GOLD_TABLES_FOLDER_NAME),
    )


def configure_connection_for_gold(conn: duckdb.DuckDBPyConnection) -> dict:
    """Configure DuckDB connection to read from GOLD managed folder backing store.

    In demo/dev modes the webapp can run without plugin python-lib; in that case
    we treat this as a no-op.
    """

    if build_storage_context is None or configure_storage is None:
        return {"ok": True, "enabled": False, "reason": "plugin_pythonlib_missing"}

    ctx = _gold_storage_ctx()
    # configure_storage is callable when plugin python-lib is available.
    return configure_storage(conn, ctx=ctx)  # type: ignore[misc]


def gold_base_path() -> str:
    """Return blob base path for the GOLD managed folder.

    Returned value does not include a trailing slash.
    """

    ctx = _gold_storage_ctx()

    root = ctx.folder_root.strip("/")
    if ctx.connection_type == "EC2":
        return f"s3://{ctx.bucket_or_container}/{root}"
    if ctx.connection_type == "Azure":
        # DuckDB azure extension uses `azure://container@account/...`.
        info = ctx.connection_handle.get_info()
        storage_account = (info.get("params") or {}).get("storageAccount")
        return f"azure://{ctx.bucket_or_container}@{storage_account}/{root}"
    if ctx.connection_type == "GCS":
        return f"gs://{ctx.bucket_or_container}/{root}"

    raise ValueError(f"Unsupported connection type: {ctx.connection_type}")


def gold_blob_url(*, rel_path: str) -> str:
    """Return a blob URL for a managed-folder relative path."""

    return f"{gold_base_path()}/{rel_path.lstrip('/')}"


def gold_partitioned_glob(*, dataset: str) -> str:
    """Return a standard partitioned parquet glob for GOLD datasets.

    Example dataset: 'fact_dev_activity_events'
    """

    # Expected layout: gold/<dataset>/instance_name=*/year=*/month=*/day=*/*.parquet
    return f"{gold_base_path()}/gold/{dataset}/instance_name=*/year=*/month=*/day=*/*.parquet"
