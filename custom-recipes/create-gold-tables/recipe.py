from __future__ import annotations

import io
import logging
import os
from pathlib import Path
import uuid
from datetime import datetime, timezone
import getpass

import dataiku
from dataiku.customrecipe import get_output_names_for_role, get_recipe_config


def _resolve_gold_folder_lookup() -> str:
    """Resolve GOLD output folder lookup.

    In normal DSS recipe runs, this comes from the output role `gold_tables_folder`.

    For local/debug runs (outside the DSS recipe harness), set
    `PULSE_GOLD_DEBUG_LOOKUP` to a managed folder name (ex: `gold_data`) and the
    recipe will use that value.
    """

    debug_lookup = os.environ.get("PULSE_GOLD_DEBUG_LOOKUP")
    if debug_lookup:
        return debug_lookup

    try:
        out_names = get_output_names_for_role("gold_tables_folder")
    except Exception:
        out_names = []

    if out_names:
        return out_names[0]

    raise ValueError(
        "Missing output managed folder for role 'gold_tables_folder' "
        "(or set PULSE_GOLD_DEBUG_LOOKUP for local runs)"
    )

from data_collection.helper.dss_folder_writer import ensure_managed_folder
from data_collection.pulse_duckdb.context import build_storage_context
from data_collection.pulse_duckdb.duckdb_manager import prepare_duckdb
from data_collection.pulse_duckdb.gold_builder import apply_gold_spec, load_gold_spec
from data_collection.pulse_duckdb.views import create_silver_view


logger = logging.getLogger(__name__)


def _cleanup_stale_duckdb_files(*, base_dir: Path, max_age_hours: float = 24.0) -> None:
    """Remove old per-run DuckDB files (best-effort)."""

    try:
        if not base_dir.exists():
            return
        cutoff = datetime.now(timezone.utc).timestamp() - (max_age_hours * 3600)
        for p in base_dir.glob("pulse_*.duckdb"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except Exception:
                continue
    except Exception:
        return


def _unique_duckdb_path(*, project_key: str) -> Path:
    """Build a unique DuckDB path per run/user.

    Uses `PULSE_DUCKDB_DIR` as the parent directory if set, otherwise defaults to
    `/tmp/duckdb`.
    """

    base_dir = Path(os.environ.get("PULSE_DUCKDB_DIR", "/tmp/duckdb"))
    base_dir.mkdir(parents=True, exist_ok=True)

    # Cleanup old runs so /tmp doesn't accumulate forever.
    _cleanup_stale_duckdb_files(base_dir=base_dir)

    user = os.environ.get("DKU_CURRENT_USER") or getpass.getuser()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    token = uuid.uuid4().hex[:8]
    filename = f"pulse_{project_key}_{user}_{ts}_{token}.duckdb"
    filename = filename.replace("/", "_")
    return base_dir / filename


def run() -> dict:
    # Recipe output folder is used as the GOLD destination.
    # (Later steps will unload parquet here.)
    gold_folder_lookup = _resolve_gold_folder_lookup()

    # Resolve source storage from the DATA_COLLECTION partitioned_data managed folder.
    project_key = os.environ.get("PULSE_SOURCE_PROJECT_KEY", "DATA_COLLECTION")
    ensure_managed_folder(
        project_key=project_key,
        folder_lookup="partitioned_data",
    )
    ctx = build_storage_context(project_key=project_key, folder_lookup="partitioned_data")

    # 4. Custom edits
    recipe_config = get_recipe_config() or {}

    unload_behavior = recipe_config.get("unload_behavior", "duckdb")

    # The output managed folder is the GOLD destination.
    # Dataiku recipe helpers usually return a managed folder id.
    ensure_managed_folder(
        project_key=dataiku.default_project_key(),
        folder_lookup=gold_folder_lookup,
    )
    gold_ctx = build_storage_context(project_key=dataiku.default_project_key(), folder_lookup=gold_folder_lookup)

    # For now: always reset before build (keeps it deterministic).
    # Use a unique per-run DuckDB file to avoid cross-user permission issues.
    setup = prepare_duckdb(
        ctx=ctx,
        read_only=False,
        reset=True,
        db_path=_unique_duckdb_path(project_key=ctx.project_key),
    )

    failed_tables: list[str] = []
    base_tables: list[str] = []
    storage_info: dict = {}

    # Mirror legacy `settings.py` blob header mapping.
    if ctx.connection_type == "EC2":
        blob_header = "s3"
    elif ctx.connection_type == "Azure":
        blob_header = "az"
    elif ctx.connection_type == "GCS":
        blob_header = "gs"
    else:
        raise ValueError(f"Unsupported connection type: {ctx.connection_type}")

    # This recipe assumes GOLD and `partitioned_data` share the same connection.
    if gold_ctx.connection_name != ctx.connection_name or gold_ctx.connection_type != ctx.connection_type:
        raise ValueError(
            "GOLD output folder must share the same backing connection as `partitioned_data` "
            f"(silver: {ctx.connection_name}/{ctx.connection_type}, gold: {gold_ctx.connection_name}/{gold_ctx.connection_type})"
        )

    try:
        storage_info = {
            "provider": setup.provider,
            "credential_mode": setup.credential_mode,
            "db_path": str(setup.db_path),
        }

        # Create views needed for scenario build.
        create_silver_view(
            conn=setup.conn,
            ctx=ctx,
            category="scenarios",
            module="project_metadata",
            view_name="v_scenarios__project_metadata",
        )

        # Build scenario tables from gold specs.
        spec_dir = Path(
            __file__
        ).resolve().parents[2] / "python-lib/data_collection/pulse_duckdb/gold_specs/base"

        for spec_path in [
            spec_dir / "base_scenarios_project_metadata_history.yaml",
            spec_dir / "base_scenarios_project_metadata_latest.yaml",
        ]:
            spec = load_gold_spec(spec_path)
            apply_gold_spec(setup.conn, spec)

        # Unload `base_*` tables.
        base_tables = [
            name
            for (name,) in setup.conn.sql("SHOW TABLES").fetchall()
            if name.startswith("base_")
        ]


        for table_name in base_tables:
            destination = f"gold/{table_name}.parquet"
            logger.info("Unloading %s to %s...", table_name, destination)

            if unload_behavior == "duckdb":
                try:
                    # Build blob URL to write into the GOLD managed folder location.
                    # This mirrors the legacy `settings.py` approach.
                    root = gold_ctx.folder_root.strip("/")
                    if root:
                        root = f"{root}/"

                    if not gold_ctx.bucket_or_container:
                        raise ValueError("Could not resolve GOLD bucket/container")

                    path = f"{blob_header}://{gold_ctx.bucket_or_container}/{root}{destination}"
                    query = f"COPY {table_name} TO '{path}' (FORMAT 'PARQUET', OVERWRITE TRUE);"
                    logger.debug(query)
                    setup.conn.execute(query)
                except Exception as e:
                    logger.error("Failed to unload %s: %s", table_name, e)
                    failed_tables.append(table_name)

            elif unload_behavior == "dataiku":
                try:
                    unload_df = setup.conn.execute(f"SELECT * FROM {table_name};").df()
                    buf = io.BytesIO()
                    unload_df.to_parquet(buf, compression="gzip", engine="pyarrow", index=False)
                    buf.seek(0)
                    content = buf.read()

                    folder = dataiku.Folder(gold_folder_lookup)
                    folder.upload_stream(destination, content)
                except Exception as e:
                    logger.error("Dataiku unload failed for %s: %s", table_name, e)
                    failed_tables.append(table_name)

            else:
                raise ValueError(f"Unknown unload behavior: {unload_behavior!r}")

    finally:
        setup.conn.close()

    return {
        "ok": len(failed_tables) == 0,
        "failed_tables": failed_tables,
        "source_project_key": project_key,
        "connection_type": ctx.connection_type,
        "connection_name": ctx.connection_name,
        **storage_info,
        "gold_output_folder": gold_folder_lookup,
        "unload_behavior": unload_behavior,
        "unloaded_tables": base_tables,
    }


run()
