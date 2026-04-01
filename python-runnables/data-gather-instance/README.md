# Gather instance data macro

This runnable is intended to be packaged as a Dataiku plugin macro.

## What it does

- Discovers all no-arg `client.list_*` methods on the current DSS instance
- For each `list_*` method:
  - Uses `{category} = <method_name without the list_ prefix>`
  - Uses `{module} = metadata`
  - Writes RAW payload as `json.gz`
  - Normalizes to SILVER and writes parquet

## Outputs

## Downstream GOLD tables

The SILVER parquet emitted by this runnable is designed to be queried by DuckDB to build curated GOLD tables.

See:
- `custom-recipes/create-gold-tables/README.md`
- `python-lib/data_collection/pulse_duckdb/README.md`


Outputs are written to the Dataiku managed folder (default: `partitioned_data`) using Dataiku APIs.

- **RAW**: gzipped JSON payload dumps
  - Path pattern: `raw/category={category}/module=instance_metadata/instance_name={instance_name}/year={YYYY}/month={MM}/day={DD}/instance.json.gz`
  - Empty `list_*` results do not write any files
  - Errors are written to: `raw_errors/category={category}/module=instance_metadata/instance_name={instance_name}/year={YYYY}/month={MM}/day={DD}/instance.json`

- **SILVER**: parquet (snappy)
  - Path pattern: `silver/category={category}/module=instance_metadata/instance_name={instance_name}/year={YYYY}/month={MM}/day={DD}/instance.parquet`

## Configuration

The runnable uses plugin settings:
- `pulse_project_key`: project that contains the output managed folder
- `pulse_partitioned_data`: managed folder lookup (default: `partitioned_data`)
- `pulse_folder_connection`: connection used to create the folder if missing

## Exclusions

To skip specific `client.list_*` methods, add their method names to:
- `python-lib/data_collection/collection_exclusions/instance_data.yaml`

This is an exclusion list to keep capturing new DSS methods by default.

## Project inclusions (collected once)

Some `project_handle.list_*` methods return project-invariant results and do not need to be collected for every project.

At the end of this macro run:
- The runnable connects to `plugin_config["pulse_worker_key"]`
- Executes the curated list of `project_handle.list_*` methods from:
  - `python-lib/data_collection/collection_exclusions/instance_project_inclusion.yaml`

These are written once (using the same RAW/SILVER pattern) under the worker project key.
