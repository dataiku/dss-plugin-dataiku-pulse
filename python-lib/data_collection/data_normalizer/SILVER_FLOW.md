# Silver normalization flow

This folder (`data_collection.data_normalizer`) defines the SILVER-layer normalization pipeline.

SILVER outputs are written as parquet under `partitioned_data/silver/...`.

## Order of operations (SILVER)

SILVER normalization MUST run in this order before writing parquet:

1) **Column name normalization**
- Replace special characters with `_`.
- Collapse repeated `_`.
- Force column names to lower-case.

2) **Flattening + schema enforcement**
- Load the required/flat columns list from:
  - `python-lib/data_collection/data_normalizer/schema_consistency/flatten_columns/{project|instance|audit}/{category}_{module}.yaml`
  - Modules include: `project_metadata`, `instance_metadata`, `audit_metadata`
- Ensure required columns exist (null-fill).
- Pack all non-required columns into `extras` (JSON string).
- Enforce column order: `instance_name`, required flat columns, `run_ts`, `extras`.

3) **Type casting (schema consistency)**
- Apply deterministic type casts using YAML lists in:
  - `python-lib/data_collection/data_normalizer/schema_consistency/casting_columns/`

Current casting configs:
- `datetime.yaml`
  - Cast known datetime-like columns.
  - Supports numeric epochs (ns/us/ms/s) and ISO-like datetime strings.
  - Enforces UTC and floors timestamps to seconds.
- `numeric.yaml`
  - Cast known numeric columns using `pd.to_numeric(..., errors="coerce")`.
- `boolean.yaml`
  - Cast known boolean columns (maps true/false-like values) to pandas nullable boolean.
- `upper_str.yaml`
  - Cast known categorical string columns to `string`, strip whitespace, then uppercase.

All remaining flat columns are treated as strings:
- `astype("string").str.strip()`

4) **X (future deterministic steps)**
- Reserved placeholder for future silver rules.

## YAML conventions

- YAML files are lists of column names.
- Column names should be normalized to match silver conventions:
  - lower-case
  - underscores instead of special characters

## Global required columns

- `instance_name` and `run_ts` are always present in SILVER.
- They are always treated as flat columns (not packed into `extras`).
