# Follow-up: Activity + Usage metrics for Build Catalog / Products

## Context
We updated the Build/Catalog pages and are currently seeing `activity_30d = 0` widely. The UI’s Build → Catalog and Build → Products pages use DuckDB views that aggregate object-level events derived from DSS audit logs.

Goal: define and implement a robust “Usage” model that answers **who / what / when / where / why / how** products and assets are being used (including volume), and compute a trustworthy **last activity** signal.

## Current implementation (today)

### Where “activity” comes from
- **Asset activity**: `python-lib/pulse_dashboard/pulse_duckdb/datasets/views/asset_activity_30d.yaml`
  - `activity_30d`: `COUNT(*) FILTER (WHERE timestamp >= now() - INTERVAL 30 DAY)`
  - `active_users_30d`: `COUNT(DISTINCT login) FILTER (WHERE timestamp >= now() - INTERVAL 30 DAY)`
  - `last_activity_at`: `MAX(timestamp)`
- Joined into the catalog view:
  - `python-lib/pulse_dashboard/pulse_duckdb/datasets/views/final_build_catalog.yaml`
  - `activity_30d` and `active_users_30d` are `COALESCE(..., 0)` after a LEFT JOIN.
- **Product activity**: `python-lib/pulse_dashboard/pulse_duckdb/datasets/views/product_activity_30d.yaml` → joined by `final_build_products_catalog.yaml`.

### Where events come from
- Object activity aggregates `v_object_activity_events`:
  - `python-lib/pulse_dashboard/pulse_duckdb/datasets/views/v_object_activity_events.yaml`
  - which points to `base_object_activity_events` → `fact_object_activity_events` (compat view ensured by `python-lib/pulse_dashboard/pulse_duckdb/engine/init_db.py`).
- `fact_object_activity_events` is produced in the GOLD build:
  - `custom-recipes/create-gold-tables/recipe.py` (`_build_fact_object_activity_events`)
  - It reads SILVER parquet under:
    - `silver/category=event_mapping/module=<module>/instance_name=*/year=*/month=*/day=*/*.parquet`

### Audit-log ingestion
- Audit logs are ingested by:
  - `python-runnables/data-gather-audit-logs/runnable.py`
- A processor maps raw audit events into `dataiku_category` (used for partitioning):
  - `python-lib/data_collection/audit_logs_modules/event_mapping.py`
  - Mapping file: `python-lib/data_collection/audit_logs_modules/mapping.csv`

## Why we might be seeing “all 0s”
These are the most likely failure modes, in order of likelihood:

1) **Audit processor drops relevant execution events**
   - `event_mapping.py` filters to `topic == "generic"`.
   - If scenario / job / flow execution events are not `topic=generic`, they never land in SILVER.

2) **We only include a curated subset of `event_mapping` modules**
   - `fact_object_activity_events` is built from a curated module list:
     - `python-lib/data_collection/pulse_duckdb/gold_specs/object_activity/toolbox.yaml`
   - Current list:
     - `datasets`, `visual_recipes`, `apis`, `webapps`, `charts_dashboard`, `application_designer`
   - If “usage” is reported under categories like `flow`, `job`, `scenarios`, etc., those events are ignored.

3) **Object identifier extraction fails (NULL `object_key`)**
   - The GOLD builder derives object ids via regexes over `callpath` (see `_object_activity_branch_sql` in `custom-recipes/create-gold-tables/recipe.py`).
   - If the relevant audit events don’t contain a matching `callpath`, the resulting `object_key` becomes NULL.
   - Downstream rollups require `object_key IS NOT NULL`, so those events won’t count.

4) **GOLD unload config / loading gap**
   - `fact_object_activity_events` is unloaded as partitioned parquet and expects `unload_behavior="duckdb"`.
   - If GOLD is built/unloaded without those partitions, the dashboard will load empty activity facts.

## Proposed definition: “Activity” and “Usage”

### Activity (high-level, for catalog sorting)
- **Last activity**: last time the object was “used” according to audit events.
  - `last_activity_at = MAX(timestamp)` over all events for the object.
- **Days since last activity**:
  - `days_since_last_activity = date_diff('day', CAST(last_activity_at AS DATE), current_date)`
- **Hotness windows**:
  - `events_1d`, `events_7d`, `events_30d`
  - `unique_users_1d`, `unique_users_7d`, `unique_users_30d`

### Usage (detail tab / drilldown)
A “Usage” tab should answer:
- **How much**: event volume (total and windowed), ideally split by event type or capability.
- **Who**: top users by event count + last seen.
- **Where**: instance_name and project_key (already in the event fact).
- **When**: timeline (daily counts) and last seen.
- **What**: object_type/object_key/object_name.
- **How/Why**: categorize by capability or event_category where possible.

Notes:
- For high-volume product types (e.g. API endpoints), we should decide if “usage” is UI usage or true call volume. Audit logs may not capture all runtime API call volume (depends on DSS config).

## Questions to resolve (before implementation)

### Semantics
1) What is the authoritative meaning of “used” for each product type?
   - `api_endpoint`: UI page visits? endpoint calls? both?
   - `web_application`: opening the app? calling its backend? both?
   - `dashboard`: dashboard view loads? tile renders? exports?
   - `agent` / `dataiku_application`: what audit events represent usage?

2) For scenarios and flow rebuilds:
   - Do we attribute usage to:
     - the **scenario object** itself,
     - the **recipes/datasets executed**,
     - or both?

3) Do we care about non-UI actions (scheduled/background) as “usage”?
   - `users.py` currently filters to `message_authSource == USER_FROM_UI`.
   - For product usage, we may want the opposite: include job/scenario activity.

### Coverage
4) Which audit `topic` values contain execution events on your DSS?
   - If it’s not just `generic`, we need to broaden ingestion.

5) Which `dataiku_category` values correspond to the events we need?
   - Update `gold_specs/object_activity/toolbox.yaml` accordingly.

### Data modeling
6) Do we want separate metrics for:
   - **viewing** (read/list/open)
   - **executing** (run/build/refresh)
   - **editing** (modify/configure)

7) Should “last activity” be based on:
   - all events, or
   - a filtered set (e.g. exclude admin/listing/metadata-only)?

## Next steps (suggested)
1) Collect a small sample of real audit rows for:
   - a scenario run
   - a full flow rebuild
   - a webapp open
   - an API endpoint call (if applicable)
2) Identify:
   - the `topic` values used
   - the `msgType` / `msgTypeBase`
   - the `callpath` shapes
   - where object ids appear (callpath vs extras)
3) Adjust ingestion and GOLD build:
   - relax/parameterize `topic == generic` filtering
   - expand `object_activity/toolbox.yaml`
   - improve object_key extraction (fallbacks)
4) Add new DuckDB views/tables for usage rollups:
   - per-object windows + last activity
   - top users per object
   - daily trend per object

## Acceptance criteria (for the future “Usage” page)
- Catalog shows non-zero activity for known-used objects.
- Last activity updates correctly (e.g. an object used today shows 0 days).
- Usage tab shows:
  - counts in multiple windows
  - top users
  - a basic timeline
- Coverage includes scenario-driven executions where appropriate (per agreed semantics).
