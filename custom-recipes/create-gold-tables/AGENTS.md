# Agent notes: create-gold-tables recipe

Scope: `custom-recipes/create-gold-tables/`

## Intent

This recipe is the "GOLD builder" stage of the data-collection framework.

Target nightly flow:

- Start container
- Reset DuckDB
- Configure blob access (DuckDB secrets)
- Create external views on SILVER parquet
- Materialize GOLD fact/dim/base tables
- Unload GOLD tables to the recipe output managed folder
- Close DuckDB + delete DB file

## Key entrypoints

- Uses shared folder creation helper `data_collection.helper.ensure_managed_folder()` for `partitioned_data` and the GOLD output folder.

- `custom-recipes/create-gold-tables/recipe.py`
  - Reads recipe config: `unload_behavior`
  - Uses `data_collection.pulse_duckdb.prepare_duckdb()`

- `python-lib/data_collection/pulse_duckdb/duckdb_manager.py`
  - `prepare_duckdb(ctx, reset=True, ...)` resets file + configures storage

## Conventions

- Unload candidates: DuckDB tables starting with `base_`.
- Output path: `gold/{table_name}.parquet` under the output folder.

## Local/debug runs

Outside the DSS recipe harness, set:

- `PULSE_GOLD_DEBUG_LOOKUP=gold_data`

This bypasses `get_output_names_for_role('gold_tables_folder')`.

## Dataiku specifics

- Output role `gold_tables_folder` resolves to a managed folder identifier.
- Managed folder lookups can be folder *name* or *id*; `build_storage_context()` supports both.
