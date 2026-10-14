from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import dataiku
from dataiku.customrecipe import get_recipe_config

from data_collection.helper import ensure_managed_folder
from data_collection.pulse_duckdb.context import build_storage_context
from data_collection.pulse_duckdb.destinations import gold_destination_for_table
from data_collection.pulse_duckdb.dev_activity import (
    build_dim_category_to_capability,
    build_dim_dev_activity_event_classification,
    build_fact_dev_activity_events,
    load_dev_toolbox_modules,
)
from data_collection.pulse_duckdb.diagnostics import log_pre_unload_debug
from data_collection.pulse_duckdb.dimensions import build_dim_addon_feature_flags
from data_collection.pulse_duckdb.duckdb_manager import prepare_duckdb
from data_collection.pulse_duckdb.engine.storage_config import configure_storage
from data_collection.pulse_duckdb.gold_builder import apply_gold_spec, resolve_gold_spec_build_order
from data_collection.pulse_duckdb.license_wide import build_license_wide_sql_params
from data_collection.pulse_duckdb.manifest import copy_manifest, lookback_adjusted_watermark, manifest_watermark, read_manifest, stamp_manifest_updated_at, write_manifest
from data_collection.pulse_duckdb.sql_utils import log_timed_phase
from data_collection.pulse_duckdb.unload import unload_gold_tables_with_details
from data_collection.pulse_duckdb.object_activity import (
    _create_event_mapping_module_view,
    build_fact_object_activity_events,
    load_object_activity_modules,
)
from data_collection.pulse_duckdb.products_registry import build_base_dataiku_products_registry
from data_collection.pulse_duckdb.table_groups import group_gold_tables_by_prefix
from shared_duckdb.pathing import resolve_unique_db_path
from data_collection.pulse_duckdb.table_inventory import list_table_names
from data_collection.pulse_duckdb.user_activity import build_fact_formal_mau_daily, build_fact_user_activity_daily, build_fact_user_activity_project_daily, collect_user_activity_quality_report
from data_collection.pulse_duckdb.license_utilization import build_fact_license_utilization_daily, collect_license_utilization_quality_report
from data_collection.pulse_duckdb.views import create_silver_view
from shared_duckdb.create_conn import effective_memory_limit_bytes
from data_finalize import resolve_gold_folder_lookup
logger = logging.getLogger(__name__)

CREATE_GOLD_DUCKDB_MEMORY_PERCENTAGE = 0.40
CREATE_GOLD_DUCKDB_THREADS = 2
USER_ACTIVITY_INCREMENTAL_FACTS = {
    "fact_user_activity_daily",
    "fact_user_activity_project_daily",
}
REQUIRED_DASHBOARD_GOLD_TABLES = {
    "fact_user_activity_daily",
    "fact_user_activity_project_daily",
}
IMPACT_LABELS = {
    1: "Low",
    2: "Degraded",
    3: "Critical",
}


class CreateGoldBestEffortError(RuntimeError):
    def __init__(self, message: str, *, result: dict[str, Any]):
        super().__init__(message)
        self.result = result


def _impact_level_for_target(target: str | None) -> int:
    if target in REQUIRED_DASHBOARD_GOLD_TABLES:
        return 3
    if target and (
        target.startswith("base_")
        or target.startswith("dim_")
        or target.startswith("agg_")
        or target.startswith("fact_")
    ):
        return 2
    return 1


def _record_failure(
    failure_records: list[dict[str, Any]],
    *,
    stage: str,
    target: str,
    exc: BaseException | None = None,
    message: str | None = None,
    category: str | None = None,
    module: str | None = None,
    severity: int | None = None,
    impact: str | None = None,
    dependency: str | None = None,
) -> None:
    impact_level = severity or _impact_level_for_target(target)
    record: dict[str, Any] = {
        "stage": stage,
        "target": target,
        "error_type": type(exc).__name__ if exc is not None else None,
        "error_message": str(exc) if exc is not None else (message or ""),
        "severity": impact_level,
        "impact": impact or IMPACT_LABELS[impact_level],
    }
    if category:
        record["category"] = category
    if module:
        record["module"] = module
    if dependency:
        record["dependency"] = dependency
    failure_records.append(record)


def _failure_summary(failure_records: list[dict[str, Any]]) -> str:
    highest = max((int(record.get("severity") or 1) for record in failure_records), default=1)
    affected = ", ".join(str(record.get("target") or "unknown") for record in failure_records)
    plural = "failure" if len(failure_records) == 1 else "failures"
    return (
        f"GOLD completed best-effort with {len(failure_records)} {plural}; "
        f"highest impact: {highest} ({IMPACT_LABELS[highest]}); affected: {affected}"
    )


def _format_gib(memory_bytes: int) -> str | None:
    if memory_bytes <= 0:
        return None
    return f"{memory_bytes / (1024**3):.2f} GiB"


def _duckdb_memory_limit_setting(memory_bytes: int) -> str:
    return f"{memory_bytes}B"


def _apply_create_gold_duckdb_session_limits(conn) -> None:
    effective_memory_bytes, memory_source = effective_memory_limit_bytes()
    if effective_memory_bytes <= 0:
        conn.execute(f"SET threads = {CREATE_GOLD_DUCKDB_THREADS}")
        applied_memory_limit = conn.execute("SELECT current_setting('memory_limit')").fetchone()[0]
        applied_threads = conn.execute("SELECT current_setting('threads')").fetchone()[0]
        logger.warning(
            "Create GOLD DuckDB session memory cap not applied: effective_memory=%s memory_source=%s applied_memory_limit=%s threads=%s",
            _format_gib(effective_memory_bytes),
            memory_source,
            applied_memory_limit,
            applied_threads,
        )
        return

    memory_limit_bytes = max(1, int(effective_memory_bytes * CREATE_GOLD_DUCKDB_MEMORY_PERCENTAGE))
    memory_limit_setting = _duckdb_memory_limit_setting(memory_limit_bytes)
    conn.execute(f"SET memory_limit = '{memory_limit_setting}'")
    conn.execute(f"SET threads = {CREATE_GOLD_DUCKDB_THREADS}")

    applied_memory_limit = conn.execute("SELECT current_setting('memory_limit')").fetchone()[0]
    applied_threads = conn.execute("SELECT current_setting('threads')").fetchone()[0]
    logger.info(
        "Create GOLD DuckDB session limits applied: effective_memory=%s memory_source=%s duckdb_percentage=%s duckdb_memory_limit=%s duckdb_memory_limit_setting=%s applied_memory_limit=%s threads=%s",
        _format_gib(effective_memory_bytes),
        memory_source,
        int(CREATE_GOLD_DUCKDB_MEMORY_PERCENTAGE * 100),
        _format_gib(memory_limit_bytes),
        memory_limit_setting,
        applied_memory_limit,
        applied_threads,
    )


def _user_activity_build_watermark(
    *,
    manifest: dict[str, object] | None,
    table_name: str,
    incremental_enabled: bool,
    lookback_days: int,
) -> str | None:
    if not incremental_enabled:
        return None
    prior_watermark = manifest_watermark(manifest or {}, table_name)
    adjusted_watermark = lookback_adjusted_watermark(prior_watermark, lookback_days)
    if adjusted_watermark:
        logger.info(
            "GOLD user activity build boundary: table=%s prior_watermark=%s adjusted_watermark=%s lookback_days=%s mode=incremental",
            table_name,
            prior_watermark,
            adjusted_watermark,
            lookback_days,
        )
        return adjusted_watermark
    reason = "missing" if not prior_watermark else "unusable"
    logger.info(
        "GOLD user activity full build selected: table=%s reason=%s prior_watermark=%s",
        table_name,
        reason,
        prior_watermark,
    )
    return None


def run():
    project_key = dataiku.default_project_key()
    gold_folder_lookup = resolve_gold_folder_lookup()

    recipe_config = get_recipe_config() or {}
    unload_behavior = recipe_config.get("unload_behavior", "duckdb")
    manifest_enabled = bool(recipe_config.get("incremental_enabled", True))
    lookback_days = int(recipe_config.get("lookback_days", 3) or 3)

    ensure_managed_folder(project_key=project_key, folder_lookup="partitioned_data")
    ensure_managed_folder(project_key=project_key, folder_lookup=gold_folder_lookup)

    manifest = read_manifest(gold_folder_lookup) if manifest_enabled else {}
    pending_manifest = copy_manifest(manifest) if manifest_enabled else None

    silver_ctx = build_storage_context(project_key=project_key, folder_lookup="partitioned_data")
    gold_ctx = build_storage_context(project_key=project_key, folder_lookup=gold_folder_lookup)

    setup = prepare_duckdb(
        ctx=silver_ctx,
        read_only=False,
        reset=True,
        db_path=resolve_unique_db_path(project_key=project_key, purpose="recipe_gold_builder"),
    )
    _apply_create_gold_duckdb_session_limits(setup.conn)
    configure_storage(setup.conn, ctx=gold_ctx)

    import data_collection.pulse_duckdb.gold_builder as gold_builder_module

    gold_builder_path = Path(gold_builder_module.__file__).resolve()
    base_dir = gold_builder_path.parent / "gold_specs"

    spec_paths = sorted(
        list((base_dir / "project").glob("base_*.yaml"))
        + list((base_dir / "instance").glob("base_*.yaml"))
    )
    sql_params_by_name = {
        "base_license_limits_wide_latest.yaml": build_license_wide_sql_params(base_dir / "instance")
    }
    ordered_specs = resolve_gold_spec_build_order(
        spec_paths,
        sql_params_by_name=sql_params_by_name,
    )

    failure_records: list[dict[str, Any]] = []

    built_specs = []
    failed_outputs: set[str] = set()
    skipped_outputs: set[str] = set()
    for spec_path, spec in ordered_specs:
        failed_dependencies = [
            dependency for dependency in spec.depends_on if dependency in failed_outputs
        ]
        if failed_dependencies:
            reason = "blocked by failed dependency: " + ", ".join(failed_dependencies)
            logger.error("Skipping GOLD spec %s: %s", spec.name, reason)
            _record_failure(
                failure_records,
                stage="dependency_skip",
                target=spec.name,
                message=reason,
                dependency=", ".join(failed_dependencies),
            )
            skipped_outputs.add(spec.name)
            continue
        skipped_dependencies = [
            dependency
            for dependency in spec.depends_on
            if dependency in skipped_outputs
        ]
        if skipped_dependencies:
            logger.info(
                "Skipping GOLD spec %s: unavailable dependency had no SILVER data: %s",
                spec.name,
                ", ".join(skipped_dependencies),
            )
            skipped_outputs.add(spec.name)
            continue

        if spec.category and spec.module:
            view_name = spec.view_table_name or f"v_{spec.category}__{spec.module}"
            try:
                created_view, skip_reason = create_silver_view(
                    conn=setup.conn,
                    ctx=silver_ctx,
                    category=spec.category,
                    module=spec.module,
                    view_name=view_name,
                )
            except Exception as exc:
                logger.exception(
                    "Failed creating SILVER view for GOLD spec: spec=%s category=%s module=%s view=%s",
                    spec.name,
                    spec.category,
                    spec.module,
                    view_name,
                )
                _record_failure(
                    failure_records,
                    stage="silver_view",
                    target=spec.name,
                    exc=exc,
                    category=spec.category,
                    module=spec.module,
                )
                failed_outputs.add(spec.name)
                continue
            if not created_view:
                logger.info("Skipping GOLD spec %s: %s", spec.name, skip_reason)
                skipped_outputs.add(spec.name)
                continue

        try:
            if spec.name == "base_license_limits_wide_latest":
                with log_timed_phase(
                    setup.conn, label="apply_gold_spec.base_license_limits_wide_latest"
                ):
                    apply_gold_spec(setup.conn, spec)
            else:
                apply_gold_spec(setup.conn, spec)
            built_specs.append(spec.name)
        except Exception as exc:
            logger.exception(
                "Failed building GOLD spec: spec=%s path=%s", spec.name, spec_path
            )
            _record_failure(failure_records, stage="build", target=spec.name, exc=exc)
            failed_outputs.add(spec.name)

    current_tables = {name for name in built_specs}
    built_dimensions = []
    if "base_license_addon_licenses_latest" in failed_outputs:
        reason = "blocked by failed dependency: base_license_addon_licenses_latest"
        logger.error("Skipping dim_addon_feature_flags: %s", reason)
        _record_failure(
            failure_records,
            stage="dependency_skip",
            target="dim_addon_feature_flags",
            message=reason,
            dependency="base_license_addon_licenses_latest",
        )
        skipped_outputs.add("dim_addon_feature_flags")
    elif "base_license_addon_licenses_latest" in skipped_outputs:
        logger.info(
            "Skipping dim_addon_feature_flags: base_license_addon_licenses_latest had no SILVER data"
        )
        skipped_outputs.add("dim_addon_feature_flags")
    elif "base_license_addon_licenses_latest" in current_tables:
        try:
            with log_timed_phase(setup.conn, label="build_dim_addon_feature_flags"):
                built_dimensions.append(build_dim_addon_feature_flags(setup.conn))
        except Exception as exc:
            logger.exception("Failed building dim_addon_feature_flags")
            _record_failure(
                failure_records,
                stage="build",
                target="dim_addon_feature_flags",
                exc=exc,
            )
            failed_outputs.add("dim_addon_feature_flags")

    built_dev_activity = []
    dim_category_to_capability_ok = False
    try:
        with log_timed_phase(setup.conn, label="build_dim_category_to_capability"):
            built_dev_activity.append(
                build_dim_category_to_capability(setup.conn, base_dir=base_dir)
            )
        dim_category_to_capability_ok = True
    except Exception as exc:
        logger.exception("Failed building dim_category_to_capability")
        _record_failure(
            failure_records, stage="build", target="dim_category_to_capability", exc=exc
        )
        failed_outputs.add("dim_category_to_capability")

    try:
        with log_timed_phase(
            setup.conn, label="build_dim_dev_activity_event_classification"
        ):
            built_dev_activity.append(
                build_dim_dev_activity_event_classification(
                    setup.conn, base_dir=base_dir
                )
            )
    except Exception as exc:
        logger.exception("Failed building dim_dev_activity_event_classification")
        _record_failure(
            failure_records,
            stage="build",
            target="dim_dev_activity_event_classification",
            exc=exc,
        )
        failed_outputs.add("dim_dev_activity_event_classification")

    try:
        dev_activity_name = build_fact_dev_activity_events(
            setup.conn, ctx=silver_ctx, base_dir=base_dir
        )
        if dev_activity_name:
            built_dev_activity.append(dev_activity_name)
        else:
            skipped_outputs.add("fact_dev_activity_events")
    except Exception as exc:
        logger.exception("Failed building fact_dev_activity_events")
        _record_failure(
            failure_records, stage="build", target="fact_dev_activity_events", exc=exc
        )
        failed_outputs.add("fact_dev_activity_events")

    built_user_activity = []
    user_activity_build_watermarks = {
        table_name: _user_activity_build_watermark(
            manifest=manifest,
            table_name=table_name,
            incremental_enabled=manifest_enabled,
            lookback_days=lookback_days,
        )
        for table_name in USER_ACTIVITY_INCREMENTAL_FACTS
    }
    user_activity_builders = [
        (
            "fact_user_activity_daily",
            "build_fact_user_activity_daily",
            lambda: build_fact_user_activity_daily(
                setup.conn,
                ctx=silver_ctx,
                adjusted_watermark=user_activity_build_watermarks[
                    "fact_user_activity_daily"
                ],
            ),
        ),
        (
            "fact_user_activity_project_daily",
            "build_fact_user_activity_project_daily",
            lambda: build_fact_user_activity_project_daily(
                setup.conn,
                ctx=silver_ctx,
                adjusted_watermark=user_activity_build_watermarks[
                    "fact_user_activity_project_daily"
                ],
            ),
        ),
        (
            "fact_formal_mau_daily",
            "build_fact_formal_mau_daily",
            lambda: build_fact_formal_mau_daily(setup.conn, ctx=silver_ctx),
        ),
        (
            "fact_license_utilization_daily",
            "build_fact_license_utilization_daily",
            lambda: build_fact_license_utilization_daily(setup.conn, ctx=silver_ctx),
        ),
    ]
    for target, label, builder in user_activity_builders:
        try:
            with log_timed_phase(setup.conn, label=label):
                built_name = builder()
            if built_name:
                built_user_activity.append(built_name)
            else:
                skipped_outputs.add(target)
        except Exception as exc:
            logger.exception("Failed building %s", target)
            _record_failure(failure_records, stage="build", target=target, exc=exc)
            failed_outputs.add(target)

    try:
        with log_timed_phase(setup.conn, label="collect_user_activity_quality_report"):
            user_activity_quality = collect_user_activity_quality_report(setup.conn)
    except Exception as exc:
        logger.exception("Failed collecting user activity quality report")
        _record_failure(
            failure_records,
            stage="build",
            target="user_activity_quality_report",
            exc=exc,
            severity=1,
        )
        user_activity_quality = {}

    try:
        with log_timed_phase(
            setup.conn, label="collect_license_utilization_quality_report"
        ):
            license_utilization_quality = collect_license_utilization_quality_report(
                setup.conn
            )
    except Exception as exc:
        logger.exception("Failed collecting license utilization quality report")
        _record_failure(
            failure_records,
            stage="build",
            target="license_utilization_quality_report",
            exc=exc,
            severity=1,
        )
        license_utilization_quality = {}

    built_object_activity = []
    if not dim_category_to_capability_ok:
        reason = "blocked by failed dependency: dim_category_to_capability"
        logger.error("Skipping fact_object_activity_events: %s", reason)
        _record_failure(
            failure_records,
            stage="dependency_skip",
            target="fact_object_activity_events",
            message=reason,
            dependency="dim_category_to_capability",
        )
        skipped_outputs.add("fact_object_activity_events")
    else:
        try:
            with log_timed_phase(setup.conn, label="build_fact_object_activity_events"):
                object_activity_name = build_fact_object_activity_events(
                    setup.conn, ctx=silver_ctx, base_dir=base_dir
                )
            if object_activity_name:
                built_object_activity.append(object_activity_name)
            else:
                skipped_outputs.add("fact_object_activity_events")
        except Exception as exc:
            logger.exception("Failed building fact_object_activity_events")
            _record_failure(
                failure_records,
                stage="build",
                target="fact_object_activity_events",
                exc=exc,
            )
            failed_outputs.add("fact_object_activity_events")

    try:
        with log_timed_phase(setup.conn, label="build_base_dataiku_products_registry"):
            built_products_registry = build_base_dataiku_products_registry(
                setup.conn, base_dir=base_dir
            )
    except Exception as exc:
        logger.exception("Failed building base_dataiku_products_registry")
        _record_failure(
            failure_records,
            stage="build",
            target="base_dataiku_products_registry",
            exc=exc,
        )
        built_products_registry = ""
        failed_outputs.add("base_dataiku_products_registry")

    current_tables = list_table_names(setup.conn)
    table_groups = group_gold_tables_by_prefix(current_tables)
    base_tables = table_groups["base_tables"]
    dim_tables = table_groups["dim_tables"]
    agg_tables = table_groups["agg_tables"]
    fact_tables = table_groups["fact_tables"]
    unload_candidates = base_tables + dim_tables + agg_tables + fact_tables
    unload_destinations = {
        table_name: gold_destination_for_table(table_name)
        for table_name in unload_candidates
    }

    try:
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
    except Exception as exc:
        logger.exception("Failed pre-unload diagnostics")
        _record_failure(
            failure_records,
            stage="build",
            target="pre_unload_diagnostics",
            exc=exc,
            severity=1,
        )

    if unload_behavior == "duckdb":
        try:
            dev_modules = load_dev_toolbox_modules(base_dir)
        except Exception as exc:
            logger.exception("Failed loading dev activity modules for unload views")
            _record_failure(
                failure_records,
                stage="build",
                target="dev_activity_unload_modules",
                exc=exc,
                severity=1,
            )
            dev_modules = []
        try:
            object_modules = load_object_activity_modules(base_dir)
        except Exception as exc:
            logger.exception("Failed loading object activity modules for unload views")
            _record_failure(
                failure_records,
                stage="build",
                target="object_activity_unload_modules",
                exc=exc,
                severity=1,
            )
            object_modules = []

        for module_name in set(dev_modules + object_modules):
            try:
                _create_event_mapping_module_view(
                    setup.conn,
                    ctx=silver_ctx,
                    module=module_name,
                    view_name=f"v_event_mapping__{module_name}",
                )
            except Exception as exc:
                logger.exception(
                    "Failed creating auxiliary event_mapping view: module=%s",
                    module_name,
                )
                _record_failure(
                    failure_records,
                    stage="silver_view",
                    target=f"v_event_mapping__{module_name}",
                    exc=exc,
                    category="event_mapping",
                    module=module_name,
                    severity=1,
                )

    unloaded_tables, failed_tables, unload_failures = unload_gold_tables_with_details(
        setup.conn,
        gold_ctx=gold_ctx,
        gold_folder_lookup=gold_folder_lookup,
        table_names=unload_candidates,
        unload_behavior=unload_behavior,
        manifest=pending_manifest,
        incremental_enabled=manifest_enabled,
        lookback_days=lookback_days,
    )
    for failure in unload_failures:
        _record_failure(
            failure_records,
            stage="unload",
            target=failure.table,
            message=failure.error_message,
            severity=_impact_level_for_target(failure.table),
        )
        failure_records[-1]["error_type"] = failure.error_type

    if manifest_enabled and pending_manifest is not None and not failure_records:
        stamp_manifest_updated_at(pending_manifest)
        write_manifest(gold_folder_lookup, pending_manifest)
        manifest = pending_manifest

    has_failures = bool(failure_records)
    failure_summary = _failure_summary(failure_records) if has_failures else ""
    result = {
        "source_project_key": project_key,
        "silver_folder_lookup": silver_ctx.folder_lookup,
        "gold_folder_lookup": gold_ctx.folder_lookup,
        "gold_output_folder": gold_folder_lookup,
        "connection_type": silver_ctx.connection_type,
        "connection_name": silver_ctx.connection_name,
        "unload_behavior": unload_behavior,
        "manifest_enabled": manifest_enabled,
        "lookback_days": lookback_days,
        "user_activity_build_watermarks": user_activity_build_watermarks,
        "duckdb_connection_ready": setup.conn is not None,
        "gold_specs_dir": str(base_dir),
        "built_specs": built_specs,
        "built_dimensions": built_dimensions,
        "built_dev_activity": built_dev_activity,
        "built_user_activity": built_user_activity,
        "user_activity_quality": user_activity_quality,
        "license_utilization_quality": license_utilization_quality,
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
        "has_failures": has_failures,
        "failure_records": failure_records,
        "failure_summary": failure_summary,
        "ok": not has_failures,
        "manifest": manifest,
    }
    if has_failures:
        raise CreateGoldBestEffortError(failure_summary, result=result)
    return result


if __name__ == "__main__":
    run()
