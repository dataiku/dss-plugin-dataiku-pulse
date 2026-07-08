# Pulse v3 Release Notes

Pulse v3 brings a more complete, more polished view of the Dataiku estate through improvements across collection, data preparation, curated modeling, and the packaged dashboard experience.

These notes summarize the major themes of the v3 work at a high level for public-facing communication.

## Version Highlights

### 3.0.18

- Hardened the packaged dashboard startup path so the webapp backend binds more reliably before DuckDB warmup begins, reducing cases where the UI appears before analytics are fully ready.
- Improved DuckDB connection handling inside the dashboard process to avoid mixed read/write connection conflicts and better tolerate stale or invalid shared handles.
- Strengthened the dashboard's read-only SQL protections so the query path rejects write-style statements more explicitly.
- Added credential-refresh support for long-running GOLD builds so cloud-storage backed DuckDB reads are more resilient when temporary credentials expire mid-run.
- Improved GOLD build/runtime behavior by rendering specs at execution time, surfacing skipped tables more clearly, and handling skipped user-activity views more gracefully.
- Added scenario failure notification configuration plus follow-up fixes so dashboard refresh scenarios keep their expected triggers while attaching Pulse-managed failure reporters.
- Added a focused local regression test harness covering dashboard connection reuse, read-only query safety, credential refresh, and notification reporter setup.

### 3.0.17

- Added worker node classification support so each worker can be initialized as either `Designer` or `Automation`.
- Added Automation-node worker bootstrap using the packaged skeleton bundle `resource/dataiku_pulse_worker_bundle_skeleton_v1.zip` so Pulse can create an activation-ready worker project before building datasets, variables, and scenarios.
- Refined automation worker bundle activation and setup flow so imported bundles are used more consistently when preparing automation workers.
- Updated worker initialization so missing `projects_delta` and `audit_log_delta` cursors start **3 calendar months back** instead of only from the current run time.
- Aligned project and audit-log collection fallback cursor behavior with the same 3-month default window when worker variables are missing.
- Updated installation and runnable documentation to reflect Automation worker setup and the revised cursor defaults.

### 3.0.16

- Added broader Automation-worker setup support and documentation so Pulse can be deployed more flexibly across Designer and Automation estates.
- Improved bundled/offline DuckDB extension behavior and extension error reporting so storage-backed builds work more reliably in restricted environments.
- Hardened managed-folder upload handling and folder access reuse to make collection and build flows more reliable under real DSS execution patterns.
- Improved dashboard startup, refresh, and recovery behavior for a smoother packaged webapp experience.
- Expanded collection, normalization, and taxonomy coverage across metadata and activity inputs to improve the quality and continuity of downstream analytics.
- Improved curated GOLD-layer coverage and dashboard readability across products, assets, activity, summary, and license-oriented views.
- Strengthened resilience for mixed, incomplete, or partially missing source inputs so Pulse remains more usable across real-world deployment environments.

## Data Collection

- Expanded coverage across more platform activity and metadata so Pulse can represent a broader view of how teams build and operate in Dataiku.
- Improved historical event capture to bring older activity into the same analytical flow as current activity.
- Strengthened ingestion for more deployment patterns, including environments that require more offline-friendly operation.
- Improved collection resilience so Pulse can continue handling changing source structures and optional fields more gracefully.

## Data Cleanse

- Improved normalization of incoming metadata and audit records so similar events are represented more consistently across sources.
- Reduced the impact of schema drift and uneven source data, helping Pulse produce cleaner and more reliable outputs.
- Standardized field handling across several activity and audit inputs to improve continuity in downstream reporting.
- Tightened quality around identity, ownership, and event labeling so the analytics are easier to interpret.

## Data Smooth

- Improved continuity between historical and current activity so long-running trends are easier to follow.
- Refined event mapping and activity labeling to produce a more intuitive readout of platform behavior.
- Smoothed handling of partial, missing, or delayed records so dashboards remain usable in real-world conditions.
- Improved startup and refresh robustness to reduce friction when the analytics layer is initializing or recovering.

## Gold Layer

- Expanded the curated analytics layer with richer product, asset, activity, and license-oriented views.
- Added broader support for product and asset taxonomy so Pulse can describe the estate in more business-friendly categories.
- Improved snapshot-style views that help teams understand the current state of usage, ownership, and exposure.
- Strengthened model-building reliability so the gold layer remains usable even when some source inputs are incomplete.

## Dashboard

### Platform Experience

- The Pulse dashboard is now delivered as a more complete packaged web application experience inside the plugin flow.
- Improved load, startup, and recovery behavior for a smoother day-to-day user experience.
- Refined wording, summaries, and presentation choices to make the dashboard more approachable for business and platform audiences.
- Continued visual polish to keep the experience cleaner and easier to scan.

### Pages

#### Overview and Summary

- Sharper top-level summaries make it easier to understand platform health and usage at a glance.
- Improved license-oriented views help highlight adoption patterns and potential areas of attention.
- Better handling of incomplete source data keeps summary views more dependable.

#### Products and Assets

- Expanded product and asset coverage gives teams a fuller picture of what is being built and delivered.
- Improved taxonomy makes the inventory easier to browse in business-relevant groupings.
- Refined labels and ownership signals make the content easier to interpret and act on.

#### Activity and Usage

- Stronger activity views bring together more user and platform behavior into a unified narrative.
- Historical activity is better represented, supporting broader trend analysis over time.
- Cleaner event naming and categorization improve readability for non-technical audiences.

#### Build and Capability Views

- Broader capability coverage helps connect technical objects to the kinds of work teams are doing.
- Build-oriented views now better reflect the structure of projects, assets, and delivered products.
- Documentation and taxonomy alignment make the dashboard easier to understand across audiences.

## Overall

Pulse v3 is centered on one outcome: giving organizations a clearer, more dependable, and more business-readable understanding of their Dataiku landscape. The release improves the quality of the upstream data flow, enriches the curated analytics model, and delivers a stronger packaged dashboard experience for exploring that story.
