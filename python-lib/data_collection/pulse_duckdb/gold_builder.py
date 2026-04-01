from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import duckdb

from .helpers import yaml_loader


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GoldSpec:
    name: str
    description: str | None
    depends_on: list[str]
    build_order: int
    sql: str


def load_gold_spec(path: Path) -> GoldSpec:
    data = yaml_loader.load_yaml(path)
    if not isinstance(data, dict) or not data:
        raise ValueError(f"Invalid gold spec YAML: {path}")

    name = next(iter(data.keys()))
    payload = data[name] or {}

    return GoldSpec(
        name=name,
        description=payload.get("description"),
        depends_on=list(payload.get("depends_on") or []),
        build_order=int(payload.get("build_order") or 0),
        sql=str(payload.get("sql") or ""),
    )


def apply_gold_spec(conn: duckdb.DuckDBPyConnection, spec: GoldSpec) -> None:
    if not spec.sql.strip():
        raise ValueError(f"Empty SQL for spec {spec.name}")
    logger.info("Building GOLD table %s", spec.name)
    conn.execute(spec.sql)
