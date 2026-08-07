import dataiku

from pathlib import Path

from data_collection.helper import ensure_managed_folder
from data_collection.pulse_duckdb.context import build_storage_context
from data_collection.pulse_duckdb.destinations import gold_destination_for_table
from data_collection.pulse_duckdb.dev_activity import load_dev_toolbox_modules
from data_collection.pulse_duckdb.diagnostics import log_pre_unload_debug
from data_collection.pulse_duckdb.dimensions import build_dim_addon_feature_flags
from data_collection.pulse_duckdb.duckdb_manager import prepare_duckdb
from data_collection.pulse_duckdb.engine.storage_config import configure_storage
from data_collection.pulse_duckdb.gold_builder import apply_gold_spec, load_gold_spec
from data_collection.pulse_duckdb.license_wide import build_license_wide_sql_params
from data_collection.pulse_duckdb.manifest import read_manifest, set_manifest_watermark, stamp_manifest_updated_at, write_manifest
from data_collection.pulse_duckdb.unload import unload_gold_tables
from data_collection.pulse_duckdb.object_activity import (
    _create_event_mapping_module_view,
    build_fact_object_activity_events,
    load_object_activity_modules,
)
from data_collection.pulse_duckdb.products_registry import build_base_dataiku_products_registry
from data_collection.pulse_duckdb.table_groups import group_gold_tables_by_prefix
from data_collection.pulse_duckdb.table_inventory import list_table_names
from data_collection.pulse_duckdb.user_activity import build_fact_formal_mau_daily, build_fact_user_activity_daily, build_fact_user_activity_project_daily, collect_user_activity_quality_report
from data_collection.views import create_silver_view
from data_finalize import resolve_gold_folder_lookup
from dataiku.customrecipe import get_recipe_config


def run():
    project_key = dataiku.default_project_key()
    gold_folder_lookup = resolve_gold_folder_lookup()

    recipe_config = get_recipe_config() or {}
    unload_behavior = recipe_config.get("unload_behavior", "duckdb")
    build_dev_activity = bool(recipe_config.get("build_dev_activity", True))
    build_object_activity = bool(recipe_config.get("build_object_activity", True))
    manifest_enabled = bool(recipe_config.get("incremental_enabled", True))
    lookback_days = int(recipe_config.get("lookback_days", 3) or 3)

    ensure_managed_folder(project_key=project_key, folder_lookup="partitioned_data")
    ensure_managed_folder(project_key=project_key, folder_lookup=gold_folder_lookup)

    silver_ctx = build_storage_context(project_key=project_key, folder_lookup="partitioned_data")
    gold_ctx = build_storage_context(project_key=project_key, folder_lookup=gold_folder_lookup)

    setup = prepare_duckdb(ctx=silver_ctx, read_only=False, reset=True)
    configure_storage(setup.conn, ctx=gold_ctx)

    import data_collection.pulse_duckdb.gold_builder as gold_builder_module

    gold_builder_path = Path(gold_builder_module.__file__).resolve()
    base_dir = gold_builder_path.parent / "gold_specs"

    spec_paths = sorted(
        list((base_dir / "project").glob("base_*.yaml"))
        + list((base_dir / "instance").glob("base_*.yaml"))
    )

    built_specs = []
    for spec_path in spec_paths:
        sql_params = (
            build_license_wide_sql_params(base_dir / "instance")
            if spec_path.name == "base_license_limits_wide_latest.yaml"
            else None
        )
        spec = load_gold_spec(spec_path, sql_params=sql_params)

        if spec.category and spec.module:
            view_name = spec.view_table_name or f"v_{spec.category}__{spec.module}"
            created_view, skip_reason = create_silver_view(
                conn=setup.conn,
                ctx=silver_ctx,
                category=spec.category,
                module=spec.module,
                view_name=view_name,
            )
            if not created_view:
                continue

        apply_gold_spec(setup.conn, spec)
        built_specs.append(spec.name)

    current_tables = {name for name in built_specs}
    built_dimensions = []
    if "base_license_addon_licenses_latest" in current_tables:
        built_dimensions.append(build_dim_addon_feature_flags(setup.conn))

    built_user_activity = [
        name
        for name in [
            build_fact_user_activity_daily(setup.conn, ctx=silver_ctx),
            build_fact_user_activity_project_daily(setup.conn, ctx=silver_ctx),
            build_fact_formal_mau_daily(setup.conn, ctx=silver_ctx),
        ]
        if name
    ]

    user_activity_quality = collect_user_activity_quality_report(setup.conn)

    built_object_activity = []
    if build_object_activity:
        object_activity_name = build_fact_object_activity_events(setup.conn, ctx=silver_ctx, base_dir=base_dir)
        if object_activity_name:
            built_object_activity.append(object_activity_name)

    built_products_registry = build_base_dataiku_products_registry(setup.conn, base_dir=base_dir)

    manifest = read_manifest(gold_folder_lookup) if manifest_enabled else {}

    current_tables = list_table_names(setup.conn)
    table_groups = group_gold_tables_by_prefix(current_tables)
    base_tables = table_groups["base_tables"]
    dim_tables = table_groups["dim_tables"]
    agg_tables = table_groups["agg_tables"]
    fact_tables = table_groups["fact_tables"]
    unload_candidates = base_tables + dim_tables + agg_tables + fact_tables
    unload_destinations = {table_name: gold_destination_for_table(table_name) for table_name in unload_candidates}

    log_pre_unload_debug(
        setup.conn,
        gold_ctx=gold_ctx,
        table_names=[
            "fact_dev_activity_events",
            "fact_object_activity_events",
            "fact_user_activity_daily",
            "fact_user_activity_project_daily",
        ],
    )

    if unload_behavior == "duckdb":
        dev_modules = load_dev_toolbox_modules(base_dir)
        object_modules = load_object_activity_modules(base_dir)

        for module_name in set(dev_modules + object_modules):
            _create_event_mapping_module_view(
                setup.conn,
                ctx=silver_ctx,
                module=module_name,
                view_name=f"v_event_mapping__{module_name}",
            )

        if build_dev_activity and dev_modules:
            max_ts = (
                setup.conn.execute(
                    "SELECT CAST(MAX(run_timestamp) AS VARCHAR) FROM fact_dev_activity_events;"
                ).fetchone()[0]
                if "fact_dev_activity_events" in fact_tables
                else None
            )
            set_manifest_watermark(manifest, "fact_dev_activity_events", max_ts)

        if build_object_activity and object_modules:
            max_ts = (
                setup.conn.execute(
                    "SELECT CAST(MAX(run_timestamp) AS VARCHAR) FROM fact_object_activity_events;"
                ).fetchone()[0]
                if "fact_object_activity_events" in fact_tables
                else None
            )
            set_manifest_watermark(manifest, "fact_object_activity_events", max_ts)

    unloaded_tables, failed_tables = unload_gold_tables(
        setup.conn,
        gold_ctx=gold_ctx,
        gold_folder_lookup=gold_folder_lookup,
        table_names=unload_candidates,
        unload_behavior=unload_behavior,
    )

    if manifest_enabled:
        stamp_manifest_updated_at(manifest)
        write_manifest(gold_folder_lookup, manifest)

    return {
        "source_project_key": project_key,
        "silver_folder_lookup": silver_ctx.folder_lookup,
        "gold_folder_lookup": gold_ctx.folder_lookup,
        "gold_output_folder": gold_folder_lookup,
        "connection_type": silver_ctx.connection_type,
        "connection_name": silver_ctx.connection_name,
        "unload_behavior": unload_behavior,
        "build_dev_activity": build_dev_activity,
        "build_object_activity": build_object_activity,
        "manifest_enabled": manifest_enabled,
        "lookback_days": lookback_days,
        "duckdb_connection_ready": setup.conn is not None,
        "gold_specs_dir": str(base_dir),
        "built_specs": built_specs,
        "built_dimensions": built_dimensions,
        "built_user_activity": built_user_activity,
        "user_activity_quality": user_activity_quality,
        "built_object_activity": built_object_activity,
        "built_products_registry": built_products_registry,
        "current_tables": current_tables,
        "base_tables": base_tables,
        "dim_tables": dim_tables,
        "agg_tables": agg_tables,
        "fact_tables": fact_tables,
        "unload_candidates": unload_candidates,
        "unload_destinations": unload_destinations,
        "unloaded_tables": unloaded_tables,
        "failed_tables": failed_tables,
        "ok": len(failed_tables) == 0,
        "manifest": manifest,
    }


if __name__ == "__main__":
    run()
