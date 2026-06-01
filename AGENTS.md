# AGENTS.md

Instructions for agentic coding assistants working in this repository.

## Repo overview

Pulse is a **Dataiku DSS plugin**: Python libraries + DSS runnables/recipes + a packaged React dashboard.
Most runtime code executes **inside DSS** and relies on `dataiku` / `dataikuapi`.

**Key paths**
- `python-lib/`: shared Python libraries
  - `python-lib/data_collection/`: collection + normalization + DuckDB GOLD builder helpers
  - `python-lib/pulse_dashboard/`: dashboard DuckDB init/load/query helpers
  - `python-lib/pulse_init/`: initialization helpers for hub/worker bootstrap
- `python-runnables/`: plugin runnables (`data-gather-*`, `initialize-*`) used by macros
- `custom-recipes/`: plugin recipes (notably `create-gold-tables`)
- `webapps/pulse-dashboard/`: DSS webapp wrapper + Flask backend for the packaged plugin
- `resource/pulse-dashboard/build/`: committed **built** frontend assets served by the plugin
- `/home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse.extras/webapps/entry_point/frontend/`: editable React source used to produce the packaged frontend
- `code-env/python/spec/requirements.txt`: Dataiku code-env dependency spec

**Scoped agent notes (must obey when editing within scope)**
- `webapps/pulse-dashboard/AGENTS.md` applies to `webapps/pulse-dashboard/` and `resource/pulse-dashboard/`
- `custom-recipes/create-gold-tables/AGENTS.md` applies to `custom-recipes/create-gold-tables/`

## Cursor / Copilot rules

- No `.cursorrules`, `.cursor/rules/`, or `.github/copilot-instructions.md` found.

## Dev environment (Code Studio)

- Default Dataiku Python: `/opt/dataiku/pyenv` (container image)
- In this workspace: plugin env at `project-lib-versioned/python/dataiku-pulse.extras/plugin_dataiku-pulse_managed`
- When running Python commands, use an env that includes `dataiku`.

## Build / lint / format / test

This repo is not a standard Python package (no `pyproject.toml` / `setup.cfg`) and typically has no JS build step.
Run tools directly from the repo root.

### Install tooling (optional)

```bash
python -m pip install -r code-env/python/spec/requirements.txt
```

### Lint (Ruff)

```bash
ruff check .
ruff check . --fix
ruff check python-lib scripts python-runnables custom-recipes webapps
```

### Format (Black)

```bash
black .
black python-lib scripts python-runnables custom-recipes webapps
```

### Tests (Pytest)

Pytest is listed as a dev dependency, but the repo currently has **no conventional `tests/` suite**.
If/when tests are added:

```bash
pytest
```

Run a single test file:

```bash
pytest path/to/test_file.py
```

Run a single test (common patterns):

```bash
pytest path/to/test_file.py -k test_name_substring
pytest path/to/test_file.py::TestClass::test_method
```

### Run the dashboard backend (dev)

Entry point: `webapps/pulse-dashboard/backend.py`.
Outside DSS, imports may fail unless `python-lib/` is on `PYTHONPATH` and `dataiku` is available.

```bash
python -m flask --app webapps/pulse-dashboard/backend.py run --port 8995
```

### Sync the React build into the plugin

Do not hand-edit `resource/pulse-dashboard/build/`. That folder is generated packaged output served by the plugin.

In this Code Studio workspace, the authoritative editable React source lives outside the plugin repo at:
- `/home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse.extras/webapps/entry_point/frontend/`

Canonical workflow:

```bash
bash /home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse.extras/webapps/entry_point/scripts/build_frontend.sh
bash /home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse.extras/scripts/sync_pulse_dashboard_build.sh /home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse.extras/webapps/entry_point/frontend/build
```

Important rules for agents:
- Make frontend source edits in `dataiku-pulse.extras/webapps/entry_point/frontend/`, not in `resource/pulse-dashboard/build/`.
- Keep `webapps/pulse-dashboard/` in this plugin repo for backend/wrapper changes only.
- After frontend edits, rebuild and sync so `dataiku-pulse/resource/pulse-dashboard/build/` is refreshed before handing work back.
- The sync script in `dataiku-pulse.extras/scripts/` now targets the plugin repo (`dataiku-pulse`) by default.
- Ignore or remove stale duplicate packaged builds under `dataiku-pulse.extras/resource/`; they are not the served source of truth.

When editing the external Pulse dashboard frontend source at `/home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse.extras/webapps/entry_point/frontend/`, agents should automatically rebuild the frontend and sync the resulting build into `resource/pulse-dashboard/build/` before handing work back, unless the user explicitly asks not to.

## Code style guidelines

### Principles

- Prefer small, deterministic changes; avoid broad refactors.
- Assume DSS constraints: restricted egress, limited filesystem; `/tmp` usually available.
- Avoid writing into the plugin directory at runtime; write to managed folders via Dataiku APIs.

### Python version + typing

- Target Python **3.10+**.
- Prefer `from __future__ import annotations` in new/edited modules.
- Prefer `X | None` unions and built-in generics (`list[str]`, `dict[str, Any]`).
- Add type hints for new public functions and non-trivial locals.

### Imports

Keep imports grouped and sorted:
1. `from __future__ import annotations`
2. stdlib
3. third-party (`pandas`, `duckdb`, `flask`, `yaml`, ...)
4. Dataiku SDK (`import dataiku`, `from dataikuapi...`)
5. local packages (`from data_collection...`, `from pulse_dashboard...`)

Guidelines:
- Avoid unused imports.
- Lazy-import heavier Dataiku modules at boundaries when needed.

### Formatting

- Black-compatible formatting; don’t fight the formatter.
- Prefer readable multi-line code over clever one-liners.

### Naming

- Functions/vars: `snake_case`
- Classes: `PascalCase`
- Constants/env vars: `UPPER_SNAKE_CASE`
- Prefer explicit domain names (`instance_name`, `project_key`, `managed_folder_id`).

### Logging

- Use `logging.getLogger(__name__)` and `logger.info/debug/warning/error`.
- Use `logger.exception("...")` inside `except` blocks when keeping stack traces.

### Error handling

- Catch broad `Exception` only at integration boundaries:
  - Flask endpoints (`webapps/pulse-dashboard/backend.py`)
  - DSS runnables (`python-runnables/`)
  - DSS recipes (`custom-recipes/`)
  - best-effort cleanup utilities
- In library code, prefer specific exceptions (`ValueError`, `RuntimeError`) with actionable messages.
- Include operational context when relevant (project key, instance name, folder id/name).

### Paths, temp files, and safety

- Use `pathlib.Path`.
- Sanitize path segments used in filenames (remove `/`, collapse unsafe chars).
- Use `/tmp` only for transient artifacts; prefer best-effort cleanup.

### Pandas / normalization

- Avoid mutating inputs: start with `df.copy()`.
- Use `pd.to_datetime(..., utc=True, errors="coerce")` for cursor timestamps.

### DuckDB / SQL

- Keep SQL readable (multi-line strings + `.strip()` when needed).
- Avoid exposing arbitrary SQL execution to untrusted inputs in web endpoints; validate/whitelist.

## Repo safety notes

- `resource/pulse-dashboard/build/` is generated output; update via `scripts/sync_pulse_dashboard_build.sh`.
- Treat minified files under `resource/` as non-source.
