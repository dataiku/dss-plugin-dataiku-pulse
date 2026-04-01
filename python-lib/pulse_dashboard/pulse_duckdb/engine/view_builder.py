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


def build_views_from_specs(conn: duckdb.DuckDBPyConnection) -> dict:
    """Execute all view SQL from `pulse_duckdb/datasets/views/*.yaml`.

    If an existing object has the same name but is a TABLE (eg. mistakenly loaded
    from CSV), we drop it before creating the view.
    """

    specs = _load_view_specs()

    view_statements: list[tuple[str, str]] = []
    for spec in specs:
        sql = str(spec.get("sql", "") or "").strip()
        name = str(spec.get("name"))
        path = str(spec.get("_path"))

        if not sql:
            raise ValueError(f"Missing `sql` in view spec: {path}")

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
        "errors": errors,
    }
