"""DB initialization for the Pulse DuckDB file.

This module is intentionally safe to call repeatedly (idempotent).

On app startup we want to:
1) ensure the DuckDB file exists
2) ensure minimal internal schema exists
3) optionally load curated GOLD parquet tables from a Dataiku managed folder

Because the app is typically served by gunicorn (potentially multiple workers),
this module also includes a simple file lock to avoid concurrent rebuilds.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

import yaml

from .create_conn import create_connection

from ... import settings


logger = logging.getLogger(__name__)


_BASE_DIR = Path(__file__).resolve().parents[1]
_BASE_SPECS_DIR = _BASE_DIR / "datasets" / "base"
_EXPECTED_STARTUP_TABLES = {
    "final_build_catalog",
    "final_build_products_catalog",
    "dev_activity_capability_daily",
    "final_build_development_activity_events",
}


def _set_status_callback(phase: str, message: str) -> None:
    callback = getattr(settings, "PULSE_INIT_STATUS_CALLBACK", None)
    if callable(callback):
        try:
            callback(str(phase), str(message))
        except Exception:
            logger.debug("DuckDB init status callback failed", exc_info=True)


def _load_base_spec_sql(table_name: str) -> str:
    path = _BASE_SPECS_DIR / f"{table_name}.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or len(doc) != 1:
        raise ValueError(f"Invalid base spec YAML (expected single top-level key): {path}")
    name, payload = next(iter(doc.items()))
    if name != table_name:
        raise ValueError(f"Base spec name mismatch in {path}: expected {table_name}, got {name}")
    sql = str(payload.get("sql", "") or "").strip()
    if not sql:
        raise ValueError(f"Missing `sql` in base spec: {path}")
    return sql


def _ensure_table_exists(conn, *, table_name: str) -> bool:
    """Ensure a BASE TABLE exists.

    Returns True if created (or recreated), else False.
    """

    table_rows = conn.execute(
        """
        SELECT table_type
        FROM information_schema.tables
        WHERE table_schema='main' AND table_name=?
        """,
        [table_name],
    ).fetchall()

    if table_rows:
        table_type = str(table_rows[0][0])
        if table_type.upper() == "BASE TABLE":
            return False
        # If an object exists but isn't a table, drop it.
        try:
            conn.execute(f'DROP VIEW IF EXISTS "{table_name}";')
        except Exception:
            pass
        try:
            conn.execute(f'DROP TABLE IF EXISTS "{table_name}";')
        except Exception:
            pass

    sql = _load_base_spec_sql(table_name)
    conn.execute(sql)
    return True


def _object_type(conn, name: str) -> str | None:
    row = conn.execute(
        """
        SELECT table_type
        FROM information_schema.tables
        WHERE table_schema='main' AND table_name=?
        """,
        [name],
    ).fetchone()
    if not row:
        return None
    return str(row[0]).upper()


def _table_exists(conn, name: str) -> bool:
    return _object_type(conn, name) == "BASE TABLE"


def _replace_view_from_query(conn, *, view_name: str, source_table: str, select_sql: str) -> None:
    if _table_exists(conn, view_name):
        logger.info(
            "DuckDB init: keeping existing base table %s; skipping compatibility view from %s",
            view_name,
            source_table,
        )
        return

    if not _object_type(conn, source_table):
        logger.info(
            "DuckDB init: source table %s missing; skipping compatibility view %s",
            source_table,
            view_name,
        )
        return

    conn.execute(f'DROP VIEW IF EXISTS "{view_name}";')
    conn.execute(f'CREATE VIEW "{view_name}" AS {select_sql}')  # nosec B608


def _maybe_create_inventory_views(conn) -> None:
    """Create compatibility views for inventory tables when base tables are absent."""

    _replace_view_from_query(
        conn,
        view_name="base_projects_metadata",
        source_table="base_projects_instance_metadata_history",
        select_sql="""
        SELECT
          instance_name,
          project_key,
          projects_name AS project_name,
          projects_ownerlogin AS project_owner_login,
          projects_ownerdisplayname AS project_owner_display_name,
          projects_creationtag_lastmodifiedby_login AS project_creation_login,
          projects_versiontag_lastmodifiedby_login AS project_last_modified_by_login,
          try_cast(projects_creationtag_lastmodifiedon AS TIMESTAMP) AS project_created_at,
          try_cast(projects_versiontag_lastmodifiedon AS TIMESTAMP) AS project_updated_at,
          projects_projecttype AS project_type,
          projects_projectapptype AS project_app_type,
          projects_tutorialproject AS tutorial_project,
          projects_commitmode AS commit_mode
        FROM base_projects_instance_metadata_history
        """.strip(),
    )

    _replace_view_from_query(
        conn,
        view_name="base_datasets_metadata",
        source_table="base_datasets_project_metadata_history",
        select_sql="""
        SELECT
          instance_name,
          project_key,
          datasets_name AS dataset_name,
          datasets_smartname AS dataset_display_name,
          datasets_type AS dataset_type,
          datasets_managed AS dataset_managed,
          datasets_versiontag_lastmodifiedby_login AS dataset_last_modified_by_login,
          try_cast(datasets_creationtag_lastmodifiedon AS TIMESTAMP) AS dataset_created_at,
          try_cast(datasets_versiontag_lastmodifiedon AS TIMESTAMP) AS dataset_updated_at,
          datasets_smartname AS dataset_smart_name,
          CAST(NULL AS VARCHAR) AS dataset_subtype,
          datasets_featuregroup AS is_feature_group
        FROM base_datasets_project_metadata_history
        """.strip(),
    )

    _replace_view_from_query(
        conn,
        view_name="base_recipes_metadata",
        source_table="base_recipes_project_metadata_history",
        select_sql="""
        SELECT
          instance_name,
          project_key,
          recipes_name AS recipe_name,
          recipes_type AS recipe_type,
          recipes_versiontag_lastmodifiedby_login AS recipe_last_modified_by_login,
          try_cast(recipes_creationtag_lastmodifiedon AS TIMESTAMP) AS recipe_created_at,
          try_cast(recipes_versiontag_lastmodifiedon AS TIMESTAMP) AS recipe_updated_at,
          recipes_params_enginetype AS engine_type,
          recipes_params_enginelabel AS engine_label,
          recipes_params_enginerecommended AS engine_recommended
        FROM base_recipes_project_metadata_history
        """.strip(),
    )

    _replace_view_from_query(
        conn,
        view_name="base_scenarios_metadata",
        source_table="base_scenarios_project_metadata_history",
        select_sql="""
        SELECT
          instance_name,
          project_key,
          scenarios_id AS scenario_id,
          scenarios_name AS scenario_name,
          scenarios_type AS scenario_type,
          scenarios_active AS scenario_active,
          scenarios_runasuser AS scenario_run_as_login,
          try_cast(scenarios_createdon AS TIMESTAMP) AS scenario_created_at,
          try_cast(scenarios_lastmodifiedon AS TIMESTAMP) AS scenario_updated_at,
          try_cast(scenarios_nextrun AS TIMESTAMP) AS scenario_next_run,
          try_cast(scenarios_start AS TIMESTAMP) AS scenario_last_run_start,
          scenarios_running AS scenario_running
        FROM base_scenarios_project_metadata_history
        """.strip(),
    )

    _replace_view_from_query(
        conn,
        view_name="base_object_activity_events",
        source_table="fact_object_activity_events",
        select_sql="SELECT * FROM fact_object_activity_events",
    )


def _maybe_seed_demo_dev_activity(conn) -> dict:
    """Seed dev-activity tables for demo mode.

    In the current DEMO workflow, auto-loading from the managed folder only loads
    `base_*.csv` tables. The Build → Development Activity page depends on
    `fact_dev_activity_events` + `dim_category_to_capability`, which may not be
    present.

    We create them from their YAML base specs if missing, and seed deterministic
    dummy data only when they are empty.
    """

    if not settings.PULSE_SEED_DEMO_DEV_ACTIVITY:
        return {"ok": True, "enabled": False}

    created: list[str] = []
    seeded: list[str] = []

    for table_name in ["dim_category_to_capability", "fact_dev_activity_events"]:
        if _ensure_table_exists(conn, table_name=table_name):
            created.append(table_name)

    # Seed taxonomy if empty
    try:
        n = conn.execute('SELECT COUNT(*) FROM "dim_category_to_capability";').fetchone()[0]
    except Exception:
        n = 0
    if int(n) == 0:
        conn.execute(
            """
            INSERT INTO dim_category_to_capability (dataiku_category, capability, capability_order, category_order)
            VALUES
              ('Coding', 'Data Engineering', 1, 1),
              ('Datasets', 'Data Engineering', 1, 2),
              ('Visual Recipes', 'Data Engineering', 1, 3),
              ('Machine Learning & Operations', 'Advanced Analytics & ML', 2, 1),
              ('Generative AI & LLM', 'GenAI & LLM', 3, 1),
              ('Scenarios', 'Automation & Orchestration', 4, 1),
              ('API Services', 'APIs & Integration', 5, 1),
              ('Web Applications', 'Applications & Delivery', 6, 1);
            """
        )
        seeded.append("dim_category_to_capability")

    # Seed events if empty
    try:
        n = conn.execute('SELECT COUNT(*) FROM "fact_dev_activity_events";').fetchone()[0]
    except Exception:
        n = 0
    if int(n) == 0:
        now = datetime.now(tz=UTC)
        rows = []
        for i in range(200):
            instance = "dss-prod" if i % 3 else "dss-dev"
            login = ["alice", "bob", "carol"][i % 3]
            project = ["FIN", "MKT", "ENG"][i % 3]
            category = [
                "Coding",
                "Datasets",
                "Visual Recipes",
                "Machine Learning & Operations",
                "Generative AI & LLM",
                "Scenarios",
                "API Services",
                "Web Applications",
            ][i % 8]
            base = [
                "CODE_STUDIO",
                "DATASET_EDIT",
                "PREPARE",
                "MODEL_TRAIN",
                "PROMPT",
                "SCENARIO_RUN",
                "API_SERVICE",
                "WEBAPP_EDIT",
            ][i % 8]
            ts = now - timedelta(days=i % 45)
            rows.append((ts, instance, login, f"{base}_EVENT", base, category, project))

        conn.executemany(
            """
            INSERT INTO fact_dev_activity_events (
              timestamp, instance_name, login, msgtype, msgtypebase, dataiku_category, project_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            rows,
        )
        seeded.append("fact_dev_activity_events")

    return {
        "ok": True,
        "enabled": True,
        "created": created,
        "seeded": seeded,
    }


@contextmanager
def _duckdb_init_lock():
    """Best-effort inter-process lock using `fcntl`.

    This works on Linux (Code Studio container) and is sufficient to prevent
    multiple gunicorn workers from doing expensive table loads simultaneously.
    """

    lock_path = settings.PULSE_DUCKDB_INIT_LOCK_PATH
    settings.ensure_duckdb_parent_dir()

    # `fcntl` is Unix-only; this codebase targets Linux.
    import fcntl

    start = time.time()
    with open(lock_path, "w") as f:
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() - start > settings.PULSE_DUCKDB_INIT_TIMEOUT_SEC:
                    raise TimeoutError(f"Timed out waiting for init lock: {lock_path}")
                time.sleep(0.1)

        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def initialize_database() -> None:
    """Create the DuckDB file and seed a minimal schema."""

    started = time.time()
    logger.info("DuckDB initialize_database: opening writable connection to %s", settings.DUCKDB_PATH)
    conn = create_connection(read_only=False)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pulse_test (
                id INTEGER,
                name VARCHAR,
                created_at TIMESTAMP
            );
            """
        )

        # Seed a row (idempotent)
        conn.execute(
            """
            INSERT INTO pulse_test
            SELECT 1, 'hello', now()
            WHERE NOT EXISTS (SELECT 1 FROM pulse_test WHERE id = 1);
            """
        )
    finally:
        conn.close()
        logger.info(
            "DuckDB initialize_database: completed in %.3fs",
            time.time() - started,
        )


def ensure_database_ready(*, load_gold_tables: bool | None = None, replace_gold_tables: bool | None = None) -> dict:
    """Ensure DuckDB exists and (optionally) load curated GOLD parquet tables.

    Returns a small status dict suitable for logging/debug endpoints.

    Notes:
    - If `load_gold_tables` is false, this only creates the DB + minimal schema.
    - If loading gold tables fails (misconfigured project key/folder, missing
      permissions, etc.), the app continues to start and the error is reported
      in the returned dict.
    """

    if load_gold_tables is None:
        load_gold_tables = settings.PULSE_AUTO_LOAD_GOLD_TABLES

    if replace_gold_tables is None:
        replace_gold_tables = settings.PULSE_AUTO_LOAD_REPLACE

    started = time.time()
    logger.info(
        "DuckDB ensure_database_ready: start load_gold_tables=%s replace_gold_tables=%s path=%s source_project=%s folder=%s",
        load_gold_tables,
        replace_gold_tables,
        settings.DUCKDB_PATH,
        settings.PULSE_SOURCE_PROJECT_KEY,
        settings.PULSE_GOLD_TABLES_FOLDER_ID or settings.PULSE_GOLD_TABLES_FOLDER_NAME,
    )
    _set_status_callback("waiting_lock", "Waiting for DuckDB init lock")

    try:
        logger.info("DuckDB ensure_database_ready: waiting for init lock at %s", settings.PULSE_DUCKDB_INIT_LOCK_PATH)
        with _duckdb_init_lock():
            _set_status_callback("preparing_db", "Init lock acquired; preparing local DuckDB")
            logger.info(
                "DuckDB ensure_database_ready: acquired init lock after %.3fs",
                time.time() - started,
            )
            initialize_database()

            if not load_gold_tables:
                _set_status_callback("frontend_ready", "DuckDB is ready")
                logger.info(
                    "DuckDB ensure_database_ready: completed without GOLD load in %.3fs",
                    time.time() - started,
                )
                return {"ok": True, "initialized": True, "gold_loaded": False}

            try:
                connection_started = time.time()
                logger.info("DuckDB ensure_database_ready: opening main writable connection")
                conn = create_connection(read_only=False)
                try:
                    _set_status_callback("preparing_reload", "Connected to DuckDB; preparing GOLD reload")
                    logger.info(
                        "DuckDB ensure_database_ready: main connection ready in %.3fs",
                        time.time() - connection_started,
                    )
                    # Lazy import: this pulls in `dataiku` (heavier) and hits DSS APIs.
                    import_started = time.time()
                    from .view_builder import build_views_from_specs
                    logger.info(
                        "DuckDB ensure_database_ready: imported view builder in %.3fs",
                        time.time() - import_started,
                    )

                    report: dict | None = None
                    gold_tables_loaded = False
                    reason = None

                    if not replace_gold_tables:
                        # If gold tables are already present, avoid hitting DSS APIs.
                        #
                        # We require at least one `fact_`/`dim_` too, otherwise we might
                        # be in a half-initialized state from an older loader.
                        existing_tables = set(conn.execute("PRAGMA show_tables;").df()["name"].tolist())
                        has_base = any(t.startswith("base_") for t in existing_tables)
                        has_fact_or_dim = any(t.startswith(("fact_", "dim_")) for t in existing_tables)
                        has_expected_views = _EXPECTED_STARTUP_TABLES.issubset(existing_tables)
                        logger.info(
                            "DuckDB ensure_database_ready: existing tables=%s has_base=%s has_fact_or_dim=%s has_expected_views=%s",
                            len(existing_tables),
                            has_base,
                            has_fact_or_dim,
                            has_expected_views,
                        )
                        if has_base and has_fact_or_dim and has_expected_views:
                            reason = "base_tables_present"
                        else:
                            if has_base and has_fact_or_dim and not has_expected_views:
                                logger.info(
                                    "DuckDB exists but required startup views are missing; reloading GOLD tables"
                                )
                            # No base tables yet: do a first-time load.
                            from .gold_loader import load_gold_tables as _load_gold_tables
                            from .gold_loader import infer_table_name
                            from .gold_loader import list_gold_paths

                            path_listing_started = time.time()
                            _set_status_callback("listing_gold", "Listing GOLD datasets from managed folder")
                            logger.info("DuckDB ensure_database_ready: listing candidate GOLD paths")
                            view_like_names = {
                                p.stem
                                for p in (Path(__file__).resolve().parents[1] / "datasets" / "views").glob("*.yaml")
                            }

                            gold_paths = list_gold_paths(suffixes=(".csv", ".parquet"))
                            logger.info(
                                "DuckDB ensure_database_ready: listed %s GOLD paths in %.3fs",
                                len(gold_paths),
                                time.time() - path_listing_started,
                            )

                            allowed_names = {
                                infer_table_name(str(p).lstrip("/"))
                                for p in gold_paths
                                if PurePosixPath(str(p).lstrip("/")).parts
                                and PurePosixPath(str(p).lstrip("/")).parts[0].lstrip("/") == "gold"
                                and infer_table_name(str(p).lstrip("/"))
                                and infer_table_name(str(p).lstrip("/")) not in view_like_names
                                and infer_table_name(str(p).lstrip("/")).startswith(("base_", "dim_", "fact_", "reg_"))
                            }
                            logger.info(
                                "DuckDB ensure_database_ready: %s allowed GOLD table names after filtering",
                                len(allowed_names),
                            )

                            load_started = time.time()
                            _set_status_callback("loading_gold", "Loading GOLD tables into DuckDB")
                            logger.info("DuckDB ensure_database_ready: loading GOLD tables replace=%s", False)
                            report = _load_gold_tables(
                                conn,
                                replace=False,
                                prefix=settings.PULSE_GOLD_LOAD_PREFIX,
                                name_glob=settings.PULSE_GOLD_LOAD_NAME_GLOB,
                                allowed_suffixes=(".csv", ".parquet"),
                                allowed_table_names=allowed_names,
                            )
                            logger.info(
                                "DuckDB ensure_database_ready: GOLD load completed in %.3fs with ok=%s loaded=%s failed=%s",
                                time.time() - load_started,
                                report.get("ok"),
                                len(report.get("loaded", [])),
                                len(report.get("failed", [])),
                            )
                            gold_tables_loaded = bool(report.get("loaded"))
                    else:
                        from .gold_loader import infer_table_name
                        from .gold_loader import load_gold_tables as _load_gold_tables
                        from .gold_loader import list_gold_paths

                        path_listing_started = time.time()
                        _set_status_callback("listing_gold", "Listing GOLD datasets from managed folder")
                        logger.info("DuckDB ensure_database_ready: listing candidate GOLD paths for replace run")
                        view_like_names = {
                            p.stem
                            for p in (Path(__file__).resolve().parents[1] / "datasets" / "views").glob("*.yaml")
                        }

                        gold_paths = list_gold_paths(suffixes=(".csv", ".parquet"))
                        logger.info(
                            "DuckDB ensure_database_ready: listed %s GOLD paths in %.3fs",
                            len(gold_paths),
                            time.time() - path_listing_started,
                        )

                        allowed_names = {
                            infer_table_name(str(p).lstrip("/"))
                            for p in gold_paths
                            if PurePosixPath(str(p).lstrip("/")).parts
                            and PurePosixPath(str(p).lstrip("/")).parts[0].lstrip("/") == "gold"
                            and infer_table_name(str(p).lstrip("/"))
                            and infer_table_name(str(p).lstrip("/")) not in view_like_names
                            and infer_table_name(str(p).lstrip("/")).startswith(("base_", "dim_", "fact_", "reg_"))
                        }
                        logger.info(
                            "DuckDB ensure_database_ready: %s allowed GOLD table names after filtering",
                            len(allowed_names),
                        )

                        load_started = time.time()
                        _set_status_callback("loading_gold", "Loading GOLD tables into DuckDB")
                        logger.info("DuckDB ensure_database_ready: loading GOLD tables replace=%s", True)
                        report = _load_gold_tables(
                            conn,
                            replace=True,
                            prefix=settings.PULSE_GOLD_LOAD_PREFIX,
                            name_glob=settings.PULSE_GOLD_LOAD_NAME_GLOB,
                            allowed_suffixes=(".csv", ".parquet"),
                            allowed_table_names=allowed_names,
                        )
                        logger.info(
                            "DuckDB ensure_database_ready: GOLD load completed in %.3fs with ok=%s loaded=%s failed=%s",
                            time.time() - load_started,
                            report.get("ok"),
                            len(report.get("loaded", [])),
                            len(report.get("failed", [])),
                        )
                        gold_tables_loaded = bool(report.get("loaded"))

                    # Compatibility views: map GOLD `*_metadata_history` outputs to
                    # the UI-facing `base_*_metadata` tables expected by view specs.
                    inventory_started = time.time()
                    _set_status_callback("inventory_views", "Creating compatibility views")
                    _maybe_create_inventory_views(conn)
                    logger.info(
                        "DuckDB ensure_database_ready: inventory compatibility views ready in %.3fs",
                        time.time() - inventory_started,
                    )

                    seed_started = time.time()
                    _set_status_callback("seeding_demo", "Seeding demo activity data if needed")
                    seed_report = _maybe_seed_demo_dev_activity(conn)
                    logger.info(
                        "DuckDB ensure_database_ready: demo seed completed in %.3fs enabled=%s",
                        time.time() - seed_started,
                        seed_report.get("enabled"),
                    )
                    views_started = time.time()
                    _set_status_callback("building_views", "Building dashboard views")
                    views_report = build_views_from_specs(conn)
                    logger.info(
                        "DuckDB ensure_database_ready: view build completed in %.3fs with ok=%s",
                        time.time() - views_started,
                        views_report.get("ok"),
                    )

                    ok = bool(views_report.get("ok", False))
                    if report is not None:
                        ok = ok and bool(report.get("ok", False))

                    logger.info(
                        "DuckDB ensure_database_ready: finished in %.3fs ok=%s gold_loaded=%s reason=%s",
                        time.time() - started,
                        ok,
                        gold_tables_loaded,
                        reason,
                    )
                    _set_status_callback(
                        "frontend_ready" if ok else "failed",
                        "DuckDB initialization complete" if ok else "DuckDB initialization failed",
                    )
                    return {
                        "ok": ok,
                        "initialized": True,
                        "gold_loaded": gold_tables_loaded,
                        "reason": reason,
                        "report": report,
                        "seed_demo_dev_activity": seed_report,
                        "views": views_report,
                    }
                finally:
                    conn.close()
            except Exception as e:
                logger.exception("DuckDB auto-load failed")
                return {"ok": False, "initialized": True, "gold_loaded": False, "error": str(e)}
    except TimeoutError as e:
        logger.warning("DuckDB init lock timeout: %s", e)
        return {"ok": False, "initialized": False, "gold_loaded": False, "error": str(e)}
