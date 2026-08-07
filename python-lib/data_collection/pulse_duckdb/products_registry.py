from __future__ import annotations

from pathlib import Path

import duckdb
import yaml


REGISTRY_COLUMNS = [
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


def build_base_dataiku_products_registry(
    conn: duckdb.DuckDBPyConnection,
    *,
    base_dir: Path,
) -> str:
    products_specs_dir = base_dir / "dataiku_products"

    conn.execute(
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

    if not products_specs_dir.exists():
        return "base_dataiku_products_registry"

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

        registry_rows.append(
            tuple(payload.get(column) if column != "spec_file" else path.name for column in REGISTRY_COLUMNS)
        )

    if registry_rows:
        placeholders = ", ".join(["?"] * len(REGISTRY_COLUMNS))
        conn.executemany(
            f"INSERT INTO base_dataiku_products_registry ({', '.join(REGISTRY_COLUMNS)}) VALUES ({placeholders});",
            registry_rows,
        )

    return "base_dataiku_products_registry"
