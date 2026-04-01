# Agent notes: pulse-dashboard webapp a

Scope: `webapps/pulse-dashboard/` and `resource/pulse-dashboard/`

## Intent

This webapp is packaged inside the plugin and is split into:

- **Frontend**: React build output under `resource/pulse-dashboard/build/`
- **Backend**: Dataiku webapp backend under `webapps/pulse-dashboard/backend.py`
- **Shared backend helpers**: `python-lib/pulse_dashboard/`

## Key conventions

- `webapps/pulse-dashboard/app.js` is a DSS-required stub file.
- `webapps/pulse-dashboard/body.html` is a loader HTML (not a symlink).
  - It loads `resource/pulse-dashboard/build/asset-manifest.json` from plugin resources and injects the hashed JS/CSS entrypoints.
- The React build is copied into the plugin under `resource/pulse-dashboard/build/`.
  - Use `scripts/sync_pulse_dashboard_build.sh <abs/path/to/build>` to refresh.

## Notes

- The React build currently uses relative asset paths (`./static/...`) which is compatible with DSS webapps.
- The backend exposes `/api/status` and a simple `/api/duckdb/query` endpoint (DuckDB file must exist / be initialized).
