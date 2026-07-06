# Pulse dashboard frontend (React source)

This directory is the in-repo home for the dashboard's React source. The
committed build served by the plugin lives in `resource/pulse-dashboard/build/`
and is produced from here by `scripts/build_frontend.sh`.

## ⚠ One-time vendoring step (requires Code Studio access)

The React source historically lived only in one Code Studio workspace:

```
/home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse.extras/webapps/entry_point/frontend/
```

That path is not reachable from this checkout, so the source could not be
vendored automatically. Someone with access to that workspace must copy it in
once:

```bash
# from the Code Studio workspace
rsync -a --exclude node_modules --exclude build \
  /home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse.extras/webapps/entry_point/frontend/ \
  <this-repo>/frontend/
```

After that, `scripts/build_frontend.sh` reproduces the packaged build and the
external workspace is no longer load-bearing.

`frontend/node_modules/` and `frontend/build/` are gitignored — only source
files (src/, public/, package.json, package-lock.json, configs) are committed.
