from __future__ import annotations


def group_gold_tables_by_prefix(table_names: list[str]) -> dict[str, list[str]]:
    return {
        "base_tables": [name for name in table_names if name.startswith("base_")],
        "dim_tables": [name for name in table_names if name.startswith("dim_")],
        "agg_tables": [name for name in table_names if name.startswith("agg_")],
        "fact_tables": [name for name in table_names if name.startswith("fact_")],
    }
