# Agent notes: pulse-dashboard webapp

Scope: `webapps/pulse-dashboard/` and `resource/pulse-dashboard/`

## Intent

This webapp is packaged inside the plugin and is split into:

- **Frontend (served output)**: React build output under `resource/pulse-dashboard/build/`
- **Backend**: Dataiku webapp backend under `webapps/pulse-dashboard/backend.py`
- **Shared backend helpers**: `python-lib/pulse_dashboard/`

The authoritative frontend source home is `frontend/` at the repo root. Until the one-time vendoring copy is done (see `frontend/README.md`), the legacy source lives only in an external Code Studio workspace; `resource/pulse-dashboard/build/` is the packaged output either way.

## Key conventions

- `webapps/pulse-dashboard/app.js` is a DSS-required stub file.
- `webapps/pulse-dashboard/body.html` is a loader HTML (not a symlink).
  - It loads `resource/pulse-dashboard/build/asset-manifest.json` from plugin resources and injects the hashed JS/CSS entrypoints.
- The React build is copied into the plugin under `resource/pulse-dashboard/build/`.
  - Use `bash scripts/build_frontend.sh` to rebuild and sync it (requires `frontend/` to be populated).

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

## Frontend source

Do not edit `resource/pulse-dashboard/build/` directly — it is generated packaged output.

The authoritative source home is `frontend/` at the repo root. It is not yet populated: the React source must be copied in once from the legacy Code Studio workspace (`/home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse.extras/webapps/entry_point/frontend/`); `frontend/README.md` documents that one-time step.

Practical rules:
- Frontend feature/UI work happens in `frontend/` (or, until it is vendored, in the legacy workspace — then re-run the vendoring copy).
- After every frontend source change, run `bash scripts/build_frontend.sh` to rebuild and sync `resource/pulse-dashboard/build/`.
- Backend/wrapper work happens in `webapps/pulse-dashboard/`.
- Any build under `dataiku-pulse.extras/` is not the served source of truth and should not be relied on.
