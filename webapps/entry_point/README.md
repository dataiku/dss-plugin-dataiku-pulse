# Pulse Entry Point (Flask + React)

This folder contains the editable full-stack Pulse application workspace used for local development of the dashboard experience.

## Purpose

- Provide the editable React frontend under `frontend/`
- Provide the local Flask/Gunicorn app used during workspace development
- Keep frontend build scripts, app bootstrap code, and supporting docs together in one in-repo location

## Background

The legacy Streamlit implementation lives in Code Studio resources at:
- `/home/dataiku/workspace/code_studio-resources/dataiku_pulse`

Pulse helps **Account Execs (AE)**, **Product Owners (PO)**, **Customer Success Managers (CSM)**, and **Dataiku Admins** understand how Dataiku is being used across one or more DSS instances.

Pulse has two main halves:
- **Data collection / curation**: build curated parquet GOLD tables from DSS metadata and audit logs
- **Presentation**: React UI + Flask API over curated tables

This `entry_point` workspace supports the presentation side during development.

## Current role in this repo

- `webapps/pulse-dashboard/` remains the plugin-packaged DSS backend/wrapper
- `resource/pulse-dashboard/build/` remains the plugin-served built frontend output
- `webapps/entry_point/` is the editable local development workspace for the frontend/app source

## What’s here

- `app.py`: local Flask application
- `pulse_plugin.py`: bootstrap that makes plugin Python modules importable
- `settings.py`: local app settings
- `frontend/`: editable React application source
- `scripts/`: local helper scripts such as `build_frontend.sh` and `start_app.sh`
- `docs/`: supporting workspace notes

## Quickstart

From the repo root:

```bash
bash webapps/entry_point/scripts/build_frontend.sh
bash scripts/webapp/sync_pulse_dashboard_build.sh /home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse/webapps/entry_point/frontend/build
bash scripts/webapp/run_backend.sh
```

To preview the built frontend locally:

```bash
cd /home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse/webapps/entry_point/frontend
npx serve -s build -l 3000
```

## Notes

- This workspace is intended for Dataiku-managed development environments.
- Some helper scripts still rely on absolute workspace paths and the shared plugin environment pointer file under `dataiku-pulse.extras`.
- Do not treat `frontend/build/` as source; rebuild it from `frontend/src/` when needed.
