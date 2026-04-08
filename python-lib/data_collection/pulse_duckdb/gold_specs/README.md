# GOLD specs: naming conventions

This directory (`python-lib/data_collection/pulse_duckdb/gold_specs/`) contains YAML configuration and SQL templates used by the nightly **GOLD builder** (DuckDB) recipe.

## Naming rules

These prefixes are a contract. They drive how we unload to the `gold_data` managed folder and how the webapp loads/queries parquet efficiently.

- `fact_*`
  - Large / partitioned datasets written as parquet *directories*.
  - Must be unloaded partitioned by at least `(instance_name, year, month, day)`.
  - Example output layout:
    - `gold/fact_object_activity_events/instance_name=*/year=*/month=*/day=*/*.parquet`

- `base_*`
  - “One-off” curated tables materialized directly from SILVER for convenience.
  - Typically unloaded as a single parquet file:
    - `gold/base_scenarios_project_metadata_history.parquet`

- `reg_*`
  - Registry/schema/config tables built from YAML definitions (not from SILVER rows).
  - Used to drive dynamic view construction (ex: product index registry).

- `dim_*`
  - One-off dimension tables that are computed (joins/transforms) and then persisted back to GOLD.
  - Typically small and unloaded as a single parquet file.

- `v_*`
  - DuckDB **views only**.
  - Never unloaded to the managed folder.

## Notes

- Partitioned/parquet-folder outputs should be reserved for `fact_*` tables.
- Tables that exist primarily to normalize configuration (YAML-driven) should be `reg_*`.
- If a downstream query only needs a view, prefer `v_*` over persisting another table.
