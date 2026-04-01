from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import duckdb

from .context import StorageContext


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ViewSpec:
    category: str
    module: str
    view_name: str


def default_view_name(*, category: str, module: str) -> str:
    return f"v_{category}__{module}".replace("-", "_")


def create_silver_view(
    *,
    conn: duckdb.DuckDBPyConnection,
    ctx: StorageContext,
    category: str,
    module: str,
    view_name: str | None = None,
) -> str:
    """Create a view over SILVER parquet for a given `{category,module}`."""

    if ctx.connection_type != "EC2":
        raise ValueError(f"Only EC2/S3 is implemented currently (got {ctx.connection_type})")

    if not ctx.bucket_or_container:
        raise ValueError("Missing bucket/container")

    view_name = view_name or default_view_name(category=category, module=module)
    base_path = f"s3://{ctx.bucket_or_container}/{ctx.folder_root.strip('/')}/silver"

    glob = f"{base_path}/category={category}/module={module}/instance_name=*/year=*/month=*/day=*/*.parquet"

    sql = f"""
    CREATE OR REPLACE VIEW {view_name} AS
    SELECT
      *,
      make_date(
        CAST(year AS INTEGER),
        CAST(month AS INTEGER),
        CAST(day AS INTEGER)
      ) AS partition_date
    FROM read_parquet(
      '{glob}',
      hive_partitioning = true
    );
    """.strip()

    logger.info("Creating view %s for %s/%s", view_name, category, module)
    conn.execute(sql)
    return view_name
