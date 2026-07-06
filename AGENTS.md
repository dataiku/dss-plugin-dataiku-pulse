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
- `frontend/`: authoritative home for the React source (not yet populated — the legacy source lives in the external Code Studio workspace until the one-time copy in `frontend/README.md` is done)
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

### Install tooling (dev/test)

```bash
python -m pip install -r tests/requirements-dev.txt
```

(The prod code-env spec `code-env/python/spec/requirements.txt` intentionally
contains no dev tooling.)

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

The suite lives in `tests/` and runs **without a DSS runtime**: `tests/conftest.py`
puts `python-lib` on `sys.path` and installs a stub `dataiku` module from
`tests/stubs/`. Layout:

- `tests/contract/` — cross-checks the string contracts (flatten/casting YAML,
  gold specs, dashboard table registry) via `data_collection.contracts`
- `tests/unit/` — pure-Python unit tests (normalizer, cursors, notifications)
- `tests/integration/` — tiny in-memory DuckDB gold builds over tmp parquet

```bash
pip install -r tests/requirements-dev.txt
pytest                        # whole suite
pytest tests/contract         # contracts only (fast; run after YAML edits)
pytest tests/unit/test_cursor_clamp.py -k failure   # single file / pattern
```

When adding a collector, flatten YAML, casting entry or gold spec, run
`pytest tests/contract` — it fails on dangling names that would otherwise
degrade data silently.

### Run the dashboard backend (dev)

Entry point: `webapps/pulse-dashboard/backend.py`.
Outside DSS, imports may fail unless `python-lib/` is on `PYTHONPATH` and `dataiku` is available.

```bash
python -m flask --app webapps/pulse-dashboard/backend.py run --port 8995
```

### Sync the React build into the plugin

Do not hand-edit `resource/pulse-dashboard/build/`. That folder is generated packaged output served by the plugin.

The authoritative React source home is `frontend/` in this repo. Until the
one-time vendoring copy is done (see `frontend/README.md`), the legacy source
lives in the Code Studio workspace at
`/home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse.extras/webapps/entry_point/frontend/`.

Canonical workflow (once `frontend/` is populated):

```bash
bash scripts/build_frontend.sh
```

Important rules for agents:
- Make frontend source edits in `frontend/`, not in `resource/pulse-dashboard/build/`.
- Keep `webapps/pulse-dashboard/` in this plugin repo for backend/wrapper changes only.
- After every frontend source change, immediately run `scripts/build_frontend.sh` so `resource/pulse-dashboard/build/` is refreshed before handing work back.
- `frontend/node_modules/` and `frontend/build/` are gitignored; only source files are committed.

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

- `resource/pulse-dashboard/build/` is generated output; update via `bash scripts/build_frontend.sh` (requires `frontend/` to be populated).
- Treat minified files under `resource/` as non-source.
