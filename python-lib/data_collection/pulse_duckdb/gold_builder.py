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
    # Optional metadata for orchestration (ex: creating SILVER views)
    category: str | None = None
    module: str | None = None
    view_table_name: str | None = None
    base_table_name: str | None = None
    partition_keys: list[str] | None = None


def _templates_dir() -> Path:
    # Templates live alongside the legacy base specs.
    return Path(__file__).resolve().parent / "gold_specs" / "base"


def _load_sql_template(*, name: str) -> str:
    path = _templates_dir() / f"{name}.sql"
    if not path.exists():
        raise ValueError(f"Unknown gold spec template {name!r} (missing {path})")
    return path.read_text(encoding="utf-8")


def _render_template(*, template_name: str, payload: dict) -> str:
    if template_name == "latest_by_partition":
        partition_keys = payload.get("partition_keys")
        if not isinstance(partition_keys, list) or not partition_keys:
            raise ValueError("template=latest_by_partition requires non-empty partition_keys")

        base_table_name = payload.get("base_table_name")
        view_table_name = payload.get("view_table_name")
        if not base_table_name or not view_table_name:
            raise ValueError("template=latest_by_partition requires base_table_name and view_table_name")

        return _load_sql_template(name=template_name).format(
            base_table_name=base_table_name,
            view_table_name=view_table_name,
            partition_keys=", ".join(map(str, partition_keys)),
        )

    raise ValueError(f"Unknown gold spec template: {template_name!r}")


def load_gold_spec(path: Path) -> GoldSpec:
    data = yaml_loader.load_yaml(path)
    if not isinstance(data, dict) or not data:
        raise ValueError(f"Invalid gold spec YAML: {path}")

    name = next(iter(data.keys()))
    payload = data[name] or {}

    template = payload.get("template")
    if template:
        sql = _render_template(template_name=str(template), payload=payload)
    else:
        sql = str(payload.get("sql") or "")

    return GoldSpec(
        name=name,
        description=payload.get("description"),
        depends_on=list(payload.get("depends_on") or []),
        build_order=int(payload.get("build_order") or 0),
        sql=sql,
        category=(str(payload.get("category")) if payload.get("category") else None),
        module=(str(payload.get("module")) if payload.get("module") else None),
        view_table_name=(str(payload.get("view_table_name")) if payload.get("view_table_name") else None),
        base_table_name=(str(payload.get("base_table_name")) if payload.get("base_table_name") else None),
        partition_keys=(
            [str(x) for x in payload.get("partition_keys")]
            if isinstance(payload.get("partition_keys"), list)
            else None
        ),
    )


def apply_gold_spec(conn: duckdb.DuckDBPyConnection, spec: GoldSpec) -> None:
    if not spec.sql.strip():
        raise ValueError(f"Empty SQL for spec {spec.name}")
    logger.info("Building GOLD table %s", spec.name)
    conn.execute(spec.sql)
