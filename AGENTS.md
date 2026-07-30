# AGENTS.md

Instructions for AI coding agents working in this repository.

> Primary objective: Make the **smallest correct change** that fixes the verified root cause without introducing regressions.

---

# AI Operating Contract

Your priorities are:

1. Correctness
2. Determinism
3. Backward compatibility
4. Simplicity
5. Performance

Never optimize for cleverness.

When multiple implementations are possible, choose the one that is:

- Easier to understand
- Easier to debug
- Easier to maintain
- Less invasive
- More consistent with the existing repository

---

# Required Workflow

For every task:

1. Read the request completely.
2. Investigate before editing.
3. Reproduce the issue whenever possible.
4. Identify the root cause.
5. Explain the proposed fix.
6. Make one logical change.
7. Verify it.
8. Stop if verification fails.
9. Review the git diff.
10. Report results honestly.

Never stack speculative fixes.
Never claim success without verification.



Before editing any files:

- Summarize your understanding of the problem.
- List the files you believe are involved.
- Explain why those files need modification.
- Describe the smallest expected solution.

Do not begin editing until you have a coherent plan.

If multiple valid approaches exist, explain them and choose the least invasive.

---

# Do Not Guess

If uncertain:

- Stop.
- Explain what is uncertain.
- Present possible causes.
- Ask for clarification if required.

Never invent:

- APIs
- helper functions
- configuration
- environment variables
- Dataiku capabilities
- SQL schemas
- file locations

Search first.

---

# Repository & Environment Overview

Pulse is a **Dataiku DSS plugin** executing inside DSS and relying on `dataiku` / `dataikuapi`. 

### Environment Context
- Default Dataiku Python: /opt/dataiku/pyenv (container image)
- Plugin Workspace Env: project-lib-versioned/python/dataiku-pulse.extras/plugin_dataiku-pulse_managed
- Preferred local env pointer: `dataiku-pulse/.local/plugin_env_path.txt` (gitignored, one absolute venv path per developer)
- Always use an environment that includes dataiku when running commands.

### Primary Folders & Target Paths
- python-lib/ - Shared Python libraries
  - python-lib/data_collection/ - Collection, normalization, and DuckDB GOLD builder helpers
  - python-lib/pulse_dashboard/ - Dashboard DuckDB init, load, and query helpers
  - python-lib/pulse_init/ - Initialization helpers for hub/worker bootstrap
- python-runnables/ - Plugin runnables (data-gather-*, initialize-*) used by macros
- custom-recipes/ - Plugin recipes (notably create-gold-tables)
- webapps/pulse-dashboard/ - DSS webapp wrapper + Flask backend
- resource/pulse-dashboard/build/ - Committed built frontend assets served by the plugin. Never edit directly.
- /home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse/webapps/entry_point/frontend/ - Editable React source used to produce the frontend.

### Localized Scoped Rules
- webapps/pulse-dashboard/AGENTS.md applies to webapps/pulse-dashboard/ and resource/pulse-dashboard/
- custom-recipes/create-gold-tables/AGENTS.md applies to custom-recipes/create-gold-tables/

---

# Architecture & Data Flow

Data flow follows a strict directional path:

RAW -> SILVER -> GOLD -> DuckDB -> Dashboard

Fix problems where they originate. Never compensate downstream for upstream bugs.

For GOLD-table and web-application issues, do not assume a patch is needed first. Confirm that the SILVER source data and metadata are correct, then patch GOLD logic if needed, and only then patch web application views or other downstream tables if the issue still remains.

### Dataiku Safety Rules
- Assume DSS constraints: restricted egress, limited filesystem; /tmp is available only for transient artifacts.
- Avoid writing into the plugin directory at runtime; write to managed folders via Dataiku APIs.
- For GOLD export issues, distinguish between: (1) DuckDB table creation, (2) unload/export execution, and (3) managed-folder visibility. Verify blob existence using dataiku.Folder(...).list_paths_in_partition().

---

# Frontend Build & Sync Workflow

Do not hand-edit minified or generated assets under resource/pulse-dashboard/build/. 

When modifying the external React frontend source at /home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse/webapps/entry_point/frontend/, you must automatically rebuild and sync the compiled asset directory before handing work back:

```bash
bash /home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse/webapps/entry_point/scripts/build_frontend.sh
bash /home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse/scripts/webapp/sync_pulse_dashboard_build.sh /home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse/webapps/entry_point/frontend/build
```
- Keep webapps/pulse-dashboard/ in the plugin repo dedicated to backend/wrapper changes only.
- Do not leave frontend source edits applied without syncing the compiled build during the same task.
- Ignore or remove stale duplicate packaged builds under dataiku-pulse.extras/resource/.

---

# Investigation & Code Reuse

Before modifying code:
- Read the entire function, its callers, and its callees.
- Search for existing helpers, constants, utilities, SQL builders, or normalization functions.
- Understand the execution path. Do not modify the first suspicious file.

Prefer reuse over duplication.

---

# Preservation & Change Discipline

- Make the smallest possible change. No unrelated refactoring.
- Avoid rewriting modules, deleting code, renaming APIs, or introducing unnecessary abstractions.
- Preserve backward compatibility. 
- No dependency upgrades or formatting-only commits unless explicitly requested.

---

# Code Style & Engineering Guidelines

### Python Standards
- Runtime: Python 3.10+
- Typing: Prefer from __future__ import annotations in new/edited modules. Use X | None unions and built-in generics (list[str], dict[str, Any]). Add type hints for new public functions and non-trivial locals.
- Conventions: pathlib.Path, snake_case variables/functions, PascalCase classes, UPPER_SNAKE_CASE constants.
- Pandas: Avoid mutating inputs; start operations with df.copy(). Use pd.to_datetime(..., utc=True, errors="coerce") for cursor timestamps.

### Strict Import Ordering
1. from __future__ import annotations
2. Standard library imports
3. Third-party dependencies (pandas, duckdb, flask, ...)
4. Dataiku SDK (import dataiku, from dataikuapi...)
5. Local packages (from data_collection...)
*Avoid unused imports. Lazy-import heavier Dataiku modules at integration boundaries.*

### Logging & Error Handling
- Never hide bugs or swallow exceptions. No broad except Exception in library code; use specific exceptions (ValueError, RuntimeError) with clear domain context (project key, folder ID).
- Only catch broad exceptions at application integration boundaries:
  - Flask endpoints (webapps/pulse-dashboard/backend.py)
  - DSS runnables (python-runnables/)
  - DSS recipes (custom-recipes/)
- Initialize logger: logger = logging.getLogger(__name__). Always use logger.exception("...") inside except blocks to preserve stack traces.

### SQL Guidelines
- Keep SQL inside DuckDB whenever practical. Do not move SQL into Python unless requested.
- Keep queries readable (multi-line strings + .strip()).
- Avoid exposing arbitrary SQL execution to untrusted inputs in web endpoints; validate/whitelist.
- For audit/object-activity modeling, do not derive object identifiers by parsing `callPath` or `callpath`. Templated paths do not provide trustworthy object identity; prefer explicit native audit fields and drop low-context rows when those fields are missing.

---

# Tooling & Verification

Run the narrowest validation possible first, moving to broader checks only when appropriate. Tooling can optionally be installed via python -m pip install -r code-env/python/spec/requirements.txt.

### Linting & Formatting
```bash
ruff check path/to/file.py
black --check path/to/file.py
mypy path/to/file.py
```
*Broader validation:* ruff check ., black --check ., mypy .

### Testing Suite
*Note: The repo currently has no conventional tests/ suite. If/when tests are added, use:*
```bash
pytest path/to/test.py
pytest path/to/test_file.py -k test_name_substring
```

### Dashboard Backend Dev Execution
Outside DSS, execution requires python-lib/ on PYTHONPATH and access to the dataiku environment:
```bash
bash scripts/webapp/run_backend.sh
```

If verification fails: Stop. Explain. Do not continue stacking fixes.

---

# Git Workflow & Safety Rules

### Git Safety Regulations
- Never modify or discard pre-existing user changes. Never stage unrelated files.
- Never commit generated files, secrets, credentials, tokens, environment files, or private keys.
- Do not amend an existing commit, rebase, merge, pull, or push unless explicitly requested.
- Keep each commit limited to one logical concern. Review git diff before and after every substantial edit.
- If unrelated changes are discovered, stop and report them rather than reverting them.

### Recommended Sequence
1. Inspect Baseline:
   ```bash
   git status --short
   git branch --show-current
   git diff
   ```
2. Branch Management:
   ```bash
   git switch -c fix/<short-issue-name>
   ```
3. Staging & Commit (Iterative):
   ```bash
   git diff -- path/to/file
   git add -p
   git status
   git commit -m "Fix <specific issue>"
   ```
4. Final Branch Review:
   ```bash
   git diff main...HEAD
   git diff --name-status main...HEAD
   git log --oneline main..HEAD
   ```

### Strictly Forbidden Without Explicit Approval:
```bash
git reset --hard
git clean -fdx
git checkout -- .
git branch -D <branch-name>
git push --force
git push --force-with-lease
```

Because we are in a testing/development environment all final tests needs to be performed through Dataiku actual plugin. This requires the code to be added, committed, and pushed to the current working development branch, never the main or verion branch (V2, V3, V4, etc). After the fix, perform:

```bash
# Example, not exact code (use above git logic for better reference)
git add
git commit
git push
```

Git is wrapped by audit and quality checks. If a problem is found, display the issue, then ask to resolve.

---

# Completion Checklist

Before finishing ask yourself:
- Did I reproduce the issue and identify the true root cause?
- Is this the smallest fix possible without structural drift?
- Did I modify or rebuild the frontend external source and sync it appropriately?
- Did I review the final branch git diff relative to the base branch?
- Is git status perfectly clean and verified?

Do not report success until every applicable item has been completed.

---

# Completion Report

Always include:
* Root cause
* Files changed
* Why each change was necessary
* Validation commands run and results
* Remaining risks / anything not verified
