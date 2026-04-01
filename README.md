# Dataiku Pulse

**Version:** 3.0.0

Pulse is an administrative dashboard for **Dataiku DSS** that provides centralized visibility into platform metadata and usage across one or more Dataiku instances.

It is designed for **Dataiku Platform Admins, TAMs, and Solution Architects** who need operational insight into how DSS is being used at scale.

---

## Overview

Pulse collects and presents:

- **Platform metadata** via the Dataiku API
- **Usage and activity metrics** via audit logs
- **Cross-instance insights** from one or more DSS environments

Pulse is a read-only analytics and observability layer: it does **not** modify customer data or platform state.

---

## How It Works (1000ft view)

1. **Collect & normalize (RAW/SILVER)**
   - Instance metadata, project metadata, and audit logs are collected into a partitioned managed folder (typically `partitioned_data/{raw|silver}/...`).
   - Entry points live under `python-runnables/`.

2. **Build curated tables (GOLD, DuckDB)**
   - A custom recipe builds curated GOLD tables from SILVER parquet using DuckDB.
   - Entry point: `custom-recipes/create-gold-tables/recipe.py`

3. **Consume in the dashboard webapp**
   - The Pulse dashboard reads from the curated tables and presents operational insights.
   - Frontend build assets live under `resource/pulse-dashboard/build/`.

---

## Components

- **Data collection framework** (macros + library)
  - Library code: `python-lib/data_collection/`
  - Macro runnables: `python-runnables/data-gather-*`

- **GOLD builder** (DuckDB + recipe)
  - Recipe: `custom-recipes/create-gold-tables/recipe.py`
  - DuckDB helpers: `python-lib/data_collection/pulse_duckdb/`

- **Pulse dashboard webapp** (React + backend helpers)
  - Webapp wrapper: `webapps/pulse-dashboard/`
  - Shared backend helpers: `python-lib/pulse_dashboard/`
  - Frontend build assets: `resource/pulse-dashboard/build/`

---

## Documentation

- Project metadata macro: `python-runnables/data-gather-project/README.md`
- Instance metadata macro: `python-runnables/data-gather-instance/README.md`
- Audit logs macro: `python-runnables/data-gather-audit-logs/README.md`
- GOLD recipe: `custom-recipes/create-gold-tables/README.md`

Installation / process docs:

- `docs/infrastructure/pulse_process_flow.md`
- `docs/infrastructure/installation_requirements.md`
- `docs/infrastructure/installation_process.md`
- `docs/infrastructure/pulse_usage_categories.md`

---

## Supported & Tested Versions

| Pulse Version | Dataiku DSS Version |
|--------------|---------------------|
| v2.7 | v14.3 |
| v2.6 | v14.3 |
| v2.5 | v14.3 |
| v2.1 | v14.2 |
| v1.x | v14.0 – v14.1 |

---

## Local Testing

Manual runnable wrappers (useful outside the DSS plugin runtime):

- `scripts/project_data.py`
- `scripts/instance_data.py`
- `scripts/audit_logs.py`

Frontend build sync helper:

- `scripts/sync_pulse_dashboard_build.sh`
