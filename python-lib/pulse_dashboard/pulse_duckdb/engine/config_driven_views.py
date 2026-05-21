"""Config-driven view generation.

These YAML configs are owned by the plugin (not end-users) and provide a single
source of truth for:
- which object types are considered assets vs products
- how each type maps to base inventory tables

The output schemas must remain stable because the dashboard UI queries:
- final_build_catalog (assets)
- final_build_products_catalog (products)

This module generates:
- base_asset_index
- base_product_index
- product_activity_30d

All generation is best-effort: if a referenced table/column is missing we skip
that branch and log the reason.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import yaml


logger = logging.getLogger(__name__)


_CONFIGS_DIR = Path(__file__).resolve().parents[2] / "configs"


def _sql_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


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


def _load_yaml(path: Path) -> dict[str, Any]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"Invalid YAML (expected mapping): {path}")
    return doc


@dataclass(frozen=True)
class _TypeSpec:
    type_name: str
    table: str
    key_expr: Any
    name_expr: Any
    subtype_expr: Any
    owner_expr: Any
    last_modified_by_expr: Any
    created_at_expr: Any
    updated_at_expr: Any


def _coalesce_expr(expr: Any) -> tuple[str, list[str]]:
    """Return SQL expression + referenced columns."""

    if expr is None:
        return "NULL", []

    if isinstance(expr, list):
        cols = [str(e).strip() for e in expr if str(e).strip()]
        if not cols:
            return "NULL", []
        return "COALESCE(" + ", ".join(_sql_ident(c) for c in cols) + ")", cols

    # string or other scalar
    s = str(expr).strip()
    if not s or s.lower() == "null":
        return "NULL", []
    return _sql_ident(s), [s]


def _required_cols_for_assets(spec: _TypeSpec) -> list[str]:
    cols: list[str] = []
    for e in [spec.key_expr, spec.name_expr, spec.subtype_expr, spec.owner_expr, spec.last_modified_by_expr]:
        _, used = _coalesce_expr(e)
        cols.extend(used)
    for e in [spec.created_at_expr, spec.updated_at_expr]:
        _, used = _coalesce_expr(e)
        cols.extend(used)
    # instance_name/project_key are mandatory for the UI.
    cols.extend(["instance_name", "project_key"])
    # de-dupe preserving order
    out: list[str] = []
    seen: set[str] = set()
    for c in cols:
        if c not in seen:
            out.append(c)
            seen.add(c)
    return out


def _required_cols_for_products(spec: _TypeSpec) -> list[str]:
    cols: list[str] = []
    for e in [spec.key_expr, spec.name_expr, spec.subtype_expr, spec.owner_expr, spec.last_modified_by_expr]:
        _, used = _coalesce_expr(e)
        cols.extend(used)
    for e in [spec.created_at_expr, spec.updated_at_expr]:
        _, used = _coalesce_expr(e)
        cols.extend(used)
    cols.extend(["instance_name", "project_key"])
    out: list[str] = []
    seen: set[str] = set()
    for c in cols:
        if c not in seen:
            out.append(c)
            seen.add(c)
    return out


def _load_terminology() -> dict[str, list[str]]:
    path = _CONFIGS_DIR / "terminology.yaml"
    doc = _load_yaml(path)

    def _list(name: str) -> list[str]:
        v = doc.get(name, [])
        if v is None:
            return []
        if not isinstance(v, list):
            raise ValueError(f"Invalid {name} in {path} (expected list)")
        out = []
        for item in v:
            s = str(item).strip()
            if s:
                out.append(s)
        return out

    return {"assets": _list("assets"), "products": _list("products")}


def _load_structure(*, kind: str) -> dict[str, dict[str, Any]]:
    path = _CONFIGS_DIR / f"{kind}_structure.yaml"
    doc = _load_yaml(path)
    out: dict[str, dict[str, Any]] = {}
    for k, v in doc.items():
        if not isinstance(v, dict):
            raise ValueError(f"Invalid entry for {k} in {path} (expected mapping)")
        out[str(k).strip()] = dict(v)
    return out


def validate_configs() -> dict[str, Any]:
    """Validate taxonomy/structure alignment and log warnings.

    This is meant to be lightweight and safe in all environments.
    """

    terminology = _load_terminology()
    assets = [t for t in terminology.get("assets", []) if t]
    products = [t for t in terminology.get("products", []) if t]

    asset_structure = _load_structure(kind="asset")
    product_structure = _load_structure(kind="product")

    missing_asset_mappings = [t for t in assets if t not in asset_structure]
    missing_product_mappings = [t for t in products if t not in product_structure]

    unused_asset_mappings = [t for t in asset_structure.keys() if t not in set(assets)]
    unused_product_mappings = [t for t in product_structure.keys() if t not in set(products)]

    overlap = sorted(set(assets).intersection(products))

    if not assets:
        logger.warning("terminology.yaml has no assets")
    if not products:
        logger.warning("terminology.yaml has no products")

    if overlap:
        logger.warning("terminology.yaml types present in both assets and products: %s", overlap)

    for t in missing_asset_mappings:
        logger.warning("asset type missing in asset_structure.yaml: %s", t)
    for t in missing_product_mappings:
        logger.warning("product type missing in product_structure.yaml: %s", t)

    # Unused mappings aren't wrong, but they're good to track.
    if unused_asset_mappings:
        logger.info("asset_structure.yaml has unused mappings: %s", sorted(unused_asset_mappings))
    if unused_product_mappings:
        logger.info("product_structure.yaml has unused mappings: %s", sorted(unused_product_mappings))

    return {
        "ok": True,
        "assets": assets,
        "products": products,
        "overlap": overlap,
        "missing": {"assets": sorted(missing_asset_mappings), "products": sorted(missing_product_mappings)},
        "unused": {"assets": sorted(unused_asset_mappings), "products": sorted(unused_product_mappings)},
    }


def build_base_asset_index(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    terminology = _load_terminology()
    assets = terminology.get("assets", [])
    structure = _load_structure(kind="asset")

    included: list[dict[str, str]] = []
    skipped: list[dict[str, Any]] = []
    branches: list[str] = []

    for type_name in assets:
        entry = structure.get(type_name)
        if entry is None:
            skipped.append({"object_type": type_name, "reason": "missing_structure"})
            logger.warning("asset type missing in asset_structure.yaml: %s", type_name)
            continue

        table = str(entry.get("table") or "").strip()
        if not table:
            skipped.append({"object_type": type_name, "reason": "missing_table"})
            logger.warning("asset type %s missing table mapping", type_name)
            continue

        spec = _TypeSpec(
            type_name=type_name,
            table=table,
            key_expr=entry.get("object_key"),
            name_expr=entry.get("object_name"),
            subtype_expr=entry.get("object_subtype"),
            owner_expr=entry.get("owner_login"),
            last_modified_by_expr=entry.get("last_modified_by_login"),
            created_at_expr=entry.get("created_at"),
            updated_at_expr=entry.get("updated_at"),
        )

        if not _table_exists(conn, name=table):
            skipped.append({"object_type": type_name, "table": table, "reason": "missing_table"})
            logger.info("asset type %s skipped; missing table: %s", type_name, table)
            continue

        required = _required_cols_for_assets(spec)
        missing_cols = [c for c in required if not _column_exists(conn, table=table, column=c)]
        if missing_cols:
            skipped.append(
                {"object_type": type_name, "table": table, "reason": "missing_columns", "missing": missing_cols}
            )
            logger.info("asset type %s skipped; missing columns in %s: %s", type_name, table, missing_cols)
            continue

        key_sql, _ = _coalesce_expr(spec.key_expr)
        name_sql, _ = _coalesce_expr(spec.name_expr)
        subtype_sql, _ = _coalesce_expr(spec.subtype_expr)
        owner_sql, _ = _coalesce_expr(spec.owner_expr)
        lmb_sql, _ = _coalesce_expr(spec.last_modified_by_expr)
        created_sql, _ = _coalesce_expr(spec.created_at_expr)
        updated_sql, _ = _coalesce_expr(spec.updated_at_expr)

        branch = (
            "SELECT\n"
            "  instance_name,\n"
            "  project_key,\n"
            f"  '{type_name}' AS object_type,\n"  # nosec B608 (type_name is plugin config)
            f"  {key_sql} AS object_key,\n"  # nosec B608 (validated identifiers)
            f"  {name_sql} AS object_name,\n"  # nosec B608 (validated identifiers)
            f"  {subtype_sql} AS object_subtype,\n"  # nosec B608 (validated identifiers)
            f"  {owner_sql} AS owner_login,\n"  # nosec B608 (validated identifiers)
            f"  {lmb_sql} AS last_modified_by_login,\n"  # nosec B608 (validated identifiers)
            f"  try_cast({created_sql} AS TIMESTAMP) AS created_at,\n"  # nosec B608 (validated identifiers)
            f"  try_cast({updated_sql} AS TIMESTAMP) AS updated_at\n"  # nosec B608 (validated identifiers)
            f"FROM {_sql_ident(table)}"  # nosec B608 (table is validated)
        )
        branches.append(branch)
        included.append({"object_type": type_name, "table": table})

    if not branches:
        # Create an empty view with the expected schema.
        conn.execute(
            """
            CREATE OR REPLACE VIEW base_asset_index AS
            SELECT
              CAST(NULL AS VARCHAR) AS instance_name,
              CAST(NULL AS VARCHAR) AS project_key,
              CAST(NULL AS VARCHAR) AS object_type,
              CAST(NULL AS VARCHAR) AS object_key,
              CAST(NULL AS VARCHAR) AS object_name,
              CAST(NULL AS VARCHAR) AS object_subtype,
              CAST(NULL AS VARCHAR) AS owner_login,
              CAST(NULL AS VARCHAR) AS last_modified_by_login,
              CAST(NULL AS TIMESTAMP) AS created_at,
              CAST(NULL AS TIMESTAMP) AS updated_at
            WHERE 1=0;
            """.strip()
        )
        return {"ok": True, "enabled": True, "created": True, "branches": 0, "included": included, "skipped": skipped}

    sql = "CREATE OR REPLACE VIEW base_asset_index AS\n" + "\nUNION ALL\n".join(branches) + ";"
    conn.execute(sql)

    return {
        "ok": True,
        "enabled": True,
        "created": True,
        "branches": len(branches),
        "included": included,
        "skipped": skipped,
    }


def build_base_product_index(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    terminology = _load_terminology()
    products = terminology.get("products", [])
    structure = _load_structure(kind="product")

    included: list[dict[str, str]] = []
    skipped: list[dict[str, Any]] = []
    branches: list[str] = []

    for type_name in products:
        entry = structure.get(type_name)
        if entry is None:
            skipped.append({"product_type": type_name, "reason": "missing_structure"})
            logger.warning("product type missing in product_structure.yaml: %s", type_name)
            continue

        table = str(entry.get("table") or "").strip()
        if not table:
            skipped.append({"product_type": type_name, "reason": "missing_table"})
            logger.warning("product type %s missing table mapping", type_name)
            continue

        spec = _TypeSpec(
            type_name=type_name,
            table=table,
            key_expr=entry.get("product_key"),
            name_expr=entry.get("product_name"),
            subtype_expr=entry.get("product_subtype"),
            owner_expr=entry.get("owner_login"),
            last_modified_by_expr=entry.get("last_modified_by_login"),
            created_at_expr=entry.get("created_at"),
            updated_at_expr=entry.get("updated_at"),
        )

        if not _table_exists(conn, name=table):
            skipped.append({"product_type": type_name, "table": table, "reason": "missing_table"})
            logger.info("product type %s skipped; missing table: %s", type_name, table)
            continue

        required = _required_cols_for_products(spec)
        missing_cols = [c for c in required if not _column_exists(conn, table=table, column=c)]
        if missing_cols:
            skipped.append(
                {"product_type": type_name, "table": table, "reason": "missing_columns", "missing": missing_cols}
            )
            logger.info("product type %s skipped; missing columns in %s: %s", type_name, table, missing_cols)
            continue

        key_sql, _ = _coalesce_expr(spec.key_expr)
        name_sql, _ = _coalesce_expr(spec.name_expr)
        subtype_sql, _ = _coalesce_expr(spec.subtype_expr)
        owner_sql, _ = _coalesce_expr(spec.owner_expr)
        lmb_sql, _ = _coalesce_expr(spec.last_modified_by_expr)
        created_sql, _ = _coalesce_expr(spec.created_at_expr)
        updated_sql, _ = _coalesce_expr(spec.updated_at_expr)

        branch = (
            "SELECT\n"
            "  instance_name,\n"
            "  project_key,\n"
            f"  '{type_name}' AS product_type,\n"  # nosec B608 (type_name is plugin config)
            f"  {key_sql} AS product_key,\n"  # nosec B608 (validated identifiers)
            f"  {name_sql} AS product_name,\n"  # nosec B608 (validated identifiers)
            f"  {subtype_sql} AS product_subtype,\n"  # nosec B608 (validated identifiers)
            f"  {owner_sql} AS owner_login,\n"  # nosec B608 (validated identifiers)
            f"  {lmb_sql} AS last_modified_by_login,\n"  # nosec B608 (validated identifiers)
            f"  try_cast({created_sql} AS TIMESTAMP) AS created_at,\n"  # nosec B608 (validated identifiers)
            f"  try_cast({updated_sql} AS TIMESTAMP) AS updated_at\n"  # nosec B608 (validated identifiers)
            f"FROM {_sql_ident(table)}"  # nosec B608 (table is validated)
        )

        branches.append(branch)
        included.append({"product_type": type_name, "table": table})

    if not branches:
        conn.execute(
            """
            CREATE OR REPLACE VIEW base_product_index AS
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
        return {"ok": True, "enabled": True, "created": True, "branches": 0, "included": included, "skipped": skipped}

    sql = "CREATE OR REPLACE VIEW base_product_index AS\n" + "\nUNION ALL\n".join(branches) + ";"
    conn.execute(sql)

    return {
        "ok": True,
        "enabled": True,
        "created": True,
        "branches": len(branches),
        "included": included,
        "skipped": skipped,
    }


def build_product_activity_30d(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    terminology = _load_terminology()
    products = [p for p in terminology.get("products", []) if p]

    if not products:
        logger.warning("terminology products list is empty; product_activity_30d will be empty")

    quoted = ", ".join("'" + p.replace("'", "''") + "'" for p in products)

    # Build from `base_object_activity_events` (compat view ensured by init_db).
    # We avoid depending on `v_object_activity_events` ordering within the YAML view specs.
    activity_source = "base_object_activity_events"
    if not _table_exists(conn, name=activity_source):
        logger.info("%s missing; product_activity_30d will be empty", activity_source)
        conn.execute(
            """
            CREATE OR REPLACE VIEW product_activity_30d AS
            SELECT
              CAST(NULL AS VARCHAR) AS instance_name,
              CAST(NULL AS VARCHAR) AS project_key,
              CAST(NULL AS VARCHAR) AS product_type,
              CAST(NULL AS VARCHAR) AS product_key,
              CAST(NULL AS BIGINT) AS activity_30d,
              CAST(NULL AS BIGINT) AS active_users_30d,
              CAST(NULL AS TIMESTAMP) AS last_activity_at
            WHERE 1=0;
            """.strip()
        )
        return {"ok": True, "enabled": True, "created": True, "reason": f"missing_{activity_source}"}

    where_in = f"object_type IN ({quoted})" if products else "1=0"

    conn.execute(
        (
            "CREATE OR REPLACE VIEW product_activity_30d AS\n"
            "SELECT\n"
            "  instance_name,\n"
            "  project_key,\n"
            "  object_type AS product_type,\n"
            "  object_key AS product_key,\n"
            "  COUNT(*) FILTER (WHERE timestamp >= now() - INTERVAL 30 DAY) AS activity_30d,\n"
            "  COUNT(DISTINCT login) FILTER (WHERE timestamp >= now() - INTERVAL 30 DAY) AS active_users_30d,\n"
            "  MAX(timestamp) AS last_activity_at\n"
            f"FROM {activity_source}\n"  # nosec B608 (activity_source is fixed/validated)
            f"WHERE {where_in}\n"  # nosec B608 (where_in is built from plugin-owned allowlist)
            "  AND object_key IS NOT NULL\n"
            "GROUP BY 1,2,3,4;"
        )
    )

    return {"ok": True, "enabled": True, "created": True, "product_types": products}
