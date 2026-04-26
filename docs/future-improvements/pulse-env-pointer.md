# Pulse environment pointer (`pulse_env_path.txt`)

Pulse’s local/backend runner scripts need to run inside a Python environment that contains the required dependencies (eg. `flask`, `gunicorn`, `duckdb`, `pyarrow`, etc.).

To avoid hardcoding a particular code-environment name or installation path in every script, we use a single *environment pointer file* that stores the current environment location.

## The pointer file

- File: `project-lib-versioned/python/dataiku-pulse.extras/plugin_env_path.txt`
- Format: a single line containing an absolute path to the venv root directory.

Example contents:

```
/home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse.extras/plugin_dataiku-pulse_managed
```

This path is expected to contain:
- `bin/python`
- `bin/activate`

## How it is created/updated

- Bootstrap: external to this repo in this workspace setup.
  - Current location: `project-lib-versioned/python/dataiku-pulse.extras/plugin_dataiku-pulse_managed`
  - Pointer file: `project-lib-versioned/python/dataiku-pulse.extras/plugin_env_path.txt`

Run:

- `bash /home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse.extras/build_code_env.sh` (if available in your workspace)

## How runner scripts should use it

Runner scripts should read the pointer file and then either:

- Activate the env:
  - `source "$VENV_DIR/bin/activate"`

or (preferred for robustness):

- Call the environment’s Python directly:
  - `exec "$VENV_DIR/bin/python" -m gunicorn ...`

The “direct python” approach avoids relying on shell activation state and reduces subtle PATH issues.

## Failure modes and expected messaging

If the pointer file is missing or empty, runner scripts should:
- Print an actionable error
- Point to the pointer file location:
  - `/home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse.extras/plugin_env_path.txt`

## Related files

- Pointer file: `project-lib-versioned/python/dataiku-pulse.extras/plugin_env_path.txt`
- Venv: `project-lib-versioned/python/dataiku-pulse.extras/plugin_dataiku-pulse_managed`
- Backend runner (webapp): `project-lib-versioned/python/webapps/entry_point/scripts/start_app.sh`
- Backend runner (convenience): `/home/dataiku/run_backend.sh`
