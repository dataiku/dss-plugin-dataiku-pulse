# Gather instance data macro

This runnable is intended to be packaged as a Dataiku plugin macro.

## What it does

- Loads plugin settings from the single macro parameter set: `plugin_config["pulse_primary"]`
- Uses a local client (`dataiku.api_client()`) to discover all no-arg `client.list_*` methods on the current DSS instance
- Executes curated custom instance calls such as `client.get_licensing_status()` through the same raw/silver pipeline
- Uses a remote client (`dataikuapi.DSSClient`) to upload results to the hub/dashboard project managed folder
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

## Custom instance calls

The runnable also supports curated non-`list_*` instance methods.

Current custom calls:
- `get_licensing_status`: writes RAW licensing payload and splits SILVER outputs into:
  - `category=license/module=license_status`
  - `category=license/module=max_licenses`
  - `category=license/module=addon_licenses`

## Method-specific rules

Some DSS methods need special call arguments or small cleanup steps before persistence.

These are defined in:
- `python-lib/data_collection/collection_exclusions/instance_method_rules.yaml`
- `python-lib/data_collection/method_rules_hooks.py`

Behavior:
- Methods without a rule use the generic no-arg collection flow.
- Methods with declarative rules can add fixed kwargs and dataframe cleanup.
- Methods with hook-based rules can compute kwargs or apply custom payload/dataframe cleanup.
- If a method needs explicit handling and no valid rule is available, the result table reports `needs_rule`.

Example included:
- `list_connections`: hook-based cleanup removes secret-like dataframe columns before SILVER normalization.
- `list_global_api_keys`: explicitly disabled pending safe handling for sensitive fields.
- `list_imported_bundles`, `list_ml_tasks`, `list_llms`: explicitly registered so future per-method cleanup can be added without changing the runnable flow.
