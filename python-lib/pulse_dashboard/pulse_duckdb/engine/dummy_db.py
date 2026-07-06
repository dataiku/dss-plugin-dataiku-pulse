"""Build a local DuckDB schema with dummy data from `pulse_duckdb/datasets/**/*.yaml`.

This is intended for DEMO/dev usage so the React UI can be migrated away from
hardcoded arrays and toward SQL queries without requiring a real GOLD parquet
pipeline.

It is safe to call repeatedly. When enabled, this module drops existing tables
and views in the DuckDB file and recreates them.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import yaml

from ... import settings
from .create_conn import create_connection
from .init_db import _duckdb_init_lock


logger = logging.getLogger(__name__)


_BASE_DIR = Path(__file__).resolve().parents[1]
_DATASETS_DIR = _BASE_DIR / "datasets"
_BASE_SPECS_DIR = _DATASETS_DIR / "base"
_VIEW_SPECS_DIR = _DATASETS_DIR / "views"


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_ident(name: str) -> str:
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"Unsafe identifier: {name}")
    return name


def _type_to_duckdb(type_str: str) -> str:
    t = type_str.strip().upper()
    if t in {"STRING", "VARCHAR"}:
        return "VARCHAR"
    if t in {"BIGINT", "INT64"}:
        return "BIGINT"
    if t in {"INTEGER", "INT", "INT32"}:
        return "INTEGER"
    if t in {"BOOLEAN", "BOOL"}:
        return "BOOLEAN"
    if t in {"TIMESTAMP", "DATETIME"}:
        return "TIMESTAMP"
    if t in {"DATE"}:
        return "DATE"

    # Loose specs
    if "JSON" in t:
        return "VARCHAR"
    if "ARRAY" in t:
        return "VARCHAR"

    return "VARCHAR"


def _extract_backticked_name(text: str, *, default_name: str) -> str:
    """Best-effort extract object name from markdown.

    Some docs reference other table names (eg. addendums). Prefer the first H1
    header pattern like: `# Base table spec: `name``.
    """

    # Prefer the header that defines the spec name
    for pat in [r"^#\s*Base table spec:\s*`([A-Za-z0-9_]+)`", r"^#\s*View spec:\s*`([A-Za-z0-9_]+)`"]:
        m = re.search(pat, text, flags=re.MULTILINE)
        if m:
            return m.group(1)

    # Fall back to first backticked identifier
    m = re.search(r"`([A-Za-z0-9_]+)`", text)
    if m:
        return m.group(1)

    return default_name


def _parse_schema_tables(md_text: str) -> list[tuple[str, str]]:
    """Parse markdown column/type tables.

    Looks for markdown tables with headers containing `column` and `type`.
    Returns a list of `(column_name, duckdb_type)`.
    """

    lines = md_text.splitlines()
    cols: list[tuple[str, str]] = []

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().lower().startswith("| column") and "| type" in line.lower():
            # Skip separator line (|---|---|...)
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if len(row) >= 2:
                    col = row[0]
                    typ = row[1]
                    if col and col.lower() != "column":
                        cols.append((_safe_ident(col), _type_to_duckdb(typ)))
                i += 1
            continue
        i += 1

    # De-dup preserving order
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for c, t in cols:
        if c in seen:
            continue
        seen.add(c)
        unique.append((c, t))

    return unique


# (View creation is delegated to `pulse_duckdb.engine.view_builder`.)


def reset_duckdb(conn: duckdb.DuckDBPyConnection) -> dict:
    """Drop all tables/views in schema `main`.

    We query views/tables separately because DuckDB's information_schema.tables
    reports `BASE TABLE` for tables, and views are exposed in
    information_schema.views.
    """

    tables = [
        r[0]
        for r in conn.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'main'
              AND table_type = 'BASE TABLE';
            """
        ).fetchall()
    ]

    views = [
        r[0]
        for r in conn.execute(
            """
            SELECT table_name
            FROM information_schema.views
            WHERE table_schema = 'main';
            """
        ).fetchall()
        # DuckDB exposes system views in `main` as well.
        if not str(r[0]).startswith("duckdb_")
        and not str(r[0]).startswith("pragma_")
        and not str(r[0]).startswith("sqlite_")
        and str(r[0]) != "sqlite_master"
    ]

    dropped = {"views": [], "tables": []}

    for name in sorted(views):
        _safe_ident(name)
        conn.execute(f'DROP VIEW IF EXISTS "{name}";')
        dropped["views"].append(name)

    for name in sorted(tables):
        _safe_ident(name)
        conn.execute(f'DROP TABLE IF EXISTS "{name}";')
        dropped["tables"].append(name)

    return dropped


@dataclass(frozen=True)
class _DummyContext:
    instances: list[str]
    projects: list[str]
    users: list[str]
    groups: list[str]


def _dummy_value(col: str, duck_type: str, i: int, ctx: _DummyContext):
    # Common join keys
    if col == "instance_name":
        return ctx.instances[i % len(ctx.instances)]
    if col == "project_key":
        return ctx.projects[i % len(ctx.projects)]

    if col in {"login", "owner_login", "last_modified_by_login", "project_owner_login", "project_creation_login", "project_last_modified_by_login", "dataset_last_modified_by_login", "recipe_last_modified_by_login", "scenario_run_as_login"}:
        return ctx.users[i % len(ctx.users)]

    if col == "group_name":
        return ctx.groups[i % len(ctx.groups)]

    # Dates/times
    if duck_type == "TIMESTAMP" or col.endswith("_at") or col.endswith("_timestamp") or col == "timestamp":
        return datetime.now(tz=UTC) - timedelta(days=i)
    if duck_type == "DATE" or col == "day":
        return date.today() - timedelta(days=i)

    # Booleans
    if duck_type == "BOOLEAN" or col.startswith("is_") or col.endswith("_active"):
        return i % 2 == 0

    # Numeric
    if duck_type in {"INTEGER", "BIGINT"}:
        return i

    # Type-like columns
    if col in {"object_type", "product_type"}:
        options = [
            "project",
            "dataset",
            "recipe",
            "scenario",
            "api_endpoint",
            "agent",
            "dashboard",
            "web_application",
            "dataiku_application",
            "mlmodel",
        ]
        return options[i % len(options)]

    # IDs / keys / names
    if col.endswith("_id") or col.endswith("_key"):
        return f"{col}_{i}"
    if col.endswith("_name") or col == "object_name" or col == "product_name":
        return f"{col}_{i}"

    # Fallback
    return f"{col}_{i}"


def _create_table(conn: duckdb.DuckDBPyConnection, table_name: str, columns: list[tuple[str, str]]):
    _safe_ident(table_name)
    if not columns:
        # Minimal placeholder
        columns = [("id", "INTEGER")]

    cols_sql = ",\n  ".join([f'"{c}" {t}' for c, t in columns])
    conn.execute(f'CREATE TABLE "{table_name}" (\n  {cols_sql}\n);')


def _insert_dummy_rows(
    conn: duckdb.DuckDBPyConnection,
    table_name: str,
    columns: list[tuple[str, str]],
    *,
    n_rows: int,
    ctx: _DummyContext,
):
    if not columns:
        return

    col_names = [c for c, _ in columns]
    placeholders = ", ".join(["?"] * len(col_names))
    col_list = ", ".join([f'"{c}"' for c in col_names])
    sql = f'INSERT INTO "{table_name}" ({col_list}) VALUES ({placeholders});'  # nosec B608 (table_name validated)

    for i in range(n_rows):
        values = [_dummy_value(c, t, i, ctx) for c, t in columns]
        conn.execute(sql, values)


def _seed_relational_consistency(conn: duckdb.DuckDBPyConnection):
    """Overwrite key tables with more coherent dummy data.

    Generic dummy generation is fine for most tables, but a few need sensible
    keys/relationships so the demo views produce meaningful rows.
    """

    now = datetime.now(tz=UTC)

    # Instances
    conn.execute('DELETE FROM "base_instance_registry";')
    conn.execute(
        """
        INSERT INTO base_instance_registry (instance_name, instance_display_name, instance_env, active)
        VALUES
          ('dss-prod', 'DSS Production', 'prod', true),
          ('dss-dev', 'DSS Development', 'dev', true);
        """
    )

    # Users
    conn.execute('DELETE FROM "base_users";')
    conn.execute(
        """
        INSERT INTO base_users (instance_name, login, display_name, email, enabled, user_profile, last_activity_at)
        VALUES
          ('dss-prod', 'alice', 'Alice', 'alice@example.com', true, 'admin', ?),
          ('dss-prod', 'bob', 'Bob', 'bob@example.com', true, 'data_scientist', ?),
          ('dss-dev', 'carol', 'Carol', 'carol@example.com', true, 'data_engineer', ?);
        """,
        [now - timedelta(days=1), now - timedelta(days=3), now - timedelta(days=2)],
    )

    # Projects
    conn.execute('DELETE FROM "base_projects_metadata";')
    conn.execute(
        """
        INSERT INTO base_projects_metadata (
          instance_name, project_key, project_name,
          project_owner_login, project_owner_display_name,
          project_creation_login, project_last_modified_by_login,
          project_created_at, project_updated_at
         ) VALUES
           ('dss-prod', 'FIN', 'Finance Analytics', 'alice', 'Alice', 'alice', 'bob', ?, ?),
           ('dss-prod', 'MKT', 'Marketing', 'bob', 'Bob', 'bob', 'bob', ?, ?),
           ('dss-dev', 'ENG', 'Engineering', 'carol', 'Carol', 'carol', 'carol', ?, ?);
         """,

        [
            now - timedelta(days=120),
            now - timedelta(days=2),
            now - timedelta(days=200),
            now - timedelta(days=10),
            now - timedelta(days=60),
            now - timedelta(days=1),
        ],
    )

    # Datasets
    conn.execute('DELETE FROM "base_datasets_metadata";')
    conn.execute(
        """
        INSERT INTO base_datasets_metadata (
          instance_name, project_key, dataset_name, dataset_display_name,
          dataset_type, dataset_managed, dataset_last_modified_by_login,
          dataset_created_at, dataset_updated_at
         ) VALUES
           ('dss-prod', 'FIN', 'transactions', 'Transactions', 'Filesystem', true, 'bob', ?, ?),
           ('dss-prod', 'MKT', 'leads', 'Leads', 'SQL', false, 'alice', ?, ?),
           ('dss-dev', 'ENG', 'build_logs', 'Build logs', 'Filesystem', true, 'carol', ?, ?);
         """,

        [
            now - timedelta(days=90),
            now - timedelta(days=2),
            now - timedelta(days=40),
            now - timedelta(days=5),
            now - timedelta(days=20),
            now - timedelta(days=1),
        ],
    )

    # Recipes
    conn.execute('DELETE FROM "base_recipes_metadata";')
    conn.execute(
        """
        INSERT INTO base_recipes_metadata (
          instance_name, project_key, recipe_name, recipe_type,
          recipe_last_modified_by_login, recipe_created_at, recipe_updated_at
         ) VALUES
           ('dss-prod', 'FIN', 'prep_transactions', 'prepare', 'bob', ?, ?),
           ('dss-prod', 'MKT', 'join_leads', 'join', 'alice', ?, ?);
         """,

        [
            now - timedelta(days=40),
            now - timedelta(days=3),
            now - timedelta(days=60),
            now - timedelta(days=12),
        ],
    )

    # Scenarios
    conn.execute('DELETE FROM "base_scenarios_metadata";')
    conn.execute(
        """
        INSERT INTO base_scenarios_metadata (
          instance_name, project_key, scenario_id, scenario_name, scenario_type, scenario_active,
          scenario_run_as_login, scenario_created_at, scenario_updated_at
         ) VALUES
           ('dss-prod', 'FIN', 'scn_fin_refresh', 'Refresh finance', 'step_based', true, 'alice', ?, ?),
           ('dss-dev', 'ENG', 'scn_eng_ci', 'CI checks', 'step_based', true, 'carol', ?, ?);
         """,

        [
            now - timedelta(days=100),
            now - timedelta(days=1),
            now - timedelta(days=30),
            now - timedelta(days=2),
        ],
    )

    # Dev activity events
    conn.execute('DELETE FROM "fact_dev_activity_events";')
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
        base = ["CODE_STUDIO", "DATASET_EDIT", "PREPARE", "MODEL_TRAIN", "PROMPT", "SCENARIO_RUN", "API_SERVICE", "WEBAPP_EDIT"][
            i % 8
        ]
        ts = now - timedelta(days=i % 45)
        rows.append((ts, instance, login, f"{base}_EVENT", base, category, project))

    from shared_duckdb.schemas import insert_sql as _fact_insert_sql

    conn.executemany(
        _fact_insert_sql(
            columns=[
                "timestamp",
                "instance_name",
                "login",
                "msgtype",
                "msgtypebase",
                "dataiku_category",
                "project_key",
            ]
        ),
        rows,
    )

    # Object activity events (for catalog/product activity views)
    conn.execute('DELETE FROM "fact_object_activity_events";')
    conn.execute(
        """
        CREATE OR REPLACE VIEW base_object_activity_events AS
        SELECT *
        FROM fact_object_activity_events;
        """.strip()
    )
    act_rows = []
    object_refs = [
        ("dataset", "transactions", "FIN"),
        ("dataset", "leads", "MKT"),
        ("recipe", "prep_transactions", "FIN"),
        ("scenario", "scn_fin_refresh", "FIN"),
        ("web_application", "webapp_1", "MKT"),
        ("dashboard", "dash_1", "FIN"),
        ("api_endpoint", "api_1", "FIN"),
        ("agent", "agent_1", "ENG"),
        ("dataiku_application", "app_1", "MKT"),
    ]
    for i in range(400):
        ts = now - timedelta(days=i % 35)
        instance = "dss-prod" if i % 4 else "dss-dev"
        login = ["alice", "bob", "carol"][i % 3]
        obj_type, obj_key, project = object_refs[i % len(object_refs)]
        act_rows.append(
            (
                instance,
                ts,
                login,
                "DUMMY_EVENT",
                "Demo",
                "Data Engineering",
                project,
                obj_type,
                obj_key,
                obj_key,
                "{}",
            )
        )

    conn.executemany(
        """
        INSERT INTO fact_object_activity_events (
          instance_name, timestamp, login, event_name, event_category, canonical_capability,
          project_key, object_type, object_key, object_name, details_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        act_rows,

    )

    # Products inventory (so base_product_index/final_build_products_catalog return rows)
    conn.execute('DELETE FROM "base_webapps_metadata";')
    conn.execute(
        """
        INSERT INTO base_webapps_metadata (
          instance_name, project_key, webapp_id, webapp_name, webapp_type, owner_login,
          created_at, updated_at, last_modified_by_login
        ) VALUES
          ('dss-prod', 'MKT', 'webapp_1', 'Lead Explorer', 'standard', 'alice', ?, ?, 'alice');
        """,
        [now - timedelta(days=70), now - timedelta(days=4)],
    )

    conn.execute('DELETE FROM "base_dashboards_metadata";')
    conn.execute(
        """
        INSERT INTO base_dashboards_metadata (
          instance_name, project_key, dashboard_id, dashboard_name, dashboard_kind,
          owner_login, created_at, updated_at, last_modified_by_login
        ) VALUES
          ('dss-prod', 'FIN', 'dash_1', 'Finance KPI', 'dashboard', 'bob', ?, ?, 'bob');
        """,
        [now - timedelta(days=150), now - timedelta(days=9)],
    )

    conn.execute('DELETE FROM "base_api_endpoints_metadata";')
    conn.execute(
        """
        INSERT INTO base_api_endpoints_metadata (
          instance_name, project_key, api_endpoint_id, api_endpoint_name, api_endpoint_kind,
          owner_login, created_at, updated_at, last_modified_by_login
        ) VALUES
          ('dss-prod', 'FIN', 'api_1', 'Predict risk', 'predict', 'alice', ?, ?, 'alice');
        """,
        [now - timedelta(days=110), now - timedelta(days=8)],
    )

    conn.execute('DELETE FROM "base_agents_metadata";')
    conn.execute(
        """
        INSERT INTO base_agents_metadata (
          instance_name, project_key, agent_id, agent_name, agent_type,
          owner_login, created_at, updated_at, last_modified_by_login
        ) VALUES
          ('dss-dev', 'ENG', 'agent_1', 'CI Helper', 'code', 'carol', ?, ?, 'carol');
        """,
        [now - timedelta(days=20), now - timedelta(days=2)],
    )

    conn.execute('DELETE FROM "base_dataiku_applications_metadata";')
    conn.execute(
        """
        INSERT INTO base_dataiku_applications_metadata (
          instance_name, project_key, application_id, application_name, application_kind,
          owner_login, created_at, updated_at, last_modified_by_login
        ) VALUES
          ('dss-prod', 'MKT', 'app_1', 'Marketing App', 'solution', 'alice', ?, ?, 'alice');
        """,
        [now - timedelta(days=200), now - timedelta(days=6)],
    )


def rebuild_dummy_database() -> dict:
    """Drop all objects and rebuild dummy base tables + views."""

    ctx = _DummyContext(
        instances=["dss-prod", "dss-dev"],
        projects=["FIN", "MKT", "ENG"],
        users=["alice", "bob", "carol"],
        groups=["admins", "builders", "viewers"],
    )

    with _duckdb_init_lock():
        conn = create_connection(read_only=False)
        try:
            dropped = reset_duckdb(conn)

            created_tables: list[str] = []
            created_table_set: set[str] = set()

            # Use YAML base-table specs as the schema source of truth.
            for yaml_path in sorted(_BASE_SPECS_DIR.glob("*.yaml")):
                doc = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
                if not isinstance(doc, dict) or len(doc) != 1:
                    raise ValueError(f"Invalid base spec YAML (expected single top-level key): {yaml_path}")
                table_name, payload = next(iter(doc.items()))

                # Some specs are addendums that refer to an existing base table.
                if table_name in created_table_set:
                    continue

                sql = str(payload.get("sql", "") or "").strip()
                if not sql:
                    raise ValueError(f"Missing `sql` in base spec: {yaml_path}")

                conn.execute(sql)
                created_tables.append(table_name)
                created_table_set.add(table_name)

                # Insert dummy rows (independent of spec). We infer columns from the created table.
                cols = conn.execute(
                    "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema='main' AND table_name=? ORDER BY ordinal_position;",
                    [table_name],
                ).fetchall()
                columns = [(c[0], c[1]) for c in cols]

                n_rows = 10
                if table_name in {"fact_dev_activity_events", "fact_object_activity_events"}:
                    n_rows = 50
                _insert_dummy_rows(conn, table_name, columns, n_rows=n_rows, ctx=ctx)

            # Make key tables consistent so joins look good
            missing = [
                t
                for t in [
                    "base_instance_registry",
                    "base_users",
                    "base_projects_metadata",
                    "base_datasets_metadata",
                    "base_recipes_metadata",
                    "base_scenarios_metadata",
                    "dim_category_to_capability",
                    "fact_dev_activity_events",
                    "fact_object_activity_events",
                    "base_webapps_metadata",
                    "base_dashboards_metadata",
                    "base_api_endpoints_metadata",
                    "base_agents_metadata",
                    "base_dataiku_applications_metadata",
                ]
                if t not in set(created_tables)
            ]
            if missing:
                raise RuntimeError(f"Dummy DB build missing required base tables: {missing}")

            _seed_relational_consistency(conn)

            # Compatibility views (so YAML view specs work in dummy mode)
            conn.execute(
                """
                CREATE OR REPLACE VIEW base_object_activity_events AS
                SELECT *
                FROM fact_object_activity_events;
                """.strip()
            )

            # Views: build from YAML specs
            created_views: list[str] = []
            view_errors: list[dict] = []

            from .view_builder import build_views_from_specs

            views_report = build_views_from_specs(conn)
            if not views_report.get("ok", False):
                view_errors = views_report.get("errors", [])

            # Best-effort list of view names
            created_views = [
                r[0]
                for r in conn.execute(
                    "SELECT table_name FROM information_schema.views WHERE table_schema='main' ORDER BY table_name;"
                ).fetchall()
                if not str(r[0]).startswith("duckdb_")
                and not str(r[0]).startswith("pragma_")
                and not str(r[0]).startswith("sqlite_")
                and str(r[0]) != "sqlite_master"
            ]

            return {
                "ok": True,
                "mode": "dummy",
                "db_path": str(settings.DUCKDB_PATH),
                "dropped": dropped,
                "base_tables": len(created_tables),
                "views_docs_processed": len(list(_VIEW_SPECS_DIR.glob("*.yaml"))),
                "created_views": created_views,
                "view_errors": view_errors,
            }
        finally:
            conn.close()
