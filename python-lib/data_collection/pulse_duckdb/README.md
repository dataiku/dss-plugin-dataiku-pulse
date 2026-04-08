# pulse_duckdb

Utilities for building GOLD tables with DuckDB.

This package provides helpers to:

- Create/reset a local DuckDB database file
- Configure DuckDB secrets so it can read/write directly to the Dataiku managed-folder backing blob storage
- Provide small convenience wrappers used by custom recipes

## GOLD specs

YAML-driven build specs live under:

- `python-lib/data_collection/pulse_duckdb/gold_specs/project/` and `python-lib/data_collection/pulse_duckdb/gold_specs/instance/` (metadata base tables)
- `python-lib/data_collection/pulse_duckdb/gold_specs/dataiku_products/` (product index registry)
- `python-lib/data_collection/pulse_duckdb/gold_specs/dataiku_dev_tools/` (development activity: modules + category→capability)
- `python-lib/data_collection/pulse_duckdb/gold_specs/object_activity/` (object activity: curated modules)

The nightly GOLD recipe executes these specs and produces `base_*`, `dim_*`, and `fact_*` outputs.

Implemented first slice (Scenarios):

- `base_scenarios_metadata_history`
- `base_scenarios_metadata_latest`

## Key APIs

### `prepare_duckdb(ctx, reset=True, read_only=False, db_path=None)`

Defined in `python-lib/data_collection/pulse_duckdb/duckdb_manager.py`.

- If `reset=True`, deletes the DuckDB file if present and recreates the parent directory.
- Opens a DuckDB connection.
- Applies blob storage configuration using `engine/storage_config.py` + `config/blob/blob_credentials.yaml`.

Returns a `DuckDBSetupResult` containing:

- `conn`: open DuckDB connection
- `db_path`: resolved DuckDB file path
- `provider`: backing store type (ex: `EC2`, `Azure`, `GCS`)
- `credential_mode`: resolved auth mode (provider-specific)

### `query_df(ctx, query, reset=True, ...)`

Convenience helper that:

- Calls `prepare_duckdb(...)`
- Executes `conn.sql(query).df()`
- Always closes the connection

## Blob credentials template

DuckDB blob access is configured from:

- `python-lib/data_collection/pulse_duckdb/config/blob/blob_credentials.yaml`

The YAML contains provider-specific SQL templates to:

- `INSTALL`/`LOAD` required DuckDB extensions
- `CREATE OR REPLACE SECRET ...` with the credentials sourced from the Dataiku connection

## DuckDB file path controls

Default: `/tmp/duckdb/pulse.duckdb`

Environment overrides:

- `PULSE_DUCKDB_PATH`: explicit file path for the DB
- `PULSE_DUCKDB_DIR`: directory containing the DB file

If no explicit file path is provided, `db_path(project_key=...)` appends the project key to the default filename stem to avoid collisions.

## Notes

- DuckDB extension installation (`INSTALL httpfs`, `INSTALL azure`) may require outbound network access the first time it runs.
- Most recipes will keep `reset=True` to make nightly builds deterministic.
