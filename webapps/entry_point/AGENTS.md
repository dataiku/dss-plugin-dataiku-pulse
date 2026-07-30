# Agent instructions (`webapps/entry_point` scope)

This file applies to all files under `webapps/entry_point/`.

## Project intent

This folder is the editable local development workspace for the Pulse presentation layer.

Pulse exists to help **AE / PO / CSM / Admin** personas understand platform adoption and, especially, how higher-level **capabilities** (Data Engineering, GenAI, etc.) are being used across DSS instances, users, and groups.

Product model notes:
- Pulse has two halves:
  - **Data collection/curation** (build curated parquet “GOLD tables” from DSS metadata + audit logs)
  - **Presentation** (this app)
- This workspace focuses on the presentation layer, but the curated model (and therefore the DuckDB schema) may evolve.

Notes:
- Pulse “Infrastructure” pages are not part of the target experience here.
- `webapps/entry_point/` is now the in-repo editable workspace for frontend/app development.

## Python execution environment

All Python commands for this webapp should be run inside the workspace-managed Pulse plugin environment.

- Default venv location: `project-lib-versioned/python/dataiku-pulse.extras/plugin_dataiku-pulse_managed`
- Pointer file used by runner scripts: first `dataiku-pulse/.local/plugin_env_path.txt`, then fallback to `project-lib-versioned/python/dataiku-pulse.extras/plugin_env_path.txt`

Activate with:
- `source /home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse.extras/plugin_dataiku-pulse_managed/bin/activate`

This includes (but isn’t limited to): running `python`, `pytest`, `gunicorn`, `python -m py_compile`, and any ad-hoc import checks.

If you see editor/LSP errors like “Import 'flask' could not be resolved”, it usually means VS Code is not pointed at the correct interpreter. Set the Python interpreter to:
- `/home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse.extras/plugin_dataiku-pulse_managed/bin/python`

## Runbook

Typical local workflow:

1. Backend (API + serves React build)
   - Start: `bash /home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse/scripts/webapp/run_backend.sh`
   - Port: `8995`

2. Frontend build
   - After editing React source (`frontend/src/*`), rebuild: `bash /home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse/webapps/entry_point/scripts/build_frontend.sh`

3. Sync build into the plugin-served resource path
   - Run: `bash /home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse/scripts/webapp/sync_pulse_dashboard_build.sh /home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse/webapps/entry_point/frontend/build`

4. Frontend preview (static server)
   - Run: `cd /home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse/webapps/entry_point/frontend && npx serve -s build -l 3000`

## Notes

- Files synced into Code Studio can lose executable permissions; run scripts via `bash ...` instead of `./...`.
- Avoid using the system Python without activating the code env.
- Do not hand-edit `frontend/build/`; regenerate it from source.
