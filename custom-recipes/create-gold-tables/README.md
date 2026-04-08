# Create GOLD tables (DuckDB)

This custom recipe is the start of the "GOLD builder" step in the data-collection pipeline.

It is intended to run on a schedule (nightly) inside a Dataiku-managed container, and to:

1. Create/reset a local DuckDB file
2. Configure DuckDB to access the same blob storage used by `partitioned_data`
3. Create views and materialize GOLD fact/dim/base tables (WIP)
4. Unload the resulting tables to the recipe output managed folder ("gold tables")
5. Clean up (close connection + delete DuckDB file)

## Current behavior

Implemented in `custom-recipes/create-gold-tables/recipe.py`:

- Resolves the source managed folder `partitioned_data` from the project defined by `PULSE_SOURCE_PROJECT_KEY` (default: `DATA_COLLECTION`).
- Creates a DuckDB connection using `data_collection.pulse_duckdb.prepare_duckdb()`:
  - Deletes the DuckDB file if it exists (reset safeguard)
  - Recreates the parent folder
  - Configures DuckDB blob access using `python-lib/data_collection/pulse_duckdb/config/blob/blob_credentials.yaml`
- Creates SILVER external views as needed and materializes curated GOLD tables.
  - Metadata tables are driven by YAML specs under `python-lib/data_collection/pulse_duckdb/gold_specs/`.
  - Development activity + object activity are built from curated audit `event_mapping` modules.
- Reads the recipe parameter `unload_behavior` (default: `duckdb`).
- Unloads curated DuckDB tables to the recipe output managed folder:
  - `base_*`, `dim_*`, `fact_*`
  - Default destination pattern: `gold/{table_name}.parquet`
  - Partitioned event tables (written as directories): `gold/{table_name}/instance_name=*/year=*/month=*/day=*/*.parquet`

Notes:
- Scenario ids/names and timestamps are currently extracted from `extras` JSON in SILVER.
- This recipe assumes the GOLD output managed folder and `partitioned_data` share the same underlying connection.

## Recipe parameters

- `unload_behavior`
  - `duckdb`: uses DuckDB `COPY ... TO '<blob-url>'` to write parquet directly to blob storage
  - `dataiku`: uses `SELECT * FROM <table>` -> pandas -> `Folder.upload_stream()`

## Local/debug runs

When running this file outside the DSS recipe harness, set:

- `PULSE_GOLD_DEBUG_LOOKUP=<managed-folder-name>` (example: `gold_data`)

In normal DSS runs, the output managed folder is resolved from the output role `gold_tables_folder`.

## Blob configuration

Blob access is configured via:

- `python-lib/data_collection/pulse_duckdb/engine/storage_config.py`
- `python-lib/data_collection/pulse_duckdb/config/blob/blob_credentials.yaml`

The code derives the managed folder backing store from Dataiku and sets DuckDB secrets accordingly.

## DuckDB path controls

DuckDB file location defaults to `/tmp/duckdb/pulse.duckdb`.

You can override it with:

- `PULSE_DUCKDB_PATH`: explicit file path to the DuckDB file
- `PULSE_DUCKDB_DIR`: directory for DuckDB files (filename stays `pulse.duckdb` unless project isolation is used)

## Notes

- DuckDB extension installation may require outbound network access on first run (e.g. `INSTALL httpfs`).
- This recipe assumes the GOLD output managed folder and `partitioned_data` share the same underlying connection.
