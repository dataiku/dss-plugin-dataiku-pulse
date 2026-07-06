"""Canonical table schemas shared across builders.

`fact_dev_activity_events` was previously declared independently in the gold
recipe, the dashboard dataset YAML and the demo seeders — drifting copies of
the same contract. Declare column names/types once here and derive SQL.
"""

from __future__ import annotations

# Base event columns (the dashboard-side contract).
FACT_DEV_ACTIVITY_EVENTS_COLUMNS: list[tuple[str, str]] = [
    ("timestamp", "TIMESTAMP"),
    ("instance_name", "VARCHAR"),
    ("login", "VARCHAR"),
    ("msgtype", "VARCHAR"),
    ("msgtypebase", "VARCHAR"),
    ("dataiku_category", "VARCHAR"),
    ("project_key", "VARCHAR"),
    ("callpath", "VARCHAR"),
    ("extras", "VARCHAR"),
    ("run_timestamp", "TIMESTAMP"),
]

# Hive partition columns added by the gold recipe's partitioned unload.
FACT_DEV_ACTIVITY_EVENTS_PARTITION_COLUMNS: list[tuple[str, str]] = [
    ("year", "INTEGER"),
    ("month", "INTEGER"),
    ("day", "INTEGER"),
]


def fact_dev_activity_events_column_names(*, include_partitions: bool = False) -> list[str]:
    cols = [name for name, _ in FACT_DEV_ACTIVITY_EVENTS_COLUMNS]
    if include_partitions:
        cols += [name for name, _ in FACT_DEV_ACTIVITY_EVENTS_PARTITION_COLUMNS]
    return cols


def create_table_sql(
    *,
    table_name: str = "fact_dev_activity_events",
    include_partitions: bool = False,
) -> str:
    """Empty-schema CREATE OR REPLACE TABLE statement."""

    columns = list(FACT_DEV_ACTIVITY_EVENTS_COLUMNS)
    if include_partitions:
        columns += FACT_DEV_ACTIVITY_EVENTS_PARTITION_COLUMNS
    select_list = ",\n      ".join(
        f"CAST(NULL AS {col_type}) AS {name}" for name, col_type in columns
    )
    return (
        f'CREATE OR REPLACE TABLE "{table_name}" AS\n'
        f"    SELECT\n      {select_list}\n    WHERE 1=0;"
    )


def insert_sql(
    *,
    table_name: str = "fact_dev_activity_events",
    columns: list[str] | None = None,
) -> str:
    """Parameterized INSERT statement for the given (subset of) columns."""

    valid = set(fact_dev_activity_events_column_names(include_partitions=True))
    cols = columns if columns is not None else fact_dev_activity_events_column_names()
    unknown = [c for c in cols if c not in valid]
    if unknown:
        raise ValueError(f"Unknown fact_dev_activity_events columns: {unknown}")
    placeholders = ", ".join("?" for _ in cols)
    return f'INSERT INTO "{table_name}" ({", ".join(cols)}) VALUES ({placeholders});'
