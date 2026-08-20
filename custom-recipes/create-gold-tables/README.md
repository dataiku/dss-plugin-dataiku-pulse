# Create GOLD tables (DuckDB)

This custom recipe is the start of the "GOLD builder" step in the data-collection pipeline.

Current implementation notes in this document reflect the `3.0.20` GOLD export/debugging update.

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
- User activity rollups are built from audit `users/user_activity` SILVER parquet (UI-only activity).
- Reads the recipe parameter `unload_behavior` (default: `duckdb`).
- Unloads curated DuckDB tables to the recipe output managed folder:
  - `base_*`, `dim_*`, `fact_*`
  - Default destination pattern: `gold/{table_name}.parquet`
  - Partitioned facts (written as directories): `gold/{table_name}/instance_name=*/year=*/month=*/day=*/*.parquet`

Naming rules are documented at `python-lib/data_collection/pulse_duckdb/gold_specs/README.md`.

Notes:
- Scenario ids/names and timestamps are currently extracted from `extras` JSON in SILVER.
- This recipe assumes the GOLD output managed folder and `partitioned_data` share the same underlying connection.
- Incremental state is stored in the GOLD managed folder at `gold/_state/manifest.json`.

## Recipe parameters

- `unload_behavior`
  - `duckdb`: uses DuckDB `COPY ... TO '<blob-url>'` to write parquet directly to blob storage
  - `dataiku`: uses `SELECT * FROM <table>` -> pandas -> `Folder.upload_stream()`
- `incremental_enabled`
  - default: `true`
  - enables manifest-backed incremental behavior
- `lookback_days`
  - default: `3`
  - reprocesses a recent safety window on each incremental run to catch late-arriving data

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

DuckDB files default under `/tmp/duckdb/` using a unique per-run filename such as
`pulse_<project>_<user>_<timestamp>_<token>.duckdb`.

You can override it with:

- `PULSE_DUCKDB_DIR`: directory for per-run DuckDB files

## Incremental state

This recipe creates a fresh local DuckDB file on every run, so runtime improvements
cannot rely on persisted local database state.

Instead, incremental progress is stored in the GOLD output managed folder at:

- `gold/_state/manifest.json`

The manifest stores per-table watermarks so later runs can:

- rescan only recent SILVER data for append-heavy event facts
- merge prior GOLD latest-state outputs with new SILVER rows for `base_*` latest tables
- keep a small safety lookback window for late-arriving data

### Incremental recipe controls

Use the recipe parameters in `custom-recipes/create-gold-tables/recipe.json` to control:

- whether incremental manifest-backed execution is enabled
- how many lookback days are rescanned

The recipe always builds and exports these required activity facts:

- `fact_dev_activity_events`
- `fact_object_activity_events`

### Important notes

- The first run after enabling incremental mode is still close to a full rebuild because no manifest exists yet.
- Later runs should be substantially faster when SILVER is mostly append-only.
- If older source partitions can be rewritten or corrected, increase `lookback_days`.

Notes:
- This recipe currently generates the DuckDB file path internally and does not read `PULSE_DUCKDB_PATH`.
- Old per-run DuckDB files in the target directory are cleaned up on a best-effort basis.

## Notes

- DuckDB extension installation may require outbound network access on first run (e.g. `INSTALL httpfs`).
- This recipe assumes the GOLD output managed folder and `partitioned_data` share the same underlying connection.
- `unload_behavior=dataiku` materializes tables through pandas before upload, so it is more memory-intensive than `unload_behavior=duckdb`.
