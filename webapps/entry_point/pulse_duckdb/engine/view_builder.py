"""Create DuckDB views from YAML specs in `pulse_duckdb/datasets/views`.

The `pulse_duckdb/datasets/views/*.yaml` files are the source of truth for view definitions.
This module executes the `sql` field in a dependency-aware way.

Intended usage:
- Load base tables from GOLD (managed folder)
- Then build views from these specs

This is safe to call repeatedly.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import duckdb
import yaml


logger = logging.getLogger(__name__)


_BASE_DIR = Path(__file__).resolve().parents[1]
_VIEW_SPECS_DIR = _BASE_DIR / "datasets" / "views"


def _split_sql_statements(sql: str) -> list[str]:
    return [p.strip() + ";" for p in sql.split(";") if p.strip()]


def _extract_created_view_name(stmt: str) -> str | None:
    m = re.search(r"CREATE\s+OR\s+REPLACE\s+VIEW\s+\"?([A-Za-z0-9_]+)\"?", stmt, flags=re.IGNORECASE)
    return m.group(1) if m else None


def _load_view_specs() -> list[dict]:
    specs: list[dict] = []
    for yaml_path in sorted(_VIEW_SPECS_DIR.glob("*.yaml")):
        doc = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or len(doc) != 1:
            raise ValueError(f"Invalid view spec YAML (expected single top-level key): {yaml_path}")
        view_name, payload = next(iter(doc.items()))
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid view spec payload (expected mapping): {yaml_path}")
        payload = dict(payload)
        payload.setdefault("name", view_name)
        payload.setdefault("_path", str(yaml_path))
        specs.append(payload)
    return specs


def _table_exists(conn: duckdb.DuckDBPyConnection, *, name: str) -> bool:
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_schema='main' AND table_name=?
        """.strip(),
        [name],
    ).fetchone()
    return bool(row and int(row[0]) > 0)


def _column_exists(conn: duckdb.DuckDBPyConnection, *, table: str, column: str) -> bool:
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.columns
        WHERE table_schema='main' AND table_name=? AND column_name=?
        """.strip(),
        [table, column],
    ).fetchone()
    return bool(row and int(row[0]) > 0)


def _sql_ident(name: str) -> str:
    # Identifiers are controlled by our pipeline (table/column names), but keep safe anyway.
    return '"' + str(name).replace('"', '""') + '"'


def _sql_string_literal(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _build_product_index_branch_sql(
    *,
    instance_name_ident: str,
    project_key_ident: str,
    product_type_literal: str,
    key_ident: str,
    name_ident: str,
    subtype_expr: str,
    owner_expr: str,
    last_modified_by_expr: str,
    created_at_expr: str,
    updated_at_expr: str,
    source_table_ident: str,
    where_clause: str,
) -> str:
    lines = [
        "SELECT",
        f"  {instance_name_ident} AS instance_name,",
        f"  {project_key_ident} AS project_key,",
        f"  {product_type_literal} AS product_type,",
        f"  {key_ident} AS product_key,",
        f"  {name_ident} AS product_name,",
        f"  {subtype_expr} AS product_subtype,",
        f"  {owner_expr} AS owner_login,",
        f"  {last_modified_by_expr} AS last_modified_by_login,",
        f"  try_cast({created_at_expr} AS TIMESTAMP) AS created_at,",
        f"  try_cast({updated_at_expr} AS TIMESTAMP) AS updated_at",
        f"FROM {source_table_ident}",
        f"WHERE {where_clause}",
    ]
    return "\n".join(lines)


def _build_base_product_index_view_sql(union_sql: str) -> str:
    lines = [
        'CREATE OR REPLACE VIEW "base_product_index" AS',
        'SELECT instance_name, project_key, product_type, product_key, product_name, product_subtype,',
        '       owner_login, last_modified_by_login, created_at, updated_at',
        'FROM (',
        '  SELECT',
        '    *,',
        '    row_number() OVER (',
        '      PARTITION BY instance_name, project_key, product_type, product_key',
        '      ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST, product_name',
        '    ) AS _rn',
        '  FROM (',
        union_sql,
        '  ) u',
        ') d',
        'WHERE _rn = 1;',
    ]
    return "\n".join(lines)


def _build_v_object_activity_events(conn: duckdb.DuckDBPyConnection) -> dict:
    """Create/replace `v_object_activity_events`.

    Preferred source is the curated GOLD fact table `fact_object_activity_events`.
    If it is missing (eg. customer has not built it yet), fall back to the SILVER
    audit view `v_event_mapping__all` when available.

    This keeps downstream activity rollups (`product_activity_30d`,
    `asset_activity_30d`) from failing hard.
    """

    # 1) Preferred: curated fact table.
    if _table_exists(conn, name="fact_object_activity_events"):
        # dim_category_to_capability may or may not exist; left join defensively.
        if _table_exists(conn, name="dim_category_to_capability") and _column_exists(
            conn, table="dim_category_to_capability", column="dataiku_category"
        ):
            conn.execute(
                """
                CREATE OR REPLACE VIEW "v_object_activity_events" AS
                SELECT
                  e.instance_name,
                  e.timestamp,
                  e.login,
                  e.event_name,
                  e.event_category,
                  m.capability AS canonical_capability,
                  e.project_key,
                  e.object_type,
                  e.object_key,
                  e.object_name,
                  e.instance_url,
                  e.group_names,
                  e.session_id,
                  e.ip_address,
                  e.user_agent,
                  e.details_json,
                  NULL::TIMESTAMP AS run_timestamp
                FROM fact_object_activity_events e
                LEFT JOIN dim_category_to_capability m
                  ON m.dataiku_category = e.event_category;
                """.strip()
            )
        else:
            conn.execute(
                """
                CREATE OR REPLACE VIEW "v_object_activity_events" AS
                SELECT
                  instance_name,
                  timestamp,
                  login,
                  event_name,
                  event_category,
                  NULL::VARCHAR AS canonical_capability,
                  project_key,
                  object_type,
                  object_key,
                  object_name,
                  instance_url,
                  group_names,
                  session_id,
                  ip_address,
                  user_agent,
                  details_json,
                  NULL::TIMESTAMP AS run_timestamp
                FROM fact_object_activity_events;
                """.strip()
            )

        return {"ok": True, "enabled": True, "source": "fact_object_activity_events"}

    # 2) Fallback: raw audit mapping view (project-level only).
    if _table_exists(conn, name="v_event_mapping__all"):
        conn.execute(
            """
            CREATE OR REPLACE VIEW "v_object_activity_events" AS
            SELECT
              instance_name,
              COALESCE(timestamp, date) AS timestamp,
              COALESCE(authuser, user) AS login,
              msgtype AS event_name,
              dataiku_category AS event_category,
              NULL::VARCHAR AS canonical_capability,
              project_key,
              'project' AS object_type,
              project_key AS object_key,
              NULL::VARCHAR AS object_name,
              NULL::VARCHAR AS instance_url,
              NULL::VARCHAR AS group_names,
              NULL::VARCHAR AS session_id,
              clientip AS ip_address,
              NULL::VARCHAR AS user_agent,
              extras AS details_json,
              NULL::TIMESTAMP AS run_timestamp
            FROM v_event_mapping__all
            WHERE project_key IS NOT NULL;
            """.strip()
        )
        return {"ok": True, "enabled": True, "source": "v_event_mapping__all"}

    # 3) Empty placeholder (so view builds never fail).
    conn.execute(
        """
        CREATE OR REPLACE VIEW "v_object_activity_events" AS
        SELECT
          CAST(NULL AS VARCHAR) AS instance_name,
          CAST(NULL AS TIMESTAMP) AS timestamp,
          CAST(NULL AS VARCHAR) AS login,
          CAST(NULL AS VARCHAR) AS event_name,
          CAST(NULL AS VARCHAR) AS event_category,
          CAST(NULL AS VARCHAR) AS canonical_capability,
          CAST(NULL AS VARCHAR) AS project_key,
          CAST(NULL AS VARCHAR) AS object_type,
          CAST(NULL AS VARCHAR) AS object_key,
          CAST(NULL AS VARCHAR) AS object_name,
          CAST(NULL AS VARCHAR) AS instance_url,
          CAST(NULL AS VARCHAR) AS group_names,
          CAST(NULL AS VARCHAR) AS session_id,
          CAST(NULL AS VARCHAR) AS ip_address,
          CAST(NULL AS VARCHAR) AS user_agent,
          CAST(NULL AS VARCHAR) AS details_json,
          CAST(NULL AS TIMESTAMP) AS run_timestamp
        WHERE 1=0;
        """.strip()
    )
    return {"ok": True, "enabled": True, "source": "empty"}


def _build_base_product_index(conn: duckdb.DuckDBPyConnection) -> dict:
    """Create/replace `base_product_index` from `base_dataiku_products_registry`.

    Customers should have an identical registry mapping table (shipped with the plugin),
    but their instance may not contain every product-type table.

    Behavior:
    - If the registry table is missing: do nothing (YAML spec will apply).
    - For each registry row: include it only if its `source_table` exists and
      required columns exist.
    - Optional columns that don't exist are replaced with NULL.
    - Final view is de-duplicated on (instance_name, project_key, product_type, product_key)
      to avoid showcasing the same "id" multiple times.
    """

    registry_table = "base_dataiku_products_registry"
    if not _table_exists(conn, name=registry_table):
        return {"ok": True, "enabled": False, "reason": "registry_missing"}

    registry_table_ident = _sql_ident(registry_table)
    sql = "\n".join(
        [
            "SELECT",
            "  product_type,",
            "  source_table,",
            "  instance_name_col,",
            "  project_key_col,",
            "  key_col,",
            "  name_col,",
            "  subtype_col,",
            "  owner_col,",
            "  last_modified_by_col,",
            "  created_at_col,",
            "  updated_at_col,",
            "  where_sql",
            f"FROM {registry_table_ident}",
            "ORDER BY product_type;",
        ]
    )
    rows = conn.execute(sql).fetchall()

    included: list[dict] = []
    skipped: list[dict] = []
    optional_missing: list[dict] = []
    branches: list[str] = []

    def _is_nullish(v: object) -> bool:
        return v is None or str(v).strip() == "" or str(v).strip().lower() == "null"

    def _optional_col(table: str, col: object, *, label: str) -> str:
        if _is_nullish(col):
            return "NULL"
        col_name = str(col).strip()
        if _column_exists(conn, table=table, column=col_name):
            return _sql_ident(col_name)
        optional_missing.append({"table": table, "column": col_name, "field": label})
        return "NULL"

    for (
        product_type,
        source_table,
        instance_name_col,
        project_key_col,
        key_col,
        name_col,
        subtype_col,
        owner_col,
        last_modified_by_col,
        created_at_col,
        updated_at_col,
        where_sql,
    ) in rows:
        product_type = str(product_type or "").strip()
        source_table = str(source_table or "").strip()

        required_mapping = {
            "instance_name_col": instance_name_col,
            "project_key_col": project_key_col,
            "key_col": key_col,
            "name_col": name_col,
        }
        missing_mapping = [k for k, v in required_mapping.items() if _is_nullish(v)]
        if not product_type or not source_table or missing_mapping:
            skipped.append(
                {
                    "product_type": product_type or None,
                    "source_table": source_table or None,
                    "reason": "missing_mapping",
                    "missing": missing_mapping,
                }
            )
            continue

        if not _table_exists(conn, name=source_table):
            skipped.append({"product_type": product_type, "source_table": source_table, "reason": "missing_table"})
            continue

        required_cols = [str(instance_name_col).strip(), str(project_key_col).strip(), str(key_col).strip(), str(name_col).strip()]
        missing_cols = [c for c in required_cols if not _column_exists(conn, table=source_table, column=c)]
        if missing_cols:
            skipped.append(
                {
                    "product_type": product_type,
                    "source_table": source_table,
                    "reason": "missing_columns",
                    "missing": missing_cols,
                }
            )
            continue

        where = "1=1" if _is_nullish(where_sql) else str(where_sql).strip()

        branch = _build_product_index_branch_sql(
            instance_name_ident=_sql_ident(str(instance_name_col).strip()),
            project_key_ident=_sql_ident(str(project_key_col).strip()),
            product_type_literal=_sql_string_literal(product_type),
            key_ident=_sql_ident(str(key_col).strip()),
            name_ident=_sql_ident(str(name_col).strip()),
            subtype_expr=_optional_col(source_table, subtype_col, label="subtype_col"),
            owner_expr=_optional_col(source_table, owner_col, label="owner_col"),
            last_modified_by_expr=_optional_col(source_table, last_modified_by_col, label="last_modified_by_col"),
            created_at_expr=_optional_col(source_table, created_at_col, label="created_at_col"),
            updated_at_expr=_optional_col(source_table, updated_at_col, label="updated_at_col"),
            source_table_ident=_sql_ident(source_table),
            where_clause=where,
        )

        branches.append(branch)
        included.append({"product_type": product_type, "source_table": source_table})

    if not branches:
        # Important: create an empty view so downstream views can be created and queried.
        conn.execute(
            """
            CREATE OR REPLACE VIEW "base_product_index" AS
            SELECT
              CAST(NULL AS VARCHAR) AS instance_name,
              CAST(NULL AS VARCHAR) AS project_key,
              CAST(NULL AS VARCHAR) AS product_type,
              CAST(NULL AS VARCHAR) AS product_key,
              CAST(NULL AS VARCHAR) AS product_name,
              CAST(NULL AS VARCHAR) AS product_subtype,
              CAST(NULL AS VARCHAR) AS owner_login,
              CAST(NULL AS VARCHAR) AS last_modified_by_login,
              CAST(NULL AS TIMESTAMP) AS created_at,
              CAST(NULL AS TIMESTAMP) AS updated_at
            WHERE 1=0;
            """.strip()
        )
        return {
            "ok": True,
            "enabled": True,
            "created": True,
            "branches": 0,
            "included": included,
            "skipped": skipped,
            "optional_missing": optional_missing,
            "reason": "no_branches",
        }

    union_sql = "\nUNION ALL\n".join(branches)

    # De-dupe by key so we don't showcase the same product multiple times.
    sql = _build_base_product_index_view_sql(union_sql)

    conn.execute(sql)

    return {
        "ok": True,
        "enabled": True,
        "created": True,
        "branches": len(branches),
        "included": included,
        "skipped": skipped,
        "optional_missing": optional_missing,
    }


def build_views_from_specs(conn: duckdb.DuckDBPyConnection) -> dict:
    """Execute all view SQL from `pulse_duckdb/datasets/views/*.yaml`.

    If an existing object has the same name but is a TABLE (eg. mistakenly loaded
    from CSV), we drop it before creating the view.

    If `base_dataiku_products_registry` exists, we generate `base_product_index`
    dynamically from it, and skip any YAML statement that would overwrite it.
    """

    object_activity_report = _build_v_object_activity_events(conn)
    product_index_report = _build_base_product_index(conn)

    specs = _load_view_specs()

    view_statements: list[tuple[str, str]] = []
    for spec in specs:
        sql = str(spec.get("sql", "") or "").strip()
        name = str(spec.get("name"))
        path = str(spec.get("_path"))

        if not sql:
            raise ValueError(f"Missing `sql` in view spec: {path}")

        for stmt in _split_sql_statements(sql):
            created_view = _extract_created_view_name(stmt)
            if (
                created_view == "base_product_index"
                and product_index_report.get("enabled")
                and product_index_report.get("created")
            ):
                continue

            if created_view == "v_object_activity_events" and object_activity_report.get("enabled"):
                continue
            view_statements.append((f"{name} ({path})", stmt))

    created: list[str] = []
    pending = list(view_statements)
    max_passes = 10

    for _ in range(max_passes):
        if not pending:
            break

        progressed = False
        next_pending: list[tuple[str, str]] = []

        for spec_name, stmt in pending:
            try:
                view_name = _extract_created_view_name(stmt)
                if view_name:
                    try:
                        conn.execute(f'DROP TABLE "{view_name}";')
                    except Exception:
                        pass

                conn.execute(stmt)
                progressed = True
                created.append(spec_name)
            except Exception:
                next_pending.append((spec_name, stmt))

        if not progressed:
            pending = next_pending
            break

        pending = next_pending

    errors = []
    for spec_name, stmt in pending:
        try:
            conn.execute(stmt)
        except Exception as e:
            errors.append({"spec": spec_name, "statement": stmt[:200], "error": str(e)})

    return {
        "ok": len(errors) == 0,
        "spec_files": len(list(_VIEW_SPECS_DIR.glob("*.yaml"))),
        "statements": len(view_statements),
        "errors": errors,
        "v_object_activity_events": object_activity_report,
        "base_product_index": product_index_report,
    }
