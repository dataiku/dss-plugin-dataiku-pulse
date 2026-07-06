"""End-to-end gold build over tiny silver parquet with in-memory DuckDB.

Exercises the real spec templates (`latest_by_partition`) through
`load_gold_spec` + `apply_gold_spec`, with views created over local parquet the
same way `create_silver_view` does for blob storage.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLD_SPECS_DIR = REPO_ROOT / "python-lib" / "data_collection" / "pulse_duckdb" / "gold_specs"

# (spec file, category, module, key column, sample rows)
CASES = [
    (
        "project/base_scenarios_project_metadata_history.yaml",
        "scenarios",
        "project_metadata",
        "scenarios_id",
    ),
    (
        "project/base_datasets_project_metadata_history.yaml",
        "datasets",
        "project_metadata",
        "datasets_name",
    ),
    (
        "instance/base_users_instance_metadata_history.yaml",
        "users",
        "instance_metadata",
        "users_login",
    ),
]


def _required_columns(category: str, module: str) -> list[str]:
    from data_collection.contracts import load_flatten_registry

    registry = load_flatten_registry()
    for scope_map in registry.values():
        if (category, module) in scope_map:
            return scope_map[(category, module)]
    raise AssertionError(f"no flatten config for {category}/{module}")


def _write_silver(base_dir: Path, *, category: str, module: str, key_col: str) -> None:
    required = _required_columns(category, module)
    partition = (
        base_dir
        / f"category={category}"
        / f"module={module}"
        / "instance_name=i1"
        / "year=2026"
        / "month=07"
        / "day=01"
    )
    partition.mkdir(parents=True)

    def _row(key: str, run_ts: str, marker: str) -> dict:
        row = {c: marker for c in required}
        row["instance_name"] = "i1"
        row["run_ts"] = pd.Timestamp(run_ts, tz="UTC")
        row[key_col] = key
        if "project_key" in required:
            row["project_key"] = "PROJ"
        return row

    # Two observations of the same key (old + new) and one other key: the
    # latest_by_partition template must keep exactly the newest per key.
    df = pd.DataFrame(
        [
            _row("k1", "2026-07-01T00:00:00", "old"),
            _row("k1", "2026-07-01T06:00:00", "new"),
            _row("k2", "2026-07-01T03:00:00", "only"),
        ]
    )
    df.to_parquet(partition / "part.parquet", index=False)


@pytest.mark.parametrize("spec_rel,category,module,key_col", CASES)
def test_gold_spec_builds_latest_per_key(tmp_path, spec_rel, category, module, key_col):
    from data_collection.pulse_duckdb.gold_builder import apply_gold_spec, load_gold_spec

    _write_silver(tmp_path, category=category, module=module, key_col=key_col)

    spec = load_gold_spec(GOLD_SPECS_DIR / spec_rel)
    assert spec.category == category and spec.module == module

    conn = duckdb.connect()
    view_name = spec.view_table_name or f"v_{category}__{module}"
    glob = (
        f"{tmp_path}/category={category}/module={module}/"
        "instance_name=*/year=*/month=*/day=*/*.parquet"
    )
    conn.execute(
        f"""
        CREATE OR REPLACE VIEW {view_name} AS
        SELECT *, make_date(CAST(year AS INTEGER), CAST(month AS INTEGER), CAST(day AS INTEGER)) AS partition_date
        FROM read_parquet('{glob}', hive_partitioning = true, union_by_name = true);
        """
    )

    apply_gold_spec(conn, spec)

    rows = conn.execute(
        f"SELECT {key_col}, run_ts FROM {spec.base_table_name} ORDER BY {key_col}"
    ).fetchall()
    assert len(rows) == 2, f"expected latest-per-key dedup, got {rows}"
    by_key = {r[0]: r[1] for r in rows}
    kept = pd.Timestamp(by_key["k1"])
    if kept.tzinfo is not None:
        kept = kept.tz_convert("UTC")
    assert kept.hour == 6, f"did not keep the newest observation: {kept}"
