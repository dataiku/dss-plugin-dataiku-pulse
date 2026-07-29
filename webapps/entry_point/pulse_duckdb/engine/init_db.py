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

from pulse_duckdb.engine.create_conn import create_connection

import settings


logger = logging.getLogger(__name__)


_BASE_DIR = Path(__file__).resolve().parents[1]
_BASE_SPECS_DIR = _BASE_DIR / "datasets" / "base"


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


def _maybe_seed_demo_dev_activity(conn) -> dict:
    """Seed dev-activity tables for demo mode.

    This is strictly a fallback for demo/dev runs.

    If real GOLD data is present (non-empty dim + fact), this function becomes a
    no-op even when `PULSE_SEED_DEMO_DEV_ACTIVITY=1`.
    """

    if not settings.PULSE_SEED_DEMO_DEV_ACTIVITY:
        return {"ok": True, "enabled": False}

    def _count_rows(name: str) -> int:
        try:
            return int(conn.execute(f'SELECT COUNT(*) FROM "{name}";').fetchone()[0])
        except Exception:
            return 0

    # If GOLD load already created these (table or view) and they have data,
    # never overwrite/seed demo rows.
    if _count_rows("dim_category_to_capability") > 0 and _count_rows("fact_dev_activity_events") > 0:
        return {"ok": True, "enabled": False, "reason": "real_data_present"}

    created: list[str] = []
    seeded: list[str] = []

    for table_name in ["dim_category_to_capability", "fact_dev_activity_events"]:
        if _ensure_table_exists(conn, table_name=table_name):
            created.append(table_name)

    # Seed taxonomy if empty
    if _count_rows("dim_category_to_capability") == 0:
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
    if _count_rows("fact_dev_activity_events") == 0:
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

    try:
        with _duckdb_init_lock():
            initialize_database()

            if not load_gold_tables:
                return {"ok": True, "initialized": True, "gold_loaded": False}

            try:
                conn = create_connection(read_only=False)
                try:
                    # Lazy import: this pulls in `dataiku` (heavier) and hits DSS APIs.
                    from pulse_duckdb.engine.view_builder import build_views_from_specs

                    report: dict | None = None
                    gold_tables_loaded = False
                    reason = None

                    if not replace_gold_tables:
                        # If any base tables are already present, avoid hitting DSS APIs.
                        existing_tables = set(conn.execute("PRAGMA show_tables;").df()["name"].tolist())
                        if {t for t in existing_tables if t.startswith("base_")}:
                            reason = "base_tables_present"
                        else:
                            # No base tables yet: do a first-time load.
                            from pulse_duckdb.engine.gold_loader import load_gold_tables as _load_gold_tables
                            from pulse_duckdb.engine.gold_loader import list_gold_paths

                            view_like_names = {
                                p.stem
                                for p in (Path(__file__).resolve().parents[1] / "datasets" / "views").glob("*.yaml")
                            }
                            allowed_base_names = {
                                PurePosixPath(p).stem
                                for p in list_gold_paths(suffixes=(".csv", ".parquet"))
                                if PurePosixPath(p.lstrip("/")).name.startswith(("base_", "dim_", "fact_"))
                                and PurePosixPath(p.lstrip("/")).suffix.lower() in (".csv", ".parquet")
                                and PurePosixPath(p.lstrip("/")).stem not in view_like_names
                            }

                            report = _load_gold_tables(
                                conn,
                                replace=False,
                                prefix=settings.PULSE_GOLD_LOAD_PREFIX,
                                name_glob=settings.PULSE_GOLD_LOAD_NAME_GLOB,
                                allowed_suffixes=(".csv", ".parquet"),
                                allowed_table_names=allowed_base_names,
                            )
                            gold_tables_loaded = True
                    else:
                        from pulse_duckdb.engine.gold_loader import load_gold_tables as _load_gold_tables
                        from pulse_duckdb.engine.gold_loader import list_gold_paths

                        view_like_names = {
                            p.stem
                            for p in (Path(__file__).resolve().parents[1] / "datasets" / "views").glob("*.yaml")
                        }
                        allowed_base_names = {
                            PurePosixPath(p).stem
                            for p in list_gold_paths(suffixes=(".csv", ".parquet"))
                            if PurePosixPath(p.lstrip("/")).name.startswith(("base_", "dim_", "fact_"))
                            and PurePosixPath(p.lstrip("/")).suffix.lower() in (".csv", ".parquet")
                            and PurePosixPath(p.lstrip("/")).stem not in view_like_names
                        }


                        report = _load_gold_tables(
                            conn,
                            replace=True,
                            prefix=settings.PULSE_GOLD_LOAD_PREFIX,
                            name_glob=settings.PULSE_GOLD_LOAD_NAME_GLOB,
                            allowed_suffixes=(".csv", ".parquet"),
                            allowed_table_names=allowed_base_names,
                        )
                        gold_tables_loaded = True

                    seed_report = _maybe_seed_demo_dev_activity(conn)
                    views_report = build_views_from_specs(conn)

                    # Optional: create temporary views over SILVER audit event_mapping parquet.
                    event_mapping_report = {"ok": True, "enabled": False}
                    if settings.PULSE_LOAD_SILVER_EVENT_MAPPING:
                        try:
                            from pulse_duckdb.engine.silver_event_mapping import (
                                cache_event_mapping_parquet,
                                create_event_mapping_views,
                            )

                            cache_report = cache_event_mapping_parquet()
                            views2_report = create_event_mapping_views(conn)
                            event_mapping_report = {
                                "ok": bool(cache_report.get("ok")) and bool(views2_report.get("ok")),
                                "enabled": True,
                                "cache": cache_report,
                                "views": views2_report,
                            }
                        except Exception as e:
                            logger.exception("Failed building event_mapping views")
                            event_mapping_report = {"ok": False, "enabled": True, "error": str(e)}

                    ok = bool(views_report.get("ok", False))
                    if report is not None:
                        ok = ok and bool(report.get("ok", False))

                    return {
                        "ok": ok,
                        "initialized": True,
                        "gold_loaded": gold_tables_loaded,
                        "reason": reason,
                        "report": report,
                        "seed_demo_dev_activity": seed_report,
                        "views": views_report,
                        "event_mapping": event_mapping_report,
                    }
                finally:
                    conn.close()
            except Exception as e:
                logger.exception("DuckDB auto-load failed")
                return {"ok": False, "initialized": True, "gold_loaded": False, "error": str(e)}
    except TimeoutError as e:
        logger.warning("DuckDB init lock timeout: %s", e)
        return {"ok": False, "initialized": False, "gold_loaded": False, "error": str(e)}
