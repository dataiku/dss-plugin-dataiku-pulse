# Worker-side cursor storage

Pulse uses a hub/worker pattern:

- **Hub project** (dashboard): holds the `partitioned_data` managed folder, gold recipes, and dashboards.
- **Worker project** (per instance): runs the macros that collect instance data and uploads results to the hub.

Because of this, **delta/cursor variables must live in the worker project**, not the hub project.

## What this means in practice

When a macro runs on a worker instance:

- Read cursor variables from the **project that the macro is executed in** (the worker project).
- Update/write cursor variables back to that same worker project.
- Upload collected data to the hub project’s managed folder using the configured remote client.

## Implementation details

Cursor reads/writes use:

- The local client: `dataiku.api_client()`
- The worker project key resolved from:
  - `client.get_default_project().project_key`

This is implemented by:

- `python-lib/data_collection/helper/worker_project.py` (`resolve_worker_project_key`)
- `python-lib/data_collection/helper/cursors.py` (`resolve_cursor_ts`, `update_cursor_ts`)

Runnables that currently use worker-side cursor storage:

- `python-runnables/data-gather-audit-logs/runnable.py` (variable: `audit_log_delta`)
- `python-runnables/data-gather-project/runnable.py` (variable: `projects_delta`)

## Related cursor variable names

- `audit_log_delta` (timestamp ISO string)
- `projects_delta` (timestamp ISO string)

## Common gotcha

If you accidentally store cursors in the hub project, then:

- Workers will not have independent deltas.
- Runs can skip/duplicate data when multiple workers share the same cursor.

