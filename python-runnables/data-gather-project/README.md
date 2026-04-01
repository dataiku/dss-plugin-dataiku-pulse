# Gather project data macro

This runnable is intended to be packaged as a Dataiku plugin macro.

## What it does

- Lists all projects using `client.list_projects()`
- Applies a `projects_delta` cursor to only collect projects with recent changes
- For each selected project key, gets a `project_handle` and runs all no-arg `project_handle.list_*` methods
- Persists results to a managed folder (default: `partitioned_data`) in the target project

## Outputs

## Downstream GOLD tables

The SILVER parquet emitted by this runnable is designed to be queried by DuckDB to build curated GOLD tables.

See:
- `custom-recipes/create-gold-tables/README.md`
- `python-lib/data_collection/pulse_duckdb/README.md`


Outputs are written to the Dataiku managed folder (`partitioned_data`) using Dataiku APIs (not local filesystem writes).

- **RAW**: gzipped JSON payload dumps
  - Path pattern: `raw/category={category}/module=project_metadata/instance_name={instance_name}/year={YYYY}/month={MM}/day={DD}/{project_key}.json.gz`
  - Empty `list_*` results do not write any files
  - Errors are written to: `raw_errors/category={category}/module=project_metadata/instance_name={instance_name}/year={YYYY}/month={MM}/day={DD}/{project_key}.json`

- **SILVER**: parquet (snappy)
  - Path pattern: `silver/category={category}/module=project_metadata/instance_name={instance_name}/year={YYYY}/month={MM}/day={DD}/{project_key}.parquet`
  - SILVER normalization is defined in `python-lib/data_collection/data_normalizer/SILVER_FLOW.md`

## SILVER normalization summary

- Column name normalization (sanitize to underscores + lower-case)
- Flattening + schema enforcement (required columns via YAML, all other fields packed into `extras`)
- Type casting (datetime/numeric/boolean/upper-str via YAML)

YAML configuration lives under:
- `python-lib/data_collection/data_normalizer/schema_consistency/`

## Parallelism

Parallelism is controlled by plugin settings:
- `do_parallel` (boolean)
- `cores` (number of workers; defaults to local CPU count minus 1)
- `batch_size` (projects per joblib batch)

## Exclusions

To skip specific `project_handle.list_*` methods, add their method names to:
- `python-lib/data_collection/collection_exclusions/projects_data.yaml`

This is an exclusion list to keep capturing new DSS methods by default.

## Project delta (incremental collection)

The macro supports incremental collection of projects using a cursor stored in the *macro execution project* variables:

- Variable name: `local.projects_delta`
- If the variable does not exist, the macro uses plugin setting `pulse_default_projects_delta`
- If `pulse_projects_delta_debug` is true, the macro always uses `pulse_default_projects_delta` (ignores the variable)

Timestamp used for filtering:
- Primary: `versionTag.lastModifiedOn`
- Fallback: `creationTag.lastModifiedOn`
- Epoch unit detection is applied (s/ms/us/ns) based on value magnitude
- Very old/null/epoch-like timestamps are floored to `2015-01-01` to keep filtering deterministic

After a best-effort run, the macro updates `local.projects_delta` to the current UTC run timestamp.
