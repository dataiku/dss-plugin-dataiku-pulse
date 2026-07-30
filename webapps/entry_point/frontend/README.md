# Pulse Dashboard Frontend

This directory contains the editable React frontend source for Pulse.

## Purpose

- Source of truth for UI changes lives under `src/`.
- Production build output is generated into `build/`.
- Built assets are then synced into the plugin-served path under `resource/pulse-dashboard/build/` via the repo helper script.

## Local workflow

From the plugin repo root:

```bash
bash webapps/entry_point/scripts/build_frontend.sh
bash scripts/webapp/sync_pulse_dashboard_build.sh /home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse/webapps/entry_point/frontend/build
```

## Environment notes

- `PUBLIC_URL=.` is required so built assets work correctly behind DSS and Code Studio proxy paths.
- `REACT_APP_API_URL` is optional; when unset, the frontend uses same-origin `/api/...` calls.
- Dependencies are installed locally into `frontend/node_modules/` when `build_frontend.sh` runs and `react-scripts` is missing.
- npm cache defaults to `.local/npm-cache/` in this repo; override with `PERSISTENT_CACHE` if needed.

## Preview

To preview the built frontend locally:

```bash
cd /home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse/webapps/entry_point/frontend
npx serve -s build -l 3000
```
