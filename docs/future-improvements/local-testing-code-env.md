# Local testing notes (Code Studio)

This plugin includes helper scripts under `scripts/` that allow you to run the plugin runnables locally (outside the DSS plugin runtime), which is useful for iterating on data-collection logic.

## Python code environment

The plugin’s bundled code environment definition is under:
- `code-env/python/desc.json` (currently targets `PYTHON311`)
- `code-env/python/spec/requirements.txt`

In Code Studio, this workspace also supports a plugin-style, bootstrap-installed environment created by:
- `project-lib-versioned/python/dataiku-pulse.extras/plugin_dataiku-pulse_managed`

That environment location is written to:
- `project-lib-versioned/python/dataiku-pulse.extras/plugin_env_path.txt`

The local testing scripts (`scripts/audit_logs.py`, `scripts/instance_data.py`, `scripts/project_data.py`) will automatically re-exec themselves using the Python interpreter from that path when the file exists.

## Running the scripts

From the plugin directory:

- `python scripts/audit_logs.py`
- `python scripts/instance_data.py`
- `python scripts/project_data.py`

If you want to override the pointer file location, set:
- `PULSE_ENV_PATH_FILE=/path/to/pulse_env_path.txt`

## Common gotchas

- Ensure the pointer file exists and is populated:
  - `/home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse.extras/plugin_env_path.txt`
- If remote hub/spoke settings are configured in `runnable_inputs/plugin_config.json`, the local scripts may disable those to avoid authentication errors.
