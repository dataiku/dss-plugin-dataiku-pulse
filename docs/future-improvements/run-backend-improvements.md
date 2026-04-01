# Improvements for `run_backend.sh`

This document tracks improvements to the local backend runner script:
- `/home/dataiku/run_backend.sh`

Goal: make the script as reliable as `project-lib-versioned/python/webapps/entry_point/scripts/start_app.sh` when running inside Dataiku Code Studio, while keeping the convenience of a single command.

## Logging improvements

- **Avoid access logging to stdout**
  - Current: `--access-logfile -`
  - Improvement: default to a file under `/tmp/pulse/` (eg. `/tmp/pulse/gunicorn-access.log`).
  - Rationale: in some Code Studio contexts, writing access logs to stdout can block/hang when the output pipe buffer fills.

- **Write error logs to a file**
  - Current: relies on gunicorn defaults (stderr/stdout)
  - Improvement: default to `/tmp/pulse/gunicorn-error.log` via `--error-logfile`.

- **Add `--capture-output`**
  - Current: app stdout/stderr goes to the terminal output
  - Improvement: use `--capture-output` so application output is consistently captured into the gunicorn error log file.

- **Expose a configurable log directory**
  - Current: no `LOG_DIR` concept
  - Improvement: support `LOG_DIR=${LOG_DIR:-/tmp/pulse}` and create it.

- **Align log verbosity with dev usage**
  - Current: gunicorn default log level
  - Improvement: set a default `--log-level debug` (or `info`) and allow override via env var (eg. `LOG_LEVEL=${LOG_LEVEL:-debug}`).

## Runtime / developer-experience improvements

- **Enable auto-reload for development**
  - Current: no `--reload`
  - Improvement: support `RELOAD=${RELOAD:-1}` and enable `--reload` when set.

- **Use the same host/port handling as `start_app.sh`**
  - Current: supports `HOST`/`PORT`, but binds with inline args
  - Improvement: keep `HOST`/`PORT` and pass them as `--bind "$HOST:$PORT"`.

- **Keep the `TIMEOUT` override, but document it**
  - Current: `TIMEOUT=${TIMEOUT:-60}`
  - Improvement: keep this behavior and document defaults.

- **Improve error messages when the env pointer is missing**
  - Current: good message exists
  - Improvement: include a hint to the pointer file and the expected format (a single path to a venv root).

## Consistency improvements

- **De-duplicate logic with shared helper**
  - Current: env resolution logic exists in multiple scripts.
  - Improvement: create a small shared helper script (eg. `project-lib-versioned/python/future_items/pulse_env.sh`) that:
    - reads `project-lib-versioned/python/future_items/pulse_env_path.txt`
    - validates `bin/python` exists
    - optionally prints resolved path
  - Then `start_app.sh` and `run_backend.sh` can both `source` it.

- **Prefer `exec "$VENV_DIR/bin/python" -m gunicorn ...` over activating**
  - Current: activates the venv and calls `python -m gunicorn`
  - Improvement: consider skipping activation entirely in runner scripts (less shell state / fewer surprises), as long as `PYTHONPATH`/working dir is correct.

## Validation checklist

When updates are made, validate:
- `/api/status` responds `200`.
- `GET /` serves the React build.
- Logs appear under `/tmp/pulse/` and continue updating under moderate request volume.
- Script still works when run from any directory.
