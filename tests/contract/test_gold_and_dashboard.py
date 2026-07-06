from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLD_SPECS_DIR = REPO_ROOT / "python-lib" / "data_collection" / "pulse_duckdb" / "gold_specs"
DASHBOARD_DATASETS = REPO_ROOT / "python-lib" / "pulse_dashboard" / "pulse_duckdb" / "datasets"


def _spec_paths() -> list[Path]:
    return sorted(
        list((GOLD_SPECS_DIR / "project").glob("base_*.yaml"))
        + list((GOLD_SPECS_DIR / "instance").glob("base_*.yaml"))
    )


def test_all_gold_specs_dry_render():
    """Every gold spec must load and render to non-empty SQL (incl. wide_columns)."""

    from data_collection.pulse_duckdb.gold_builder import load_gold_spec

    assert _spec_paths(), "no gold specs found"
    for path in _spec_paths():
        sql_params = None
        if path.name == "base_license_limits_wide_latest.yaml":
            sql_params = {"wide_columns": ", 1 AS placeholder"}
        spec = load_gold_spec(path, sql_params=sql_params)
        assert spec.sql.strip(), f"{path.name}: empty SQL"
        assert "{wide_columns}" not in spec.sql, f"{path.name}: unrendered placeholder"


def test_wide_license_spec_keeps_placeholder_on_disk():
    """P0.5 regression: the spec file on disk must keep its pristine placeholder."""

    path = GOLD_SPECS_DIR / "instance" / "base_license_limits_wide_latest.yaml"
    assert "{wide_columns}" in path.read_text(encoding="utf-8")


def test_dashboard_registry_resolves():
    from data_collection.contracts import validate_dashboard_tables

    errors = [i for i in validate_dashboard_tables() if i.severity == "error"]
    assert not errors, [i.message for i in errors]


def test_fact_dev_activity_schema_three_way():
    """The fact_dev_activity_events contract must match everywhere it is declared."""

    from shared_duckdb.schemas import (
        FACT_DEV_ACTIVITY_EVENTS_COLUMNS,
        fact_dev_activity_events_column_names,
    )

    base_names = [name for name, _ in FACT_DEV_ACTIVITY_EVENTS_COLUMNS]

    # 1) dashboard dataset spec declares exactly the base columns.
    dataset_yaml = yaml.safe_load(
        (DASHBOARD_DATASETS / "base" / "fact_dev_activity_events.yaml").read_text(encoding="utf-8")
    )
    sql = dataset_yaml["fact_dev_activity_events"]["sql"]
    yaml_cols = re.findall(r"\)\s+AS\s+(\w+)", sql)
    assert yaml_cols == base_names, (
        f"dashboard dataset spec columns {yaml_cols} != shared schema {base_names}"
    )

    # 2) the gold recipe's fact builder emits the base columns + partitions.
    recipe_text = (
        REPO_ROOT / "custom-recipes" / "create-gold-tables" / "recipe.py"
    ).read_text(encoding="utf-8")
    builder = recipe_text.split("def _build_fact_dev_activity_events", 1)[1]
    branch_sql = builder.split('f"""', 1)[1].split('"""', 1)[0]
    branch_aliases = re.findall(r"(?:AS\s+(\w+)|^\s*(\w+),?\s*$)", branch_sql, flags=re.M)
    flat = [a or b for a, b in branch_aliases if (a or b) not in {"SELECT", "FROM"}]
    for name in fact_dev_activity_events_column_names(include_partitions=True):
        assert name in flat, f"recipe fact builder missing column {name!r}: {flat}"

    # 3) the partitioned unload derives its column list from the shared schema
    #    (import-based, so just assert the recipe references the helper).
    assert "fact_dev_activity_events_column_names" in recipe_text
