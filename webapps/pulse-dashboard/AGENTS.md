# Agent notes: pulse-dashboard webapp

Scope: `webapps/pulse-dashboard/` and `resource/pulse-dashboard/`

## Intent

This webapp is packaged inside the plugin and is split into:

- **Frontend (served output)**: React build output under `resource/pulse-dashboard/build/`
- **Backend**: Dataiku webapp backend under `webapps/pulse-dashboard/backend.py`
- **Shared backend helpers**: `python-lib/pulse_dashboard/`

In this workspace, the editable frontend source does **not** live in this repo. It lives in `dataiku-pulse.extras` and must be built/synced into this plugin repo.

## Key conventions

- `webapps/pulse-dashboard/app.js` is a DSS-required stub file.
- `webapps/pulse-dashboard/body.html` is a loader HTML (not a symlink).
  - It loads `resource/pulse-dashboard/build/asset-manifest.json` from plugin resources and injects the hashed JS/CSS entrypoints.
- The React build is copied into the plugin under `resource/pulse-dashboard/build/`.
  - Use `scripts/webapp/sync_pulse_dashboard_build.sh <abs/path/to/build>` to refresh.

## Notes

- The React build currently uses relative asset paths (`./static/...`) which is compatible with DSS webapps.
- In DSS, frontend code must call the webapp backend via `dataiku.getWebAppBackendUrl(...)`.
  - Direct calls like `fetch('/api/status')` will hit the DSS server root and 404.
  - `webapps/pulse-dashboard/body.html` patches `fetch` and `XMLHttpRequest` to rewrite `/api/...` requests to the proxied backend URL.
- The backend exposes `/api/status` and several JSON APIs used by the packaged React build:
  - `/api/startup/flags` (feature flags from project variables)
  - `/api/startup/duckdb` (blocking DB init)
  - `/api/duckdb/query`
  - `/api/debug/duckdb/*` (reload + table introspection)
  - `/api/build/*` (catalog/products/dev-activity views)
  - `/api/build/assets/details` and `/api/build/products/details` (modal drilldown details)
  - `/api/build/users/*` (users activity + drilldowns)

## Frontend source (not in repo)

The plugin repository intentionally stores only the built frontend under `resource/pulse-dashboard/build/`. Do not edit that build output directly.

In this Code Studio workspace, the React source used to produce that build lives at:
- `/home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse/webapps/entry_point/frontend/`

Rebuild + sync flow:
- Build: `bash /home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse/webapps/entry_point/scripts/build_frontend.sh`
- Sync into plugin: `bash /home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse/scripts/webapp/sync_pulse_dashboard_build.sh /home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse/webapps/entry_point/frontend/build`

Practical rules:
- Frontend feature/UI work happens in `dataiku-pulse/webapps/entry_point/frontend/`.
- Backend/wrapper work happens in `dataiku-pulse/webapps/pulse-dashboard/`.
- The served packaged assets must end up in `dataiku-pulse/resource/pulse-dashboard/build/`.
- A duplicate build under `dataiku-pulse.extras/resource/pulse-dashboard/` is not the served source of truth and should not be relied on.
