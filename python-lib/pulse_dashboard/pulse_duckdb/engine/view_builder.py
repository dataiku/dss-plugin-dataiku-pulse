"""Create DuckDB views from YAML specs in `pulse_duckdb/datasets/base/views`.

The `pulse_duckdb/datasets/base/views/*.yaml` files are the source of truth for view definitions.
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
_VIEW_SPECS_DIR = _BASE_DIR / "datasets" / "base" / "views"


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


def _view_exists(conn: duckdb.DuckDBPyConnection, *, name: str) -> bool:
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.views
        WHERE table_schema='main' AND table_name=?
        """.strip(),
        [name],
    ).fetchone()
    return bool(row and int(row[0]) > 0)


def _relation_exists(conn: duckdb.DuckDBPyConnection, *, name: str) -> bool:
    return _table_exists(conn, name=name) or _view_exists(conn, name=name)


def _sql_ident(name: str) -> str:
    # identifiers in our pipeline are controlled (table/column names), but keep it safe anyway
    return '"' + str(name).replace('"', '""') + '"'


def _build_base_product_index(conn: duckdb.DuckDBPyConnection) -> dict:
    """Create/replace `base_product_index` from `base_dataiku_products_registry`.

    If the registry doesn't exist, we do nothing and rely on the YAML view spec.
    If a referenced source table/column doesn't exist, that product is skipped.
    """

    registry_table = "base_dataiku_products_registry"
    if not _table_exists(conn, name=registry_table):
        return {"ok": True, "enabled": False, "reason": "registry_missing"}

    rows = conn.execute(
        f"""
        SELECT
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
          where_sql
        FROM {_sql_ident(registry_table)}
        ORDER BY product_type;
        """.strip()  # nosec B608 (registry_table is fixed)
    ).fetchall()

    included: list[dict] = []
    skipped: list[dict] = []
    branches: list[str] = []

    def _table_row_count(name: str) -> int:
        if not _table_exists(conn, name=name):
            return 0
        row = conn.execute(
            f"SELECT COUNT(*) FROM {_sql_ident(name)};"  # nosec B608 (name is validated via information_schema)
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def _maybe_col(expr: str | None) -> str:
        if not expr or not str(expr).strip() or str(expr).strip().lower() == "null":
            return "NULL"
        return _sql_ident(str(expr).strip())

    def _non_empty_ident(expr: str | None) -> str:
        ident = _maybe_col(expr)
        if ident == "NULL":
            return "NULL"
        return f"NULLIF(TRIM(CAST({ident} AS VARCHAR)), '')"

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
        missing_mapping = [k for k, v in required_mapping.items() if not v or str(v).strip().lower() == "null"]
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

        if product_type == "web_application" and source_table == "base_webapps_project_metadata_history":
            history_rows = _table_row_count(source_table)
            snapshot_rows = _table_row_count("base_webapps_metadata")
            if snapshot_rows > history_rows:
                logger.info(
                    "base_product_index: using base_webapps_metadata instead of %s for web_application (%s snapshot rows > %s history rows)",
                    source_table,
                    snapshot_rows,
                    history_rows,
                )
                source_table = "base_webapps_metadata"
                owner_col = None
                last_modified_by_col = "webapps_lastmodifiedby_login"
                created_at_col = "webapps_createdon"
                updated_at_col = "webapps_lastmodifiedon"

        if not _table_exists(conn, name=source_table):
            skipped.append({"product_type": product_type, "source_table": source_table, "reason": "missing_table"})
            continue

        required_cols = [instance_name_col, project_key_col, key_col, name_col]
        missing_cols = [
            c for c in required_cols if c and not _column_exists(conn, table=source_table, column=str(c))
        ]
        if missing_cols:
            skipped.append(
                {
                    "product_type": product_type,
                    "source_table": source_table,
                    "reason": "missing_columns",
                    "missing": [str(c) for c in missing_cols],
                }
            )
            continue

        where = str(where_sql).strip() if where_sql not in (None, "", "null") else "1=1"

        owner_expr = _maybe_col(owner_col)
        if product_type == "web_application":
            owner_expr = f"COALESCE({_non_empty_ident(owner_col)}, {_non_empty_ident(last_modified_by_col)})"

        branch = (
            "SELECT\n"
            f"  {_sql_ident(str(instance_name_col))} AS instance_name,\n"  # nosec B608 (identifiers are validated)
            f"  {_sql_ident(str(project_key_col))} AS project_key,\n"  # nosec B608 (identifiers are validated)
            f"  '{product_type}' AS product_type,\n"  # nosec B608 (product_type from registry)
            f"  {_sql_ident(str(key_col))} AS product_key,\n"  # nosec B608 (identifiers are validated)
            f"  {_sql_ident(str(name_col))} AS product_name,\n"  # nosec B608 (identifiers are validated)
            f"  {_maybe_col(subtype_col)} AS product_subtype,\n"  # nosec B608 (identifiers are validated)
            f"  {owner_expr} AS owner_login,\n"  # nosec B608 (identifiers are validated)
            f"  {_maybe_col(last_modified_by_col)} AS last_modified_by_login,\n"  # nosec B608 (identifiers are validated)
            f"  try_cast({_maybe_col(created_at_col)} AS TIMESTAMP) AS created_at,\n"  # nosec B608 (identifiers are validated)
            f"  try_cast({_maybe_col(updated_at_col)} AS TIMESTAMP) AS updated_at\n"  # nosec B608 (identifiers are validated)
            f"FROM {_sql_ident(source_table)}\n"  # nosec B608 (table is validated)
            f"WHERE {where}"  # nosec B608 (where_sql comes from registry config)
        )

        branches.append(branch)
        included.append({"product_type": product_type, "source_table": source_table})

    if not branches:
        logger.info("base_product_index: no registry branches included")
        return {"ok": True, "enabled": True, "created": False, "included": included, "skipped": skipped}

    sql = f"CREATE OR REPLACE VIEW base_product_index AS\n" + "\nUNION ALL\n".join(branches) + ";"  # nosec B608 (sql built from validated identifiers)
    conn.execute(sql)

    return {
        "ok": True,
        "enabled": True,
        "created": True,
        "included": included,
        "skipped": skipped,
        "branches": len(branches),
    }


def build_views_from_specs(conn: duckdb.DuckDBPyConnection) -> dict:
    """Execute all view SQL from `pulse_duckdb/datasets/base/views/*.yaml`.

    If an existing object has the same name but is a TABLE (eg. mistakenly loaded
    from CSV), we drop it before creating the view.

    Some views can be generated dynamically from plugin-owned configs. When that
    succeeds, we skip the static YAML definitions for those views.
    """

    # If present, prefer generating this view dynamically from the registry.
    product_index_report = _build_base_product_index(conn)

    config_reports: dict[str, dict] = {}
    try:
        from .config_driven_views import build_base_asset_index
        from .config_driven_views import build_base_product_index as build_base_product_index_from_config
        from .config_driven_views import build_product_activity_30d
        from .config_driven_views import validate_configs

        config_reports["validation"] = validate_configs()
        config_reports["base_asset_index"] = build_base_asset_index(conn)

        # If the products registry successfully built `base_product_index`, don't
        # overwrite it with the config-driven version.
        if product_index_report.get("enabled") and product_index_report.get("ok") and product_index_report.get("created"):
            config_reports["base_product_index"] = {
                "ok": True,
                "enabled": True,
                "created": False,
                "reason": "skipped_due_to_registry",
            }
        else:
            config_reports["base_product_index"] = build_base_product_index_from_config(conn)

        config_reports["product_activity_30d"] = build_product_activity_30d(conn)
    except Exception as e:
        logger.exception("config-driven view generation failed")
        config_reports["error"] = {"ok": False, "error": str(e)}

    specs = _load_view_specs()

    view_statements: list[tuple[str, str]] = []
    skipped: list[dict[str, object]] = []
    for spec in specs:
        sql = str(spec.get("sql", "") or "").strip()
        name = str(spec.get("name"))
        path = str(spec.get("_path"))
        depends_on = spec.get("depends_on") or []

        # If we successfully generated `base_product_index` from the registry,
        # avoid overwriting it with the static YAML version (which may depend on
        # tables not present in all deployments).
        if (
            name == "base_product_index"
            and product_index_report.get("enabled")
            and product_index_report.get("ok")
            and product_index_report.get("created")
        ):
            continue

        # Prefer config-driven generators when they succeed.
        if name in {"base_asset_index", "base_product_index", "product_activity_30d"}:
            report = config_reports.get(name) or {}
            if report.get("ok") and report.get("created"):
                continue

        if not sql:
            raise ValueError(f"Missing `sql` in view spec: {path}")

        if isinstance(depends_on, list):
            missing_deps = [
                str(dep)
                for dep in depends_on
                if str(dep).strip() and not _relation_exists(conn, name=str(dep).strip())
            ]
            if missing_deps:
                skipped.append(
                    {
                        "spec": f"{name} ({path})",
                        "reason": "missing_dependencies",
                        "missing": missing_deps,
                    }
                )
                logger.info("Skipping view spec %s due to missing dependencies: %s", name, missing_deps)
                continue

        for stmt in _split_sql_statements(sql):
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
        "skipped": skipped,
        "errors": errors,
        "base_product_index": product_index_report,
        "config_driven": config_reports,
    }
