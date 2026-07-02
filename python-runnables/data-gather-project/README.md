# Gather project data macro

This runnable is intended to be packaged as a Dataiku plugin macro.

## What it does

- Loads plugin settings from the single macro parameter set: `plugin_config["pulse_primary"]`
- Uses a local client (`dataiku.api_client()`) to:
  - list projects
  - read/update the `projects_delta` cursor in *worker project* variables
- Uses a remote client (`dataikuapi.DSSClient`) to upload results to the hub/dashboard project managed folder
- Applies a project-level `projects_delta` cursor to only collect projects with recent changes
- For each selected project key, runs all no-arg `project_handle.list_*` methods and persists results

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

The macro supports incremental collection of projects using a cursor stored in the *worker project* variables (the project where the macro runs):

- Worker project resolution: `client.get_default_project().project_key`
- Variable name: `local.projects_delta`
- If the variable does not exist, the macro uses plugin setting `pulse_default_projects_delta`
- If `pulse_default_projects_delta` is missing or invalid, the macro falls back to **3 calendar months before the current UTC time**
- If `pulse_projects_delta_debug` is true, the macro always uses `pulse_default_projects_delta` when valid; otherwise it uses the same 3-month fallback

Timestamp used for filtering:
- Primary: `versionTag.lastModifiedOn`
- Fallback: `creationTag.lastModifiedOn`
- Epoch unit detection is applied (s/ms/us/ns) based on value magnitude
- Very old/null/epoch-like timestamps are floored to `2015-01-01` to keep filtering deterministic

After a best-effort run, the macro updates `local.projects_delta` to the current UTC run timestamp.

## Row-level delta filtering (list_* payloads)

For projects that pass the project-level delta gate, the macro attempts to further reduce output size by applying a row-level delta filter per `list_*` payload:

- If the normalized RAW dataframe contains a column whose name includes `lastModifiedOn` or `createdOn`, rows are filtered to `>= projects_delta`.
- If filtering results in 0 rows, the macro skips writing outputs for that method.
- If no timestamp columns are detected, the macro writes the full payload for that method (better safe than sorry).

### Debug-only diagnostics

When `pulse_projects_delta_debug` is enabled, methods that lack any detectable timestamp columns are written locally for review:

- Directory: `/tmp/pulse_project_debug/`
- Filename pattern: `{PROJECT_KEY}__{method_name}__missing_timestamps.json`

These diagnostics are not written to the managed folder during normal macro runs.

## Centralized method rules

Project-level custom method behavior now lives under `python-lib/data_collection/` so it is discoverable alongside instance-level rules.

Files:
- `python-lib/data_collection/method_rules.py`
- `python-lib/data_collection/method_rules_hooks.py`
- `python-lib/data_collection/collection_exclusions/project_method_rules.yaml`

Behavior:
- Generic no-arg project methods continue to run through the shared collector.
- Methods needing custom args/cleanup can be added as centralized rules and hooks.
- Project row-level delta filtering remains supported through the shared engine.
- Methods currently excluded for unsupported handling remain explicitly disabled until a rule is added.
