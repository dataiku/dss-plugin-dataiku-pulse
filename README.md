# dataiku-pulse

This library collects Dataiku DSS metadata into a partitioned managed folder (`partitioned_data`) and provides a DuckDB-based path to build curated GOLD tables for downstream apps.

## Flow overview

1. **Collect & normalize (RAW/SILVER)**
   - Instance metadata, project metadata, and audit logs are collected into `partitioned_data/{raw|silver}/...`.
   - Entry points live under `python-runnables/`.

2. **Build GOLD tables (DuckDB)**
   - A custom recipe builds GOLD tables from the SILVER parquet using DuckDB.
   - Entry point: `custom-recipes/create-gold-tables/recipe.py`

3. **Consume GOLD tables (webapp / app layer)**
   - Downstream apps (ex: a Flask/React webapp) can read from the GOLD managed folder.

## Project layout

This plugin contains two main components:

- **Data collection framework** (macros + library)
  - Library code: `python-lib/data_collection/`
  - Macro runnables: `python-runnables/data-gather-*`

- **Pulse dashboard webapp** (React + backend helpers)
  - Frontend build assets: `resource/pulse-dashboard/build/`
  - Webapp wrapper: `webapps/pulse-dashboard/`
  - Shared backend helpers: `python-lib/pulse_dashboard/`

## Key docs

- Project metadata macro: `python-runnables/data-gather-project/README.md`
- Instance metadata macro: `python-runnables/data-gather-instance/README.md`
- Audit logs macro: `python-runnables/data-gather-audit-logs/README.md`

## Local testing

Manual runnable wrappers (useful outside the DSS plugin runtime):

- `unit_testing/project_data.py`
- `unit_testing/instance_data.py`
- `unit_testing/audit_logs.py`

Frontend build sync helper:

- `scripts/sync_pulse_dashboard_build.sh`

- GOLD recipe: `custom-recipes/create-gold-tables/README.md`
- Data collection DuckDB helpers: `python-lib/data_collection/pulse_duckdb/README.md`
- Dashboard backend helpers: `python-lib/pulse_dashboard/`
- Dashboard frontend build assets: `resource/pulse-dashboard/build/`
- Dashboard webapp wrapper: `webapps/pulse-dashboard/`
