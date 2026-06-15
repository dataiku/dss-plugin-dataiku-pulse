# Gather audit logs macro

This runnable is intended to be packaged as a Dataiku plugin macro.

## What it does

- Loads plugin settings from the single macro parameter set: `plugin_config["pulse_primary"]`
- Locates DSS audit logs (`DATA_DIR/run/audit/audit.log*`) using `client.get_instance_info()` without changing process working directory
  - If `PULSE_AUDIT_LOGS_USE_CACHED` is truthy, uses the static test folder `python/audit_data/`
- Uses a delta cursor `local.audit_log_delta` stored in the *worker project* variables (the project where the macro runs)
- Uses that delta first to select the smallest likely set of `audit.log*` files by Linux `mtime` (newest-first, with one older boundary file when needed), then applies the row-level timestamp filter inside those files
- Builds one shared prepared audit DataFrame per chunk for downstream processors by filtering to `topic == "generic"` and flattening `message` once
- Expands the audit `message` JSON into `message_*` columns
- Runs a configurable list of processors from:
  - `python-lib/data_collection/audit_logs_modules/modules.yaml`
- Applies lightweight processor-specific prefilters after the shared generic-only chunk preparation, to avoid unnecessary work on obviously irrelevant audit rows
- Stages `event_mapping` chunk outputs locally under `/tmp` and uploads coalesced final parquet files per `module/day`, to avoid excessive tiny managed-folder writes

Currently supported processor:
- `event_mapping` (maps `message_msgType` to `dataiku_category` using `mapping.csv`)

## Outputs

## Downstream GOLD tables

Processor SILVER outputs can be queried by DuckDB to build curated GOLD tables.

See:
- `custom-recipes/create-gold-tables/README.md`
- `python-lib/data_collection/pulse_duckdb/README.md`


Outputs are written to the Dataiku managed folder (default: `partitioned_data`) using Dataiku APIs.

### Optional RAW backup

If `pulse_backup_audit_logs` is true, the macro writes a raw backup (before any enrichment):
- `raw/category=audit_logs/module=backup/instance_name={instance_name}/year={YYYY}/month={MM}/day={DD}/audit_logs-{epoch_ms}.json.gz` (hive-style partitions)

This backup contains only the delta window (rows with `timestamp >= local.audit_log_delta`).

### Processor outputs (SILVER)

For each processor, outputs are written grouped by `dataiku_category`:
- `silver/category={processor_name}/module={dataiku_category}/instance_name={instance_name}/year={YYYY}/month={MM}/day={DD}/audit_logs-{epoch_ms}.parquet` (hive-style partitions)

Note: processor outputs are SILVER-only by design.

## Configuration

Plugin settings used (under `pulse_primary`):
- `pulse_project_key`: hub project that contains the output managed folder
- `pulse_partitioned_data`: managed folder lookup (default: `partitioned_data`)
- `pulse_folder_connection`: connection used to create the folder if missing
- `pulse_project_url` / `pulse_project_api`: remote target used to upload to the hub (can be the same DSS)
- `pulse_audit_logs_debug`: use static logs for development (default false)
- `pulse_backup_audit_logs`: write raw audit backup (default false)
- `pulse_audit_logs_delta`: default delta cursor if variable missing

## Delta cursor

- Worker project resolution: `client.get_default_project().project_key`
- Cursor variable: `local.audit_log_delta`
- Updated only to the max `timestamp` from chunks that completed successfully across all configured processors.

Additional behavior:
- Candidate audit files are selected newest-first from `audit.log*` using the cursor as an `mtime` boundary, with a safety fallback to include the newest file and at most one older boundary file.
- Older selected files may stop early once a parsed chunk is fully before the cursor, to avoid scanning an entire boundary file when only its newest tail could contain new rows.
- RAW backups now use hive-style partitions (`instance_name=.../year=.../month=.../day=...`).
- SILVER and RAW partitions now use event time (max timestamp in the written group/chunk), not macro run time.
- Result output includes summary diagnostics for dropped rows, scanned files/chunks, early-stopped files/chunks, DQ failures, and cursor movement.
- Upload failures to the managed folder are handled per write so a single S3/object-store reset does not abort the whole job; failed chunks do not advance the cursor.
