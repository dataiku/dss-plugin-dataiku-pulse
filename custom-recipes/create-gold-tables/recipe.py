from __future__ import annotations

import io
import logging
import os
import re
from pathlib import Path
import uuid
from datetime import datetime, timezone
import getpass

import duckdb
import dataiku
import yaml
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



def _slug(value: str) -> str:
    s = str(value or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def _load_dev_toolbox_modules(base_dir: Path) -> list[str]:
    """Load development-activity modules from YAML.

    Expected file: gold_specs/dataiku_dev_tools/toolbox.yaml
    """

    path = base_dir / "dataiku_dev_tools" / "toolbox.yaml"
    if not path.exists():
        return []

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"Invalid toolbox.yaml (expected YAML list): {path}")

    out: list[str] = []
    seen: set[str] = set()
    for v in raw:
        if v is None:
            continue
        s = _slug(str(v))
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _load_category_to_capability(base_dir: Path) -> list[dict]:
    """Load mapping rows for `dim_category_to_capability`.

    Expected file: gold_specs/dataiku_dev_tools/category_to_capability.yaml
    """

    path = base_dir / "dataiku_dev_tools" / "category_to_capability.yaml"
    if not path.exists():
        return []

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"Invalid category_to_capability.yaml (expected YAML list): {path}")

    rows: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if not item.get("dataiku_category") or not item.get("capability"):
            continue
        row = dict(item)
        row["dataiku_category"] = _slug(str(row["dataiku_category"]))
        row["capability"] = _slug(str(row["capability"]))
        # Defaults
        row.setdefault("capability_order", 1)
        row.setdefault("category_order", 1)
        row.setdefault("capability_display_name", str(item.get("capability_display_name") or row["capability"]))
        row.setdefault("category_display_name", str(item.get("category_display_name") or row["dataiku_category"]))
        row.setdefault("is_dev_activity", True)
        rows.append(row)

    return rows


def _build_dim_category_to_capability(conn: duckdb.DuckDBPyConnection, *, base_dir: Path) -> str:
    """Build `dim_category_to_capability` from YAML mapping."""

    rows = [r for r in _load_category_to_capability(base_dir) if _slug(str(r.get("capability") or "")) != "uncategorized"]

    conn.execute(
        """
        CREATE OR REPLACE TABLE dim_category_to_capability AS
        SELECT
          CAST(NULL AS VARCHAR) AS dataiku_category,
          CAST(NULL AS VARCHAR) AS capability,
          CAST(NULL AS INTEGER) AS capability_order,
          CAST(NULL AS INTEGER) AS category_order,
          CAST(NULL AS VARCHAR) AS capability_display_name,
          CAST(NULL AS VARCHAR) AS category_display_name,
          CAST(NULL AS BOOLEAN) AS is_dev_activity
        WHERE 1=0;
        """.strip()
    )

    if not rows:
        return "dim_category_to_capability"

    insert_rows = [
        (
            r.get("dataiku_category"),
            r.get("capability"),
            int(r.get("capability_order") or 1),
            int(r.get("category_order") or 1),
            r.get("capability_display_name"),
            r.get("category_display_name"),
            bool(r.get("is_dev_activity")),
        )
        for r in rows
    ]

    conn.executemany(
        """
        INSERT INTO dim_category_to_capability (
          dataiku_category,
          capability,
          capability_order,
          category_order,
          capability_display_name,
          category_display_name,
          is_dev_activity
        ) VALUES (?, ?, ?, ?, ?, ?, ?);
        """.strip(),
        insert_rows,
    )

    return "dim_category_to_capability"


def _create_event_mapping_module_view(
    conn: duckdb.DuckDBPyConnection,
    *,
    ctx,
    module: str,
    view_name: str,
) -> str:
    """Create a DuckDB view over SILVER event_mapping parquet for one module.

    Note: This intentionally does not rely on `create_silver_view()` because that
    helper currently assumes S3-only paths.
    """

    if not ctx.bucket_or_container:
        raise ValueError("Missing bucket/container")

    root = ctx.folder_root.strip("/")
    if root:
        root = f"{root}/"

    # Partitioned SILVER path:
    # silver/category=event_mapping/module=<module>/instance_name=*/year=*/month=*/day=*/*.parquet
    if ctx.connection_type == "EC2":
        base = f"s3://{ctx.bucket_or_container}/{root}silver"
    elif ctx.connection_type == "Azure":
        base = f"az://{ctx.bucket_or_container}/{root}silver"
    elif ctx.connection_type == "GCS":
        base = f"gs://{ctx.bucket_or_container}/{root}silver"
    else:
        raise ValueError(f"Unsupported connection type: {ctx.connection_type}")

    glob = f"{base}/category=event_mapping/module={module}/instance_name=*/year=*/month=*/day=*/*.parquet"

    sql = f"""
    CREATE OR REPLACE VIEW {view_name} AS
    SELECT
      *,
      make_date(CAST(year AS INTEGER), CAST(month AS INTEGER), CAST(day AS INTEGER)) AS partition_date
    FROM read_parquet('{glob}', hive_partitioning = true);
    """.strip()

    try:
        conn.execute(sql)
    except duckdb.IOException:
        # No parquet yet for this module; skip.
        return ""

    return view_name


def _build_fact_dev_activity_events(
    conn: duckdb.DuckDBPyConnection,
    *,
    ctx,
    base_dir: Path,
) -> str:
    """Build `fact_dev_activity_events` from selected event_mapping modules."""

    modules = _load_dev_toolbox_modules(base_dir)
    if not modules:
        return ""

    branches: list[str] = []

    for mod in modules:
        view_name = f"v_event_mapping__{_slug(mod)}"
        created = _create_event_mapping_module_view(conn, ctx=ctx, module=mod, view_name=view_name)
        if not created:
            continue

        branches.append(
            f"""
            SELECT
              try_cast(COALESCE(timestamp, date) AS TIMESTAMP) AS timestamp,
              instance_name,
              COALESCE(authuser, user) AS login,
              msgtype,
              msgtypebase,
              dataiku_category,
              project_key,
              severity,
              topic,
              audittopic,
              callpath,
              extras,
              try_cast(run_ts AS TIMESTAMP) AS run_timestamp,
              CAST(year AS INTEGER) AS year,
              CAST(month AS INTEGER) AS month,
              CAST(day AS INTEGER) AS day
            FROM {view_name}
            """.strip()
        )

    if not branches:
        return ""

    sql = (
        "CREATE OR REPLACE TABLE fact_dev_activity_events AS\n"
        + "\nUNION ALL\n".join(branches)
        + ";"
    )

    conn.execute(sql)
    return "fact_dev_activity_events"


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

        # Build GOLD tables from spec files.
        #
        # Current scope: project/instance metadata specs.
        # (Audit tables follow a different pattern and will be handled separately.)
        # Locate gold specs from the installed python-lib package.
        #
        # In DSS, custom recipe code is executed from a job folder (as an inlined script),
        # so `__file__` does not point to the plugin checkout. Deriving paths from the
        # imported package is stable.
        import data_collection.pulse_duckdb.gold_builder as gold_builder_module

        gold_builder_path = Path(gold_builder_module.__file__).resolve()
        base_dir = gold_builder_path.parent / "gold_specs"
        spec_paths = sorted(
            list((base_dir / "project").glob("base_*_history.yaml"))
            + list((base_dir / "instance").glob("base_*_history.yaml"))
        )

        for spec_path in spec_paths:
            spec = load_gold_spec(spec_path)

            # Ensure the upstream SILVER view exists.
            if spec.category and spec.module:
                view_name = spec.view_table_name or f"v_{spec.category}__{spec.module}"
                created_view = create_silver_view(
                    conn=setup.conn,
                    ctx=ctx,
                    category=spec.category,
                    module=spec.module,
                    view_name=view_name,
                )
                if not created_view:
                    # No data available yet for this spec.
                    continue

            apply_gold_spec(setup.conn, spec)

        # Build development activity dimension + event fact table.
        #
        # Both are configured in `gold_specs/dataiku_dev_tools/*`.
        dim_name = _build_dim_category_to_capability(setup.conn, base_dir=base_dir)
        fact_name = _build_fact_dev_activity_events(setup.conn, ctx=ctx, base_dir=base_dir)

        # Build the products registry mapping table.
        #
        # This is a static mapping (YAML -> table) shipped with the plugin. It does
        # not depend on whether the customer has actually collected a given product
        # type yet.
        products_specs_dir = base_dir / "dataiku_products"
        registry_columns = [
            "product_type",
            "source_table",
            "instance_name_col",
            "project_key_col",
            "key_col",
            "name_col",
            "subtype_col",
            "owner_col",
            "last_modified_by_col",
            "created_at_col",
            "updated_at_col",
            "where_sql",
            "spec_file",
        ]

        setup.conn.execute(
            """
            CREATE OR REPLACE TABLE base_dataiku_products_registry AS
            SELECT
              CAST(NULL AS VARCHAR) AS product_type,
              CAST(NULL AS VARCHAR) AS source_table,
              CAST(NULL AS VARCHAR) AS instance_name_col,
              CAST(NULL AS VARCHAR) AS project_key_col,
              CAST(NULL AS VARCHAR) AS key_col,
              CAST(NULL AS VARCHAR) AS name_col,
              CAST(NULL AS VARCHAR) AS subtype_col,
              CAST(NULL AS VARCHAR) AS owner_col,
              CAST(NULL AS VARCHAR) AS last_modified_by_col,
              CAST(NULL AS VARCHAR) AS created_at_col,
              CAST(NULL AS VARCHAR) AS updated_at_col,
              CAST(NULL AS VARCHAR) AS where_sql,
              CAST(NULL AS VARCHAR) AS spec_file
            WHERE 1=0;
            """.strip()
        )

        if products_specs_dir.exists():
            import yaml

            seen_product_types: set[str] = set()
            registry_rows: list[tuple] = []

            for path in sorted(products_specs_dir.glob("*.yaml")):
                payload = yaml.safe_load(path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError(f"Invalid product registry YAML (expected mapping): {path}")

                product_type = str(payload.get("product_type") or "").strip()
                if not product_type:
                    raise ValueError(f"Missing product_type in {path}")

                if product_type in seen_product_types:
                    raise ValueError(f"Duplicate product_type={product_type!r} in registry YAMLs")
                seen_product_types.add(product_type)

                row = tuple(
                    (payload.get(c) if c != "spec_file" else path.name)
                    for c in registry_columns
                )
                registry_rows.append(row)

            if registry_rows:
                placeholders = ",".join(["?"] * len(registry_columns))
                setup.conn.executemany(
                    f"INSERT INTO base_dataiku_products_registry ({', '.join(registry_columns)}) VALUES ({placeholders});",
                    registry_rows,
                )

        # Unload curated GOLD tables.
        #
        # - `base_*`: existing contract
        # - `fact_*`: event-level facts (may be partitioned)
        base_tables = [
            name
            for (name,) in setup.conn.sql("SHOW TABLES").fetchall()
            if name.startswith("base_")
        ]

        dim_tables = [
            name
            for (name,) in setup.conn.sql("SHOW TABLES").fetchall()
            if name.startswith("dim_")
        ]

        fact_tables = [
            name
            for (name,) in setup.conn.sql("SHOW TABLES").fetchall()
            if name.startswith("fact_")
        ]

        unloaded_tables = (
            base_tables
            + [t for t in dim_tables if t not in base_tables]
            + [t for t in fact_tables if t not in base_tables and t not in dim_tables]
        )

        for table_name in unloaded_tables:
            # Partition large event facts by instance+day to keep queries fast.
            if table_name == "fact_dev_activity_events":
                destination = f"gold/{table_name}"
            else:
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

                    if table_name == "fact_dev_activity_events":
                        # Write partitioned parquet for efficient downstream reads.
                        query = (
                            "COPY (\n"
                            "  SELECT\n"
                            "    timestamp,\n"
                            "    instance_name,\n"
                            "    login,\n"
                            "    msgtype,\n"
                            "    msgtypebase,\n"
                            "    dataiku_category,\n"
                            "    project_key,\n"
                            "    severity,\n"
                            "    topic,\n"
                            "    audittopic,\n"
                            "    callpath,\n"
                            "    extras,\n"
                            "    run_timestamp,\n"
                            "    year,\n"
                            "    month,\n"
                            "    day\n"
                            "  FROM fact_dev_activity_events\n"
                            ") TO '{path}' (\n"
                            "  FORMAT 'PARQUET',\n"
                            "  OVERWRITE TRUE,\n"
                            "  PARTITION_BY (instance_name, year, month, day)\n"
                            ");"
                        )
                    else:
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
                    if table_name == "fact_dev_activity_events":
                        raise ValueError(
                            "unload_behavior='dataiku' is not supported for partitioned fact_dev_activity_events; "
                            "use unload_behavior='duckdb'"
                        )
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
        "unloaded_tables": unloaded_tables,
    }


run()
