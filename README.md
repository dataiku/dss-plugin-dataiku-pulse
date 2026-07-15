# Dataiku Pulse

**Version:** 3.0.23

Dataiku Pulse is a cross-instance observability and analytics layer for **Dataiku DSS**. It helps teams understand **what is being built, what is being used, and how platform activity is evolving over time** across one or more DSS environments.

Pulse is designed to give a clear operational view of the platform: products, assets, users, usage patterns, metadata coverage, and administrative signals. It is meant to make large DSS estates easier to understand, govern, and improve.

## Documentation Quick Links

- Architecture: `docs/architecture.md`
- Taxonomy: `docs/taxonomy.md`
- Installation: `docs/install.md`

## Intent

Pulse exists to answer a few recurring platform questions:

- What products and assets exist across our DSS estate?
- What are people actually using?
- Which capabilities are driving the most platform activity?
- Which objects are foundational versus inactive or under-adopted?
- How do usage patterns vary by instance, project, product family, and user cohort?

Pulse is intentionally **read-only**. It collects metadata and audit-derived signals, normalizes them into a common analytical model, and serves them through the Pulse dashboard.

## Target Audience

Pulse is primarily built for:

- **Platform Admins** who need a centralized operational view of DSS
- **TAMs / CSMs / Field teams** who want to understand adoption and product usage
- **Solution Architects** who need visibility into platform structure and maturity
- **Platform owners and governance teams** who want a clearer picture of what exists and what is active

## What Pulse Provides

At a high level, Pulse combines:

- **Metadata collection** from DSS APIs
- **Activity and usage signals** from audit logs
- **Curated GOLD tables** built in DuckDB
- **A packaged DSS dashboard** for exploring products, assets, users, and operational metrics

This gives a 1000ft view of the platform while still allowing drilldown into specific products, assets, and activity patterns.

## How Pulse Works

Pulse follows a **hub-and-spoke** deployment model.

- The **hub** hosts the shared managed folder, curated GOLD layer, and the dashboard experience.
- One or more **workers** collect data from individual DSS instances and send normalized outputs back to the hub.

The pipeline is broadly:

1. **Collect** raw metadata and audit signals
2. **Normalize** them into consistent intermediate layers
3. **Build** curated analytical tables in DuckDB
4. **Serve** the resulting analytics through the Pulse dashboard webapp

## Core Concepts

Pulse organizes platform information around a few core ideas:

- **Products**: user-facing or application-consumed surfaces and foundational runtime capabilities
- **Assets**: internal build-time and project-scoped objects used to construct solutions
- **Activity**: observed usage and audit-derived interactions
- **Taxonomy**: a structured hierarchy used to group capabilities, categories, and tags consistently across the platform

This separation helps Pulse distinguish between:

- what teams **build with**
- what teams **deliver or consume**
- what is **active versus inactive**

## Main Components

### Collection Layer
- Shared collection logic: `python-lib/data_collection/`
- DSS runnables/macros: `python-runnables/data-gather-*`

### GOLD / DuckDB Layer
- GOLD recipe: `custom-recipes/create-gold-tables/recipe.py`
- DuckDB specs and helpers: `python-lib/data_collection/pulse_duckdb/`

### Dashboard Layer
- DSS webapp wrapper: `webapps/pulse-dashboard/`
- Shared dashboard/backend helpers: `python-lib/pulse_dashboard/`
- Packaged frontend build: `resource/pulse-dashboard/build/`

## Deployment Model

Pulse is designed for environments where a central project acts as the analytical hub and connected worker projects handle per-instance collection.

- **Hub**
  - stores shared managed-folder outputs
  - builds GOLD tables
  - serves the dashboard

- **Workers**
  - collect instance/project/audit data
  - maintain local cursor state for incremental loads
  - push normalized outputs back to the hub-managed storage pattern

## Local Development Notes

In this Code Studio workspace, the editable React frontend source lives outside this repo at:

- `/home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse.extras/webapps/entry_point/frontend/`

To rebuild and sync the dashboard build used by the plugin:

```bash
bash /home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse.extras/webapps/entry_point/scripts/build_frontend.sh
scripts/sync_pulse_dashboard_build.sh /home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse.extras/webapps/entry_point/frontend/build
```

## Documentation

The v3 documentation set lives under `docs/`:

- Architecture overview: `docs/architecture.md`
- Taxonomy guide: `docs/taxonomy.md`
- Installation guide: `docs/install.md`

A good reading order for new users is:

1. `docs/architecture.md`
2. `docs/taxonomy.md`
3. `docs/install.md`

Legacy infrastructure/process notes may also exist under the older extras documentation workspace.
