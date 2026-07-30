#!/usr/bin/env python
"""One-off migration: convert markdown dataset specs to YAML.

Creates one YAML file per markdown spec under:
- `pulse_duckdb/datasets/base/*.yaml`
- `pulse_duckdb/datasets/views/*.yaml`

This is intentionally not used by the app at runtime.
"""

from __future__ import annotations

import re
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1] / "pulse_duckdb" / "datasets"
BASE_SPECS_DIR = BASE_DIR / "base"
VIEW_SPECS_DIR = BASE_DIR / "views"

BASE_HDR = re.compile(r"^#\s*Base table spec:\s*`([A-Za-z0-9_]+)`", re.MULTILINE)
VIEW_HDR = re.compile(r"^#\s*View spec:\s*`([A-Za-z0-9_]+)`", re.MULTILINE)

TYPE_MAP = {
    "STRING": "VARCHAR",
    "VARCHAR": "VARCHAR",
    "BOOLEAN": "BOOLEAN",
    "BOOL": "BOOLEAN",
    "TIMESTAMP": "TIMESTAMP",
    "DATE": "DATE",
    "BIGINT": "BIGINT",
    "INTEGER": "INTEGER",
    "INT": "INTEGER",
}


def parse_description(md: str) -> str:
    lines = md.splitlines()
    out: list[str] = []
    started = False
    for line in lines[1:]:
        if line.strip().startswith("## "):
            break
        if not started and not line.strip():
            continue
        started = True
        if not line.strip():
            break
        out.append(line.strip())

    return " ".join(out).strip() or "TBD"


def parse_depends_on_from_inputs(md: str) -> list[str]:
    deps: list[str] = []
    in_inputs = False
    for line in md.splitlines():
        low = line.strip().lower()
        if low.startswith("## inputs") or low.startswith("## input"):
            in_inputs = True
            continue
        if in_inputs and line.strip().startswith("## "):
            break
        if in_inputs:
            m = re.search(r"`([A-Za-z0-9_]+)`", line)
            if m:
                deps.append(m.group(1))

    seen = set()
    out: list[str] = []
    for d in deps:
        if d not in seen:
            out.append(d)
            seen.add(d)
    return out


def parse_sql_block(md: str) -> str | None:
    m = re.search(r"```sql\s+(.*?)```", md, flags=re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else None


def parse_columns(md: str) -> list[tuple[str, str]]:
    lines = md.splitlines()
    cols: list[tuple[str, str]] = []

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().lower().startswith("| column") and "| type" in line.lower():
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if len(row) >= 2 and row[0] and row[0].lower() != "column":
                    col = re.sub(r"[^A-Za-z0-9_]", "_", row[0])
                    typ = row[1].strip().upper()
                    if "ARRAY" in typ or "JSON" in typ:
                        typ = "VARCHAR"
                    typ = TYPE_MAP.get(typ, "VARCHAR")
                    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", col):
                        cols.append((col, typ))
                i += 1
            continue
        i += 1

    seen = set()
    unique: list[tuple[str, str]] = []
    for c, t in cols:
        if c in seen:
            continue
        seen.add(c)
        unique.append((c, t))

    return unique


def schema_sql(table: str, cols: list[tuple[str, str]]) -> str:
    if not cols:
        cols = [("id", "INTEGER")]

    select_list = ",\n      ".join([f"CAST(NULL AS {t}) AS {c}" for c, t in cols])
    return (
        f'CREATE OR REPLACE TABLE "{table}" AS\n'
        f"SELECT\n      {select_list}\n"
        f"WHERE 1=0;"
    )


def example_insert(table: str, cols: list[tuple[str, str]]) -> str:
    if not cols:
        return f'INSERT INTO "{table}" (id) VALUES (1);'  # nosec B608

    col_names: list[str] = []
    values: list[str] = []
    for col, typ in cols[:12]:
        col_names.append(col)
        if col == "instance_name":
            values.append("'dss-prod'")
        elif col == "project_key":
            values.append("'FIN'")
        elif col in {"login", "owner_login", "last_modified_by_login"}:
            values.append("'alice'")
        elif typ in {"INTEGER", "BIGINT"}:
            values.append("1")
        elif typ == "BOOLEAN":
            values.append("true")
        elif typ == "DATE":
            values.append("DATE '2026-03-23'")
        elif typ == "TIMESTAMP":
            values.append("TIMESTAMP '2026-03-23 12:00:00'")
        else:
            values.append(f"'{col}_example'")

    return f'INSERT INTO "{table}" ({", ".join(col_names)}) VALUES ({", ".join(values)});'  # nosec B608


def write_yaml(
    *,
    out_path: Path,
    name: str,
    description: str,
    depends_on: list[str],
    build_order: int,
    sql: str,
    example_data: str,
) -> None:
    lines: list[str] = []
    lines.append(f"{name}:")
    lines.append("  description: >")
    for dline in (description.splitlines() or ["TBD"]):
        lines.append(f"    {dline.strip()}")

    if depends_on:
        lines.append("  depends_on:")
        for d in depends_on:
            lines.append(f"    - {d}")
    else:
        lines.append("  depends_on: []")

    lines.append(f"  build_order: {build_order}")

    lines.append("  sql: |")
    for sline in sql.splitlines():
        lines.append(f"    {sline.rstrip()}")

    lines.append("  example_data: |")
    for eline in example_data.splitlines():
        lines.append(f"    {eline.rstrip()}")

    lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    base_md = [p for p in sorted(BASE_SPECS_DIR.glob("*.md"))]
    view_md = [p for p in sorted(VIEW_SPECS_DIR.glob("*.md"))]

    base_specs: list[tuple[str, Path, str]] = []
    for p in base_md:
        text = p.read_text(encoding="utf-8")
        m = BASE_HDR.search(text)
        if not m:
            continue
        base_specs.append((m.group(1), p, text))

    # Deduplicate addendums
    seen = set()
    base_specs_unique: list[tuple[str, Path, str]] = []
    for name, p, text in base_specs:
        if name in seen:
            continue
        seen.add(name)
        base_specs_unique.append((name, p, text))

    for idx, (name, p, text) in enumerate(base_specs_unique, start=1):
        desc = parse_description(text)
        cols = parse_columns(text)
        sql = schema_sql(name, cols)
        ex = example_insert(name, cols)
        write_yaml(
            out_path=p.with_suffix(".yaml"),
            name=name,
            description=desc,
            depends_on=[],
            build_order=idx * 10,
            sql=sql,
            example_data=ex,
        )

    view_specs: list[tuple[str, Path, str]] = []
    for p in view_md:
        text = p.read_text(encoding="utf-8")
        m = VIEW_HDR.search(text)
        if not m:
            continue
        view_specs.append((m.group(1), p, text))

    for idx, (name, p, text) in enumerate(view_specs, start=1):
        desc = parse_description(text)
        deps = parse_depends_on_from_inputs(text)
        sql = parse_sql_block(text) or f'CREATE OR REPLACE VIEW "{name}" AS\nSELECT 1 AS dummy;'
        sql = re.sub(
            rf"CREATE\s+OR\s+REPLACE\s+VIEW\s+{re.escape(name)}\b",
            f'CREATE OR REPLACE VIEW "{name}"',
            sql,
            flags=re.IGNORECASE,
        )

        ex_lines = ["-- This object is a VIEW."]
        base_dep = next((d for d in deps if d.startswith("base_")), None)
        if base_dep:
            ex_lines.append("-- Minimal example: ensure upstream base table has at least one row.")
            ex_lines.append(f'INSERT INTO "{base_dep}" DEFAULT VALUES;')
        else:
            ex_lines.append("-- Insert example rows into its upstream base tables.")

        write_yaml(
            out_path=p.with_suffix(".yaml"),
            name=name,
            description=desc,
            depends_on=deps,
            build_order=1000 + idx * 10,
            sql=sql,
            example_data="\n".join(ex_lines),
        )


if __name__ == "__main__":
    main()
